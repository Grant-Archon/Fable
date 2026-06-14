#!/usr/bin/env python3
"""Two-debater + synthesizer harness.

Two Opus 4.8 agents debate a question (an opening round plus optional rebuttal
rounds where each sees the other's latest argument), then a synthesizer model
reads both full transcripts and produces the single best answer.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python orchestrate.py "Should a startup use a monorepo?"
    python orchestrate.py --rounds 2 --stances "For;Against" "Is nuclear the right bet for grid decarbonization?"
    echo "Long question..." | python orchestrate.py --rounds 1 --json out.json

Notes:
- The "Expanse" synthesizer persona from CLAUDE-EXPANSE.md is a chat-product
  prompt; this harness uses a slim synthesizer prompt distilled from its useful
  sections instead. "Expanse" (claude-fable-5) is not assumed to be callable —
  the synthesizer defaults to the debater model. Override with --synth-model if
  you have access to a higher tier.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from anthropic import Anthropic
except ImportError:
    sys.exit("The 'anthropic' package is required. Install it with: pip install -r requirements.txt")

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
DEFAULT_DEBATER_MODEL = "claude-opus-4-8"
# Default the synthesizer to the same tier; set to claude-fable-5 if you have access.
DEFAULT_SYNTH_MODEL = "claude-opus-4-8"


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8").strip()


def complete(client: Anthropic, *, model: str, system: str, messages: list[dict],
             max_tokens: int, temperature: float) -> str:
    """Single non-streaming Messages call returning concatenated text."""
    resp = client.messages.create(
        model=model,
        system=system,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return "".join(block.text for block in resp.content if block.type == "text").strip()


def run_debate(client: Anthropic, question: str, *, rounds: int, stances: list[str] | None,
               debater_model: str, synth_model: str, max_tokens: int,
               temperature: float) -> dict:
    debater_system = load_prompt("debater_system.md")
    synth_system = load_prompt("synthesizer_system.md")

    labels = ["Debater A", "Debater B"]
    # Per-debater transcript: list of (round_label, text)
    transcripts: list[list[tuple[str, str]]] = [[], []]

    def opening_prompt(i: int) -> str:
        base = f"Question:\n{question}\n\n"
        if stances and stances[i].strip():
            base += (f"Argue the following position as strongly and honestly as you can: "
                     f"{stances[i].strip()}\n\n")
        base += "Give your opening argument."
        return base

    # --- Opening round (debaters answer independently) ---
    for i in range(2):
        text = complete(
            client, model=debater_model, system=debater_system,
            messages=[{"role": "user", "content": opening_prompt(i)}],
            max_tokens=max_tokens, temperature=temperature,
        )
        transcripts[i].append(("Opening", text))
        print(f"\n{'='*70}\n{labels[i]} — Opening\n{'='*70}\n{text}", file=sys.stderr)

    # --- Rebuttal rounds (each sees the other's latest argument) ---
    for r in range(1, rounds + 1):
        new_texts = [None, None]
        for i in range(2):
            other = 1 - i
            other_latest = transcripts[other][-1][1]
            # Reconstruct this debater's own thread so it builds on its prior turns.
            messages: list[dict] = [{"role": "user", "content": opening_prompt(i)}]
            for round_label, text in transcripts[i]:
                messages.append({"role": "assistant", "content": text})
                if round_label == transcripts[i][-1][0]:
                    break
            messages.append({
                "role": "user",
                "content": (f"Here is the other debater's most recent argument:\n\n"
                            f"<other_debater>\n{other_latest}\n</other_debater>\n\n"
                            f"Engage with its strongest form: concede what is correct, rebut "
                            f"what is wrong and say why, and refine your own position."),
            })
            new_texts[i] = complete(
                client, model=debater_model, system=debater_system,
                messages=messages, max_tokens=max_tokens, temperature=temperature,
            )
        for i in range(2):
            transcripts[i].append((f"Rebuttal {r}", new_texts[i]))
            print(f"\n{'='*70}\n{labels[i]} — Rebuttal {r}\n{'='*70}\n{new_texts[i]}",
                  file=sys.stderr)

    # --- Synthesis ---
    def format_transcript(i: int) -> str:
        return "\n\n".join(f"[{labels[i]} — {rl}]\n{text}" for rl, text in transcripts[i])

    synth_user = (
        f"Question:\n{question}\n\n"
        f"Below are the full transcripts of two independent debaters. Synthesize the "
        f"single best answer for the user.\n\n"
        f"<debater_a_transcript>\n{format_transcript(0)}\n</debater_a_transcript>\n\n"
        f"<debater_b_transcript>\n{format_transcript(1)}\n</debater_b_transcript>"
    )
    final = complete(
        client, model=synth_model, system=synth_system,
        messages=[{"role": "user", "content": synth_user}],
        max_tokens=max_tokens, temperature=temperature,
    )
    print(f"\n{'='*70}\nSYNTHESIZED ANSWER\n{'='*70}\n{final}", file=sys.stderr)

    return {
        "question": question,
        "config": {"rounds": rounds, "stances": stances,
                   "debater_model": debater_model, "synth_model": synth_model},
        "transcripts": {labels[i]: [{"round": rl, "text": t} for rl, t in transcripts[i]]
                        for i in range(2)},
        "final_answer": final,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Two-debater + synthesizer harness.")
    p.add_argument("question", nargs="?", help="The question to debate. If omitted, read from stdin.")
    p.add_argument("--rounds", type=int, default=1,
                   help="Number of rebuttal rounds after the opening (default: 1).")
    p.add_argument("--stances", default=None,
                   help="Optional semicolon-separated stances for A and B, e.g. \"For;Against\".")
    p.add_argument("--debater-model", default=DEFAULT_DEBATER_MODEL)
    p.add_argument("--synth-model", default=DEFAULT_SYNTH_MODEL)
    p.add_argument("--max-tokens", type=int, default=2048)
    p.add_argument("--temperature", type=float, default=1.0)
    p.add_argument("--json", dest="json_out", default=None,
                   help="Write the full run (transcripts + answer) to this JSON file.")
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
        parts = [s for s in args.stances.split(";")]
        if len(parts) != 2:
            sys.exit("--stances must contain exactly two stances separated by ';'.")
        stances = parts

    client = Anthropic()
    result = run_debate(
        client, question, rounds=args.rounds, stances=stances,
        debater_model=args.debater_model, synth_model=args.synth_model,
        max_tokens=args.max_tokens, temperature=args.temperature,
    )

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\n[wrote full run to {args.json_out}]", file=sys.stderr)

    # The final answer goes to stdout (transcripts go to stderr) so it can be piped.
    print(result["final_answer"])


if __name__ == "__main__":
    main()
