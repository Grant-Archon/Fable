# Opus debate → Expanse synthesis

A harness that runs **two debaters** (Opus 4.8 by default) on the same question,
then has a **synthesizer** read both transcripts and produce the single best
answer — the multi-agent-debate / mixture-of-agents pattern with an
LLM-as-synthesizer. An optional cheap **referee** scores whether the synthesis
actually resolved the disagreements, and can trigger one revision pass.

## Why not use `CLAUDE-EXPANSE.md` as the synthesizer prompt?

`CLAUDE-EXPANSE.md` (repo root) is a full consumer **chat-product** system
prompt — product info, memory, artifacts, computer use, web search, copyright
rules, and ~20 tool schemas. Almost none of that helps a synthesizer, and
feeding it whole wastes tokens and injects unwanted behavior. So
`prompts/synthesizer_system.md` is a **slim prompt distilled from its useful
sections**: `evenhandedness`, `tone_and_formatting` / `lists_and_bullets`,
`responding_to_mistakes_and_criticism`, and a conditional `citation_instructions`.

In that file, "Claude Expanse" = `claude-fable-5`, a fictional Mythos-class
model — not assumed callable here. The synthesizer defaults to the first
debater model; point `--synth-model` at a higher tier if you have access.

## Optimizations

- **Concurrency** — the two debaters in each round run in parallel (threads),
  roughly halving per-round wall-clock. Per-phase and total timings print to stderr.
- **Prompt caching** — system prompts are sent with `cache_control`, cutting
  input cost across the opening + rebuttal rounds (and across runs within the cache TTL).
- **Streaming + retry** — calls stream internally (avoids long-request timeouts)
  and retry transient errors (429/500/502/503/529/connection) with exponential backoff.
- **Model diversity** — debaters can run on different models for genuinely
  different reasoning, not two samples of the same distribution.
- **Referee + revision** — a cheap model (Haiku by default) scores the synthesis
  1–10, lists unresolved disagreements and errors, and (`--revise`) can trigger
  one rewrite when the score is below threshold.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

## Run

```bash
# Opening + 1 rebuttal round, synthesis, then a Haiku referee:
python orchestrate.py "Should a 5-person startup use a monorepo?"

# Opposing stances, 2 rebuttal rounds, different models per debater, auto-revise:
python orchestrate.py --rounds 2 --stances "For;Against" \
  --debater-models "claude-opus-4-8,claude-sonnet-4-6" \
  --revise "Is nuclear the right bet for grid decarbonization?"

# Fastest path (independent opinions, no debate, no referee):
python orchestrate.py --rounds 0 --no-referee "..."

# Pipe a long question in and save the full run:
echo "..." | python orchestrate.py --json run.json
```

The synthesized answer goes to **stdout**; transcripts, referee report, and
timings go to **stderr**, so you can pipe just the answer. `--json` saves everything.

## Flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--rounds` | `1` | Rebuttal rounds after the opening. `0` = independent opinions, no cross-talk. |
| `--stances` | none | Semicolon-separated stances for A and B, e.g. `"For;Against"`. |
| `--debater-models` | `claude-opus-4-8` | One value = both debaters; two comma-separated = one each. |
| `--synth-model` | first debater model | Synthesizer model. |
| `--referee` / `--no-referee` | on | Run the referee scoring pass. |
| `--referee-model` | `claude-haiku-4-5-20251001` | Referee model. |
| `--revise` | off | One synthesis rewrite if the referee scores below threshold. |
| `--revise-threshold` | `7` | Score (1–10) below which `--revise` triggers. |
| `--max-tokens` | `2048` | Per-call output cap. |
| `--temperature` | `1.0` | Sampling temperature. |
| `--json FILE` | none | Write the full run to FILE. |

## Files

- `orchestrate.py` — the harness.
- `prompts/debater_system.md` — debater system prompt.
- `prompts/synthesizer_system.md` — slim synthesizer prompt (distilled from Expanse).
- `prompts/referee_system.md` — referee scoring prompt (returns JSON).
