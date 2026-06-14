# Opus debate → Expanse synthesis

A small harness that runs **two independent Opus 4.8 debaters** on the same
question, then has a **synthesizer** read both transcripts and produce the
single best answer. This is the multi-agent-debate / mixture-of-agents pattern
with an LLM-as-synthesizer at the top.

## Why not just use `CLAUDE-EXPANSE.md` as the synthesizer prompt?

`CLAUDE-EXPANSE.md` (in the repo root) is a full consumer **chat-product**
system prompt — product info, memory, artifacts, computer use, web search,
copyright rules, and ~20 tool schemas. Almost none of that helps a synthesizer,
and feeding it whole would waste tokens and inject behaviors you don't want
(reaching for artifacts/search, refusing short answers, etc.).

So `prompts/synthesizer_system.md` is a **slim prompt distilled from the genuinely
useful sections** of that file: `evenhandedness`, `tone_and_formatting` /
`lists_and_bullets`, `responding_to_mistakes_and_criticism`, and a conditional
form of `citation_instructions`. You keep Expanse's judgment and voice without
the chat-app baggage.

Note: in `CLAUDE-EXPANSE.md`, "Claude Expanse" = `claude-fable-5`, a fictional
Mythos-class Claude 5 — it isn't assumed to be a callable endpoint here. The
synthesizer defaults to the same model as the debaters (`claude-opus-4-8`);
point `--synth-model` at a higher tier if you have access.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

## Run

```bash
# Opening round + 1 rebuttal round, then synthesis:
python orchestrate.py "Should a 5-person startup use a monorepo?"

# Assign opposing stances and add a second rebuttal round:
python orchestrate.py --rounds 2 --stances "For;Against" \
  "Is nuclear the right bet for grid decarbonization?"

# Pipe a long question in, and save the full run:
echo "..." | python orchestrate.py --json run.json
```

Debate transcripts stream to **stderr**; the final synthesized answer goes to
**stdout**, so you can pipe just the answer. `--json` saves everything.

## Flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--rounds` | `1` | Rebuttal rounds after the opening (each debater sees the other's latest argument). `0` = independent opinions only, no cross-talk. |
| `--stances` | none | Semicolon-separated stances for A and B, e.g. `"For;Against"`. Omit to let each argue its own strongest take. |
| `--debater-model` | `claude-opus-4-8` | Model for both debaters. |
| `--synth-model` | `claude-opus-4-8` | Model for the synthesizer. |
| `--max-tokens` | `2048` | Per-call output cap. |
| `--temperature` | `1.0` | Sampling temperature. |
| `--json FILE` | none | Write the full run (config + transcripts + answer) to FILE. |

## Files

- `orchestrate.py` — the harness.
- `prompts/debater_system.md` — debater system prompt.
- `prompts/synthesizer_system.md` — slim synthesizer prompt (distilled from Expanse).
