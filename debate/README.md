# Fusion — panel → judge → synthesis

Fan a question out to a panel of models in parallel (each with web search and a
sandboxed bash/code tool), have a judge extract the structure across their
answers (consensus, contradictions, partial coverage, unique insights, blind
spots), then have a synthesizer write the final answer grounded in that analysis.
This is the ensemble / mixture-of-agents pattern, inspired by OpenRouter Fusion.

## Two ways to run

1. **Through Claude Code (primary, interactive).** Type `/fusion <question>`. The
   workflow lives in `../.claude/`: a `panelist` subagent (spawned once per model),
   a `judge`, and a `synthesizer`, orchestrated by `../.claude/skills/fusion/SKILL.md`.
   See `../.claude/README-fusion.md`.
2. **Programmatically (this folder).** `python fusion.py "<question>"` calls the API
   directly. Use it for scripts, CI, or batch runs. Documented below.

Both share the same prompt design (`prompts/`). The rest of this file is path 2.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

## Run

```bash
# Standard tier (default): Opus + Opus panel, Opus judge + synthesizer
python fusion.py "Is nuclear the right bet for grid decarbonization?"

# Low-level / simple question — cheaper models:
python fusion.py --tier quick "What's the difference between TCP and UDP?"

# Hardest questions — three Opus panelists:
python fusion.py --tier deep --json run.json "your question"

# Pipe a long question in:
echo "..." | python fusion.py
```

The final answer goes to **stdout**; panel answers, judge analysis, and timings
go to **stderr**, so you can pipe just the answer. `--json` saves everything.

## Tiers

The flagship is an **Opus + Opus** fusion — two top-tier panelists, with
diversity coming from their independent tool paths and sampling rather than from
mixing in weaker models. Lower-tier models are reserved for lower-level tasks.

| Tier | Panel | Judge | Synthesizer | Use for |
|------|-------|-------|-------------|---------|
| `quick` | Sonnet 4.6 + Haiku 4.5 | Haiku 4.5 | Sonnet 4.6 | Low-level / simple questions |
| `standard` (default) | Opus 4.8 × 2 | Opus 4.8 | Opus 4.8 | Most questions |
| `deep` | Opus 4.8 × 3 | Opus 4.8 | Opus 4.8 | Hardest questions |

`--panel`, `--judge-model`, and `--synth-model` override the chosen tier.

## How it works

0. **Framing / triage.** Before fan-out, the framing step (on a cheaper model —
   Haiku at quick/standard, Sonnet at deep) triages the question three ways:
   - **Materially ambiguous** → it **stops and asks**: the harness prints 1–3
     clarifying questions and exits (code 2) instead of guessing. Clarify and
     re-run, or pass `--assume-clear` to force a best-effort interpretation.
   - **Trivial** (a lookup, definition, basic how-to, short calculation) → it
     answers directly and skips the whole pipeline — no panel/judge/synthesis,
     since ensembling adds nothing on easy questions. `--force-panel` disables this.
   - **Panel-worthy** → it writes a shared context brief (interpretation, key
     definitions, scope, fixed assumptions, dimensions to address) given to every
     panelist, so they answer the same question and stay comparable. The brief
     fixes the frame, not the answer.
1. **Panel (parallel).** Each model in the tier's panel answers the *framed*
   question concurrently with the `web_search_20260209` and
   `code_execution_20260120` server tools (GA — no beta header). Web search
   reaches the live web; code execution is a sandboxed bash/python environment
   (no internet) for computation and checks.
2. **Judge.** The judge model reads every panel answer and extracts the structure
   into a self-contained analysis (it carries the panel's key specifics and sources).
3. **Synthesis.** The synthesizer writes the final answer from the judge's analysis.
   The raw panel answers are **not** re-sent to the synthesizer — the analysis
   already carries what it needs — which keeps the largest input blob off the most
   expensive call.

Token notes: framing runs on a cheap model per tier; panelists are capped at
`PANEL_MAX_TOKENS` (2048) and `web_search` at `WEB_SEARCH_MAX_USES` (5) uses;
trivial questions skip the pipeline entirely. The brief is passed to the judge so
it knows the intended scope.

## Flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--tier` | `standard` | `quick` (Sonnet+Haiku), `standard` (Opus+Opus), or `deep` (Opus×3). |
| `--frame-model` | tier frame | Overrides the tier's framing/triage model. |
| `--panel` | tier panel | Comma-separated model IDs; overrides the tier's panel. |
| `--judge-model` | tier judge | Overrides the tier's judge model. |
| `--synth-model` | tier synth | Overrides the tier's synthesizer model. |
| `--max-tokens` | `4096` | Per-call output cap (judge/synth/framing; panelists use 2048). |
| `--assume-clear` | off | Skip the clarification gate; frame a best-effort interpretation even if ambiguous (for non-interactive pipelines). |
| `--force-panel` | off | Always run the full panel; don't shortcut trivial questions. |
| `--json FILE` | none | Write the full run (brief + panel + analysis + answer) to FILE. |

## Implementation notes

- **No sampling params.** `temperature`/`top_p` are not sent — they return 400 on
  Opus 4.7 and later.
- **System prompts are cached** (`cache_control`) and transient API errors
  (429/5xx/529/connection) retry with exponential backoff.
- **`pause_turn` is handled.** Server tools run a server-side loop that can pause
  after ~10 steps; the harness echoes the assistant turn and resumes automatically.
## Files

- `fusion.py` — the harness.
- `prompts/panelist_system.md` — panelist system prompt.
- `prompts/judge_system.md` — judge (structural extraction) system prompt.
- `prompts/synthesizer_system.md` — slim synthesizer prompt (distilled from Expanse).
