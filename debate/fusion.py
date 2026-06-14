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
    sys.exit("The 'anthropic' package is required. Install it with: pip install anthropic")

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

# Tiers route work by task level. The flagship is Opus + Opus (diversity comes
# from independent tool paths and sampling, not from mixing in weaker models);
# lower tiers use cheaper models for lower-level / simpler questions. Framing is
# scoping work (triage + a short brief), so it runs on a cheaper model than the
# panel/synthesis even at the top tiers.
TIERS = {
    "quick":    {"frame": "claude-haiku-4-5",
                 "panel": ["claude-sonnet-4-6", "claude-haiku-4-5"],
                 "judge": "claude-haiku-4-5",  "synth": "claude-sonnet-4-6"},
    "standard": {"frame": "claude-haiku-4-5",
                 "panel": ["claude-opus-4-8", "claude-opus-4-8"],
                 "judge": "claude-opus-4-8",   "synth": "claude-opus-4-8"},
    "deep":     {"frame": "claude-sonnet-4-6",
                 "panel": ["claude-opus-4-8", "claude-opus-4-8", "claude-opus-4-8"],
                 "judge": "claude-opus-4-8",   "synth": "claude-opus-4-8"},
}
# Panelist answers are raw material for the judge, not final prose — cap them
# tighter than synthesis (overridable via --panel-max-tokens). The synthesizer
# writes the deliverable, so it gets a larger cap of its own.
DEFAULT_PANEL_MAX_TOKENS = 3000
DEFAULT_SYNTH_MAX_TOKENS = 8192
WEB_SEARCH_MAX_USES = 5  # cap searches per panelist (input-token control)
MAX_WORKERS = 8          # cap panel concurrency regardless of panel size
PANEL_CALL_TIMEOUT = 600  # seconds; a hung panelist becomes a failed (empty) one

# Distinct analytical lenses give same-model panelists genuinely different ground
# to cover (sampling params can't be sent on Opus 4.7+, so diversity comes from
# the lens + independent tool paths, not from temperature). Assigned round-robin.
LENSES = [
    "Reason from first principles toward the most defensible answer.",
    "Stress-test the question: hunt failure modes, edge cases, and the strongest counter-case.",
    "Weigh the practical evidence and real-world tradeoffs; be concrete about what the data shows.",
    "Take the contrarian-but-honest view: what would a thoughtful skeptic of the obvious answer argue?",
]


def parse_framing(framed: str) -> tuple[str, str]:
    """Robustly extract the triage tag and body. Tolerates leading fences,
    markdown, or punctuation; fails closed (returns tag '' if no tag found)."""
    m = re.match(r"\s*[`*#>\s]*(NEEDS_CLARIFICATION|TRIVIAL|BRIEF)\b[`*#\s:.\-]*",
                 framed, re.IGNORECASE)
    if not m:
        return "", framed.strip()
    return m.group(1).upper(), framed[m.end():].strip()
DEFAULT_TIER = "standard"
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 529}
# GA server tools (no beta header). Code execution = sandboxed bash/python.
PANEL_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES},
    {"type": "code_execution_20260120", "name": "code_execution"},
]
MAX_CONTINUATIONS = 6  # safety cap on pause_turn resumes per call


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8").strip()


def _text_of(content: list) -> str:
    return "".join(b.text for b in content if getattr(b, "type", None) == "text").strip()


def complete(client: Anthropic, *, model: str, system: str, user: str, role: str = "call",
             tools: list | None = None, max_tokens: int = 4096, max_retries: int = 4) -> str:
    """One logical turn: cached system prompt, transient-error retry, and
    automatic resume on server-tool `pause_turn`. No sampling params (they 400
    on Opus 4.7 and later). Warns to stderr if output is truncated."""
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
        # Surface truncation rather than silently passing a partial answer downstream.
        if resp.stop_reason == "max_tokens":
            print(f"[warning: {role} ({model}) hit max_tokens — output is truncated; "
                  f"raise the cap]", file=sys.stderr)
        elif resp.stop_reason == "pause_turn":
            print(f"[warning: {role} ({model}) still paused at the continuation cap "
                  f"({MAX_CONTINUATIONS}) — output may be incomplete]", file=sys.stderr)
        return _text_of(resp.content)


def banner(title: str, body: str) -> None:
    print(f"\n{'='*70}\n{title}\n{'='*70}\n{body}", file=sys.stderr)


def run_fusion(client: Anthropic, question: str, *, frame_model: str, panel: list[str],
               judge_model: str, synth_model: str, max_tokens: int,
               panel_max_tokens: int = DEFAULT_PANEL_MAX_TOKENS,
               synth_max_tokens: int = DEFAULT_SYNTH_MAX_TOKENS,
               deep: bool = False, assume_clear: bool = False,
               force_panel: bool = False) -> dict:
    framing_system = load_prompt("framing_system.md")
    panelist_system = load_prompt("panelist_system.md")
    judge_system = load_prompt("judge_system.md")
    synth_system = load_prompt("synthesizer_system.md")
    timings: dict[str, float] = {}

    # --- Phase 0: framing — triage (clarify / answer-trivially / frame) ---
    framing_user = f"Question:\n{question}"
    if assume_clear:
        framing_user += "\n\nDo not ask for clarification; choose TRIVIAL or BRIEF."
    if force_panel:
        framing_user += "\n\nDo not answer the question yourself; choose NEEDS_CLARIFICATION or BRIEF."
    t0 = time.time()
    framed = complete(client, model=frame_model, system=framing_system,
                      user=framing_user, role="framing", max_tokens=max_tokens)
    timings["framing"] = time.time() - t0
    tag, body = parse_framing(framed)

    # Enforce the flags in code, not just in the prompt (the model may disobey).
    if tag == "NEEDS_CLARIFICATION" and assume_clear:
        tag, body = "BRIEF", body          # proceed instead of halting
    if tag == "TRIVIAL" and force_panel:
        tag, body = "BRIEF", body          # run the panel instead of shortcutting
    if tag == "":                          # fail closed: unrecognized framing output
        if force_panel or assume_clear:
            tag, body = "BRIEF", framed.strip()
        else:
            print("\nFraming returned unrecognized output; not guessing. Re-run, or pass "
                  "--assume-clear to proceed.\n\n" + framed.strip(), file=sys.stderr)
            sys.exit(2)

    # Materially ambiguous → stop and ask the user (don't guess).
    if tag == "NEEDS_CLARIFICATION":
        print("\nThe question is ambiguous — clarification needed before running Fusion.\n\n"
              f"{body or '(no questions returned)'}\n\nAnswer these and re-run with a clarified "
              "question (or pass --assume-clear to proceed with a best-effort interpretation).",
              file=sys.stderr)
        sys.exit(2)

    # Trivial → a single answer is as good as a panel; skip the pipeline entirely.
    if tag == "TRIVIAL":
        banner(f"TRIVIAL — answered directly  [{frame_model}]", body)
        return {"question": question, "config": {"tier_resolved": "trivial",
                "frame_model": frame_model}, "trivial": True, "final_answer": body}

    brief = body
    banner(f"SHARED CONTEXT BRIEF  [{frame_model}]", brief)

    def framed_question(lens: str) -> str:
        return (f"Question:\n{question}\n\nShared context brief (all panelists share this frame — "
                f"stay within it; reason independently on the substance):\n"
                f"<context_brief>\n{brief}\n</context_brief>\n\n"
                f"Your analytical lens (shapes your approach, not your conclusion): {lens}")

    # --- Phase 1: panel fan-out (parallel, tool-enabled; distinct lens per panelist) ---
    # Resilient: a panelist that raises or hangs becomes an empty answer, and the
    # run continues with the survivors (per the documented contract).
    def ask_panelist(args: tuple[int, str]) -> str:
        i, model = args
        lens = LENSES[i % len(LENSES)]
        try:
            return complete(client, model=model, system=panelist_system,
                            user=framed_question(lens), role=f"panelist {i+1}",
                            tools=PANEL_TOOLS, max_tokens=panel_max_tokens)
        except Exception as err:  # noqa: BLE001 — degrade gracefully, don't kill the run
            print(f"[panelist {i+1} ({model}) failed: {type(err).__name__}: {err}]", file=sys.stderr)
            return ""

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=min(len(panel), MAX_WORKERS)) as pool:
        futures = [pool.submit(ask_panelist, (i, m)) for i, m in enumerate(panel)]
        results = []
        for i, fut in enumerate(futures):
            try:
                results.append(fut.result(timeout=PANEL_CALL_TIMEOUT))
            except Exception as err:  # noqa: BLE001 — timeout or worker error
                print(f"[panelist {i+1} timed out / errored: {type(err).__name__}]", file=sys.stderr)
                results.append("")
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
                        role="judge", max_tokens=max_tokens)
    timings["judge"] = time.time() - t0
    banner(f"JUDGE ANALYSIS  [{judge_model}]", analysis)

    # --- Phase 3: synthesizer writes the final answer from the judge's analysis ---
    # quick/standard: analysis only (self-contained) — keeps the largest input blob
    # off the most expensive call. deep: also pass raw answers so the synthesizer can
    # check the judge on the hardest questions (quality over tokens where it matters).
    synth_user = (f"Question:\n{question}\n\nShared context brief the panel worked from:\n"
                  f"<context_brief>\n{brief}\n</context_brief>\n\n"
                  f"The judge's structural analysis of the panel (your source material):\n\n"
                  f"<judge_analysis>\n{analysis}\n</judge_analysis>\n\n")
    if deep:
        synth_user += (f"The raw panel answers, to check the judge where a conclusion is "
                       f"high-stakes:\n\n{panel_block}\n\n")
    synth_user += "Write the single best final answer for the user, grounded in the analysis."
    t0 = time.time()
    final = complete(client, model=synth_model, system=synth_system, user=synth_user,
                     role="synthesis", max_tokens=synth_max_tokens)
    timings["synthesis"] = time.time() - t0
    banner(f"FINAL ANSWER  [{synth_model}]", final)

    banner("TIMINGS (s)", "\n".join(f"{k}: {v:.1f}" for k, v in timings.items())
           + f"\ntotal: {sum(timings.values()):.1f}")

    return {
        "question": question,
        "config": {"frame_model": frame_model, "panel": panel,
                   "judge_model": judge_model, "synth_model": synth_model},
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
    p.add_argument("--frame-model", default=None, help="Overrides the tier's framing model.")
    p.add_argument("--panel", default=None,
                   help="Comma-separated panel model IDs. Overrides the tier's panel.")
    p.add_argument("--judge-model", default=None, help="Overrides the tier's judge model.")
    p.add_argument("--synth-model", default=None, help="Overrides the tier's synthesizer model.")
    p.add_argument("--max-tokens", type=int, default=4096,
                   help="Output cap for framing and judge calls (default 4096).")
    p.add_argument("--panel-max-tokens", type=int, default=DEFAULT_PANEL_MAX_TOKENS,
                   help=f"Output cap per panelist (default {DEFAULT_PANEL_MAX_TOKENS}).")
    p.add_argument("--synth-max-tokens", type=int, default=DEFAULT_SYNTH_MAX_TOKENS,
                   help=f"Output cap for the final synthesized answer (default {DEFAULT_SYNTH_MAX_TOKENS}).")
    p.add_argument("--assume-clear", action="store_true",
                   help="Skip the clarification gate; frame a best-effort interpretation even if "
                        "the question is ambiguous (for non-interactive pipelines).")
    p.add_argument("--force-panel", action="store_true",
                   help="Always run the full panel; don't shortcut trivial questions to a direct answer.")
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
    frame_model = args.frame_model or tier["frame"]
    judge_model = args.judge_model or tier["judge"]
    synth_model = args.synth_model or tier["synth"]

    client = Anthropic()
    result = run_fusion(client, question, frame_model=frame_model, panel=panel,
                        judge_model=judge_model, synth_model=synth_model,
                        max_tokens=args.max_tokens, panel_max_tokens=args.panel_max_tokens,
                        synth_max_tokens=args.synth_max_tokens, deep=(args.tier == "deep"),
                        assume_clear=args.assume_clear, force_panel=args.force_panel)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\n[wrote full run to {args.json_out}]", file=sys.stderr)

    # Final answer to stdout (panel/judge/timings go to stderr) so it can be piped.
    print(result["final_answer"])


if __name__ == "__main__":
    main()
