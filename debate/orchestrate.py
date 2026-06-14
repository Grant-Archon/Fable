#!/usr/bin/env python3
"""Two-debater + synthesizer harness (optimized).

Two debaters (Opus 4.8 by default) argue the same question in parallel — an
opening round plus optional rebuttal rounds where each sees the other's latest
argument — then a synthesizer reads both full transcripts and produces the
single best answer. An optional cheap referee scores whether the synthesis
actually resolved the disagreements, and can trigger one revision pass.

Optimizations vs. the naive version:
- The two debaters in each round run CONCURRENTLY (threads), ~halving per-round latency.
- System prompts are sent with prompt caching (cache_control) to cut input cost
  across the opening + rebuttal rounds.
- Calls STREAM internally (avoids long-request timeouts) and RETRY transient
  errors (429/500/503/529/connection) with exponential backoff.
- Debaters can run on DIFFERENT models for genuine reasoning diversity.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python orchestrate.py "Should a 5-person startup use a monorepo?"
    python orchestrate.py --rounds 2 --stances "For;Against" \
        --debater-models "claude-opus-4-8,claude-sonnet-4-6" \
        --revise "Is nuclear the right bet for grid decarbonization?"

Note: in CLAUDE-EXPANSE.md "Claude Expanse" = claude-fable-5, a fictional
Mythos-class model, not assumed callable here. The synthesizer defaults to the
first debater model; point --synth-model at a higher tier if you have access.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from anthropic import (
        Anthropic,
        APIConnectionError,
        APIStatusError,
        RateLimitError,
    )
except ImportError:
    sys.exit("The 'anthropic' package is required. Install it with: pip install -r requirements.txt")

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
DEFAULT_DEBATER_MODEL = "claude-opus-4-8"
# Synthesizer defaults to the first debater model unless --synth-model is given.
DEFAULT_REFEREE_MODEL = "claude-haiku-4-5-20251001"
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 529}


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8").strip()


def complete(client: Anthropic, *, model: str, system: str, messages: list[dict],
             max_tokens: int, temperature: float, max_retries: int = 4) -> str:
    """One Messages call: cached system prompt, streamed body, retried on transient errors."""
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    attempt = 0
    while True:
        try:
            parts: list[str] = []
            with client.messages.stream(
                model=model,
                system=system_blocks,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            ) as stream:
                for chunk in stream.text_stream:
                    parts.append(chunk)
            return "".join(parts).strip()
        except (RateLimitError, APIConnectionError, APIStatusError) as err:
            status = getattr(err, "status_code", None)
            retryable = isinstance(err, (RateLimitError, APIConnectionError)) or status in RETRYABLE_STATUS
            attempt += 1
            if not retryable or attempt > max_retries:
                raise
            backoff = 2 ** attempt
            print(f"[transient error ({status or type(err).__name__}); retry "
                  f"{attempt}/{max_retries} in {backoff}s]", file=sys.stderr)
            time.sleep(backoff)


def banner(title: str, body: str) -> None:
    print(f"\n{'='*70}\n{title}\n{'='*70}\n{body}", file=sys.stderr)


def extract_json(text: str) -> dict | None:
    """Best-effort: parse the first balanced {...} object out of a model response."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def run_debate(client: Anthropic, question: str, *, rounds: int, stances: list[str] | None,
               debater_models: list[str], synth_model: str, max_tokens: int, temperature: float,
               referee: bool, referee_model: str, revise: bool, revise_threshold: int) -> dict:
    debater_system = load_prompt("debater_system.md")
    synth_system = load_prompt("synthesizer_system.md")

    labels = ["Debater A", "Debater B"]
    threads: list[list[dict]] = [[], []]            # per-debater API message history
    rounds_text: list[list[tuple[str, str]]] = [[], []]  # per-debater (round_label, text)
    timings: dict[str, float] = {}

    def opening_prompt(i: int) -> str:
        base = f"Question:\n{question}\n\n"
        if stances and stances[i].strip():
            base += (f"Argue the following position as strongly and honestly as you can: "
                     f"{stances[i].strip()}\n\n")
        return base + "Give your opening argument."

    def debater_turn(i: int) -> str:
        return complete(client, model=debater_models[i], system=debater_system,
                        messages=threads[i], max_tokens=max_tokens, temperature=temperature)

    # Run both debaters of a round concurrently, then record + print in order.
    def run_round(label: str) -> None:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=2) as pool:
            texts = list(pool.map(debater_turn, range(2)))
        timings[label] = time.time() - t0
        for i in range(2):
            threads[i].append({"role": "assistant", "content": texts[i]})
            rounds_text[i].append((label, texts[i]))
            banner(f"{labels[i]} — {label}  [{debater_models[i]}]", texts[i])

    # Opening round.
    for i in range(2):
        threads[i].append({"role": "user", "content": opening_prompt(i)})
    run_round("Opening")

    # Rebuttal rounds: each debater sees the other's latest argument.
    for r in range(1, rounds + 1):
        for i in range(2):
            other_latest = rounds_text[1 - i][-1][1]
            threads[i].append({
                "role": "user",
                "content": (f"Here is the other debater's most recent argument:\n\n"
                            f"<other_debater>\n{other_latest}\n</other_debater>\n\n"
                            f"Engage with its strongest form: concede what is correct, rebut "
                            f"what is wrong and say why, and refine your own position."),
            })
        run_round(f"Rebuttal {r}")

    def format_transcript(i: int) -> str:
        return "\n\n".join(f"[{labels[i]} — {rl}]\n{text}" for rl, text in rounds_text[i])

    transcripts_block = (
        f"<debater_a_transcript>\n{format_transcript(0)}\n</debater_a_transcript>\n\n"
        f"<debater_b_transcript>\n{format_transcript(1)}\n</debater_b_transcript>"
    )

    # Synthesis.
    def synthesize(extra: str = "") -> str:
        user = (f"Question:\n{question}\n\nBelow are the full transcripts of two independent "
                f"debaters. Synthesize the single best answer for the user.\n\n{transcripts_block}"
                + extra)
        return complete(client, model=synth_model, system=synth_system,
                        messages=[{"role": "user", "content": user}],
                        max_tokens=max_tokens, temperature=temperature)

    t0 = time.time()
    final = synthesize()
    timings["Synthesis"] = time.time() - t0
    banner(f"SYNTHESIZED ANSWER  [{synth_model}]", final)

    # Referee + optional one-shot revision.
    referee_report = None
    if referee:
        ref_system = load_prompt("referee_system.md")
        ref_user = (f"Question:\n{question}\n\n{transcripts_block}\n\n"
                    f"<synthesized_answer>\n{final}\n</synthesized_answer>")
        t0 = time.time()
        raw = complete(client, model=referee_model, system=ref_system,
                       messages=[{"role": "user", "content": ref_user}],
                       max_tokens=1024, temperature=0.0)
        timings["Referee"] = time.time() - t0
        referee_report = extract_json(raw) or {"score": None, "raw": raw}
        banner(f"REFEREE  [{referee_model}]", json.dumps(referee_report, indent=2))

        score = referee_report.get("score")
        if revise and isinstance(score, int) and score < revise_threshold:
            critique = referee_report.get("critique", "")
            unresolved = referee_report.get("unresolved", [])
            extra = ("\n\nA referee judged a prior synthesis attempt below bar "
                     f"(score {score}/{revise_threshold}). Its critique: {critique}\n"
                     f"Unresolved points: {unresolved}\n"
                     "Produce an improved final answer that addresses this.")
            t0 = time.time()
            final = synthesize(extra)
            timings["Revision"] = time.time() - t0
            referee_report["revised"] = True
            banner(f"REVISED ANSWER  [{synth_model}]", final)

    banner("TIMINGS (s)", "\n".join(f"{k}: {v:.1f}" for k, v in timings.items())
           + f"\ntotal: {sum(timings.values()):.1f}")

    return {
        "question": question,
        "config": {"rounds": rounds, "stances": stances, "debater_models": debater_models,
                   "synth_model": synth_model, "referee_model": referee_model if referee else None,
                   "revise": revise, "revise_threshold": revise_threshold},
        "transcripts": {labels[i]: [{"round": rl, "text": t} for rl, t in rounds_text[i]]
                        for i in range(2)},
        "referee": referee_report,
        "timings": timings,
        "final_answer": final,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Two-debater + synthesizer harness (optimized).")
    p.add_argument("question", nargs="?", help="The question to debate. If omitted, read from stdin.")
    p.add_argument("--rounds", type=int, default=1,
                   help="Rebuttal rounds after the opening (default: 1; 0 = independent, no cross-talk).")
    p.add_argument("--stances", default=None,
                   help='Optional semicolon-separated stances for A and B, e.g. "For;Against".')
    p.add_argument("--debater-models", default=DEFAULT_DEBATER_MODEL,
                   help='Comma-separated model(s). One value = both debaters; two = one each. '
                        'Default: claude-opus-4-8.')
    p.add_argument("--synth-model", default=None,
                   help="Synthesizer model (default: first debater model).")
    p.add_argument("--referee", action=argparse.BooleanOptionalAction, default=True,
                   help="Run a cheap referee to score the synthesis (default: on; --no-referee to skip).")
    p.add_argument("--referee-model", default=DEFAULT_REFEREE_MODEL)
    p.add_argument("--revise", action="store_true",
                   help="If the referee scores below the threshold, do one synthesis revision pass.")
    p.add_argument("--revise-threshold", type=int, default=7,
                   help="Referee score below which --revise triggers a rewrite (default: 7).")
    p.add_argument("--max-tokens", type=int, default=2048)
    p.add_argument("--temperature", type=float, default=1.0)
    p.add_argument("--json", dest="json_out", default=None,
                   help="Write the full run (config + transcripts + referee + answer) to this JSON file.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    question = args.question or sys.stdin.read().strip()
    if not question:
        sys.exit("No question provided (pass as an argument or via stdin).")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set in the environment.")

    stances = None
    if args.stances:
        parts = args.stances.split(";")
        if len(parts) != 2:
            sys.exit("--stances must contain exactly two stances separated by ';'.")
        stances = parts

    models = [m.strip() for m in args.debater_models.split(",") if m.strip()]
    if len(models) == 1:
        models = models * 2
    elif len(models) != 2:
        sys.exit("--debater-models must be one or two comma-separated model names.")
    synth_model = args.synth_model or models[0]

    client = Anthropic()
    result = run_debate(
        client, question, rounds=args.rounds, stances=stances, debater_models=models,
        synth_model=synth_model, max_tokens=args.max_tokens, temperature=args.temperature,
        referee=args.referee, referee_model=args.referee_model, revise=args.revise,
        revise_threshold=args.revise_threshold,
    )

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\n[wrote full run to {args.json_out}]", file=sys.stderr)

    # Final answer to stdout (transcripts/diagnostics go to stderr) so it can be piped.
    print(result["final_answer"])


if __name__ == "__main__":
    main()
