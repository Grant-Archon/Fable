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
# Default panel: opus-4-8, sonnet-4-6, haiku-4-5
python fusion.py "Is nuclear the right bet for grid decarbonization?"

# Custom panel + a specific synthesizer, save the full run:
python fusion.py --panel "claude-opus-4-8,claude-sonnet-4-6,claude-haiku-4-5" \
  --synth-model claude-opus-4-8 --json run.json "your question"

# Pipe a long question in:
echo "..." | python fusion.py
```

The final answer goes to **stdout**; panel answers, judge analysis, and timings
go to **stderr**, so you can pipe just the answer. `--json` saves everything.

## How it works

1. **Panel (parallel).** Each model in `--panel` answers the question concurrently,
   with the `web_search_20260209` and `code_execution_20260120` server tools
   enabled (GA — no beta header). Web search reaches the live web; code execution
   is a sandboxed bash/python environment (no internet) for computation and checks.
2. **Judge.** `--judge-model` reads every panel answer and extracts the structure.
3. **Synthesis.** `--synth-model` writes the final answer grounded in that analysis.

## Flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--panel` | `claude-opus-4-8,claude-sonnet-4-6,claude-haiku-4-5` | Comma-separated panel model IDs (heterogeneous = more diverse reasoning). |
| `--judge-model` | `claude-opus-4-8` | Model that extracts the structural analysis. |
| `--synth-model` | `claude-opus-4-8` | Model that writes the final answer (use `claude-fable-5` if you have access). |
| `--max-tokens` | `4096` | Per-call output cap. |
| `--json FILE` | none | Write the full run (panel + analysis + answer) to FILE. |

## Implementation notes

- **No sampling params.** `temperature`/`top_p` are not sent — they return 400 on
  Opus 4.7+/Fable 5.
- **System prompts are cached** (`cache_control`) and transient API errors
  (429/5xx/529/connection) retry with exponential backoff.
- **`pause_turn` is handled.** Server tools run a server-side loop that can pause
  after ~10 steps; the harness echoes the assistant turn and resumes automatically.
- "Expanse" in `../CLAUDE-EXPANSE.md` = `claude-fable-5`, a fictional model; the
  synthesizer defaults to `claude-opus-4-8`.

## Files

- `fusion.py` — the harness.
- `prompts/panelist_system.md` — panelist system prompt.
- `prompts/judge_system.md` — judge (structural extraction) system prompt.
- `prompts/synthesizer_system.md` — slim synthesizer prompt (distilled from Expanse).
