#!/usr/bin/env python3
"""Fusion: fan a question out to a panel of models, judge, then synthesize.

A heterogeneous panel of models each answers the same question in parallel, each
with web search and code/bash server tools enabled. A judge model reads every
answer and extracts the structure (consensus, contradictions, partial coverage,
unique insights, blind spots). A synthesizer then writes the final answer
grounded in that analysis. (Inspired by the OpenRouter Fusion pattern.)

This is the programmatic/API version; the interactive Claude Code version is
`/fusion` (see ../.claude/README-fusion.md).

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python fusion.py "Is nuclear the right bet for grid decarbonization?"
    python fusion.py --panel "claude-opus-4-8,claude-sonnet-4-6,claude-haiku-4-5" \
        --synth-model claude-opus-4-8 --json run.json "your question"

Notes:
- Sampling params (temperature/top_p) are NOT sent: they 400 on Opus 4.7 and later.
- Server tools used: web_search_20260209 and code_execution_20260120 (both GA, no
  beta header). Code execution runs in Anthropic's sandbox (no internet); web
  search is how panelists reach the live web. Server tools may pause with
  stop_reason "pause_turn" after ~10 internal steps — we resume automatically.
"""

from __future__ import annotations

import argparse
import json
import os
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

# Tiers route work by task level. The flagship is Opus + Opus (diversity comes
# from independent tool paths and sampling, not from mixing in weaker models);
# lower tiers use cheaper models for lower-level / simpler questions.
TIERS = {
    "quick":    {"panel": ["claude-sonnet-4-6", "claude-haiku-4-5"],
                 "judge": "claude-haiku-4-5",  "synth": "claude-sonnet-4-6"},
    "standard": {"panel": ["claude-opus-4-8", "claude-opus-4-8"],
                 "judge": "claude-opus-4-8",   "synth": "claude-opus-4-8"},
    "deep":     {"panel": ["claude-opus-4-8", "claude-opus-4-8", "claude-opus-4-8"],
                 "judge": "claude-opus-4-8",   "synth": "claude-opus-4-8"},
}
DEFAULT_TIER = "standard"
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 529}
# GA server tools (no beta header). Code execution = sandboxed bash/python.
PANEL_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "code_execution_20260120", "name": "code_execution"},
]
MAX_CONTINUATIONS = 6  # safety cap on pause_turn resumes per call


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8").strip()


def _text_of(content: list) -> str:
    return "".join(b.text for b in content if getattr(b, "type", None) == "text").strip()


def complete(client: Anthropic, *, model: str, system: str, user: str,
             tools: list | None = None, max_tokens: int = 4096, max_retries: int = 4) -> str:
    """One logical turn: cached system prompt, transient-error retry, and
    automatic resume on server-tool `pause_turn`. No sampling params (they 400
    on Opus 4.7 and later)."""
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": user}]
    continuations = 0

    while True:
        attempt = 0
        while True:
            try:
                kwargs = dict(model=model, system=system_blocks, messages=messages,
                              max_tokens=max_tokens)
                if tools:
                    kwargs["tools"] = tools
                resp = client.messages.create(**kwargs)
                break
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

        # Server tools (web_search/code_execution) run a server-side loop that can
        # pause after ~10 steps. Resume by echoing the assistant turn and re-sending.
        if resp.stop_reason == "pause_turn" and continuations < MAX_CONTINUATIONS:
            messages.append({"role": "assistant", "content": resp.content})
            continuations += 1
            continue
        return _text_of(resp.content)


def banner(title: str, body: str) -> None:
    print(f"\n{'='*70}\n{title}\n{'='*70}\n{body}", file=sys.stderr)


def run_fusion(client: Anthropic, question: str, *, panel: list[str], judge_model: str,
               synth_model: str, max_tokens: int) -> dict:
    framing_system = load_prompt("framing_system.md")
    panelist_system = load_prompt("panelist_system.md")
    judge_system = load_prompt("judge_system.md")
    synth_system = load_prompt("synthesizer_system.md")
    timings: dict[str, float] = {}

    # --- Phase 0: framing — a shared context brief keeps the panel on one topic ---
    t0 = time.time()
    brief = complete(client, model=synth_model, system=framing_system,
                     user=f"Question:\n{question}", max_tokens=max_tokens)
    timings["framing"] = time.time() - t0
    banner(f"SHARED CONTEXT BRIEF  [{synth_model}]", brief)

    framed_question = (f"Question:\n{question}\n\nShared context brief (all panelists share this "
                       f"frame — stay within it; reason independently on the substance):\n"
                       f"<context_brief>\n{brief}\n</context_brief>")

    # --- Phase 1: panel fan-out (parallel, tool-enabled, all on the same frame) ---
    def ask_panelist(model: str) -> str:
        return complete(client, model=model, system=panelist_system, user=framed_question,
                        tools=PANEL_TOOLS, max_tokens=max_tokens)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, len(panel))) as pool:
        results = list(pool.map(ask_panelist, panel))
    timings["panel"] = time.time() - t0

    answers = []  # (label, model, text)
    for i, (model, text) in enumerate(zip(panel, results)):
        label = f"Panelist {i+1}"
        answers.append((label, model, text))
        banner(f"{label}  [{model}]", text or "(no answer returned)")
    ok = [a for a in answers if a[2]]
    if not ok:
        sys.exit("All panelists failed to return an answer.")

    panel_block = "\n\n".join(
        f"<{label.lower().replace(' ', '_')} model=\"{model}\">\n{text}\n</{label.lower().replace(' ', '_')}>"
        for label, model, text in ok
    )

    # --- Phase 2: judge extracts the structure across the panel ---
    judge_user = (f"Question:\n{question}\n\nShared context brief the panel worked from:\n"
                  f"<context_brief>\n{brief}\n</context_brief>\n\n"
                  f"Here are the panel's answers. Extract the structure across them.\n\n{panel_block}")
    t0 = time.time()
    analysis = complete(client, model=judge_model, system=judge_system, user=judge_user,
                        max_tokens=max_tokens)
    timings["judge"] = time.time() - t0
    banner(f"JUDGE ANALYSIS  [{judge_model}]", analysis)

    # --- Phase 3: synthesizer writes the final answer, grounded in the analysis ---
    synth_user = (f"Question:\n{question}\n\nShared context brief the panel worked from:\n"
                  f"<context_brief>\n{brief}\n</context_brief>\n\n"
                  f"The judge's structural analysis of the panel:\n\n"
                  f"<judge_analysis>\n{analysis}\n</judge_analysis>\n\n"
                  f"The panel's answers:\n\n{panel_block}\n\n"
                  f"Write the single best final answer for the user, grounded in the analysis.")
    t0 = time.time()
    final = complete(client, model=synth_model, system=synth_system, user=synth_user,
                     max_tokens=max_tokens)
    timings["synthesis"] = time.time() - t0
    banner(f"FINAL ANSWER  [{synth_model}]", final)

    banner("TIMINGS (s)", "\n".join(f"{k}: {v:.1f}" for k, v in timings.items())
           + f"\ntotal: {sum(timings.values()):.1f}")

    return {
        "question": question,
        "config": {"panel": panel, "judge_model": judge_model, "synth_model": synth_model},
        "context_brief": brief,
        "panel": [{"label": l, "model": m, "answer": t} for l, m, t in answers],
        "judge_analysis": analysis,
        "timings": timings,
        "final_answer": final,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fusion: panel -> judge -> synthesizer.")
    p.add_argument("question", nargs="?", help="The question. If omitted, read from stdin.")
    p.add_argument("--tier", choices=list(TIERS), default=DEFAULT_TIER,
                   help="quick = Sonnet+Haiku (low-level tasks); standard = Opus+Opus (default); "
                        "deep = Opus x3.")
    p.add_argument("--panel", default=None,
                   help="Comma-separated panel model IDs. Overrides the tier's panel.")
    p.add_argument("--judge-model", default=None, help="Overrides the tier's judge model.")
    p.add_argument("--synth-model", default=None, help="Overrides the tier's synthesizer model.")
    p.add_argument("--max-tokens", type=int, default=4096)
    p.add_argument("--json", dest="json_out", default=None,
                   help="Write the full run (panel + analysis + answer) to this JSON file.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    question = args.question or sys.stdin.read().strip()
    if not question:
        sys.exit("No question provided (pass as an argument or via stdin).")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set in the environment.")

    tier = TIERS[args.tier]
    panel = ([m.strip() for m in args.panel.split(",") if m.strip()]
             if args.panel else list(tier["panel"]))
    if not panel:
        sys.exit("--panel must list at least one model.")
    judge_model = args.judge_model or tier["judge"]
    synth_model = args.synth_model or tier["synth"]

    client = Anthropic()
    result = run_fusion(client, question, panel=panel, judge_model=judge_model,
                        synth_model=synth_model, max_tokens=args.max_tokens)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\n[wrote full run to {args.json_out}]", file=sys.stderr)

    # Final answer to stdout (panel/judge/timings go to stderr) so it can be piped.
    print(result["final_answer"])


if __name__ == "__main__":
    main()
