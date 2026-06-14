# /fusion — panel → judge → synthesis (Claude Code)

Answer a question by fanning it out to a panel of models in parallel (each with
web search and bash), having a judge extract the structure across their answers,
then a synthesizer write the final answer grounded in that analysis. This is the
ensemble / mixture-of-agents pattern (inspired by OpenRouter Fusion); a
programmatic API version lives in `../debate/fusion.py`.

## Use

```
/fusion Is nuclear the right bet for grid decarbonization?
/fusion deep   What are the real tradeoffs of a monorepo at 50 engineers?
/fusion quick  What's the best way to rate-limit an API?
```

`quick` runs a 2-model panel; `deep` adds a fourth model. Default is a 3-model panel.

## How it works

The `fusion` skill runs in your main session as the orchestrator and drives
three phases:

1. **Panel (parallel).** It spawns the `panelist` subagent several times at once,
   each on a *different* model (default `claude-opus-4-8`, `claude-sonnet-4-6`,
   `claude-haiku-4-5`) via the Agent tool's `model` override. Each panelist has
   web search + bash and answers independently — no cross-talk, so you get
   genuinely diverse reasoning.
2. **Judge.** The `judge` subagent (`claude-opus-4-8`) reads every panel answer
   and extracts the structure: consensus, contradictions, partial coverage,
   unique insights, blind spots.
3. **Synthesis.** The `synthesizer` subagent writes the final answer grounded in
   the judge's analysis. Its system prompt carries the output rules distilled
   from `CLAUDE-EXPANSE.md`, so it fully governs the answer you read.

Orchestration stays in the main session (rather than a subagent spawning
subagents) because nested subagent spawning needs Claude Code 2.1.172+.

## Why subagents rather than the full CLAUDE-EXPANSE.md prompt

`CLAUDE-EXPANSE.md` is a full consumer chat-product system prompt — product info,
memory, artifacts, copyright rules, ~20 tool schemas — most of which would
misfire on panelists and a synthesizer. Claude Code also can't swap the main
system prompt per command. So each role is a dedicated subagent
(`.claude/agents/*.md`); the synthesizer's body is a slim prompt distilled from
the useful Expanse sections (`evenhandedness`, `tone_and_formatting` /
`lists_and_bullets`, `responding_to_mistakes_and_criticism`, conditional
citations). In `CLAUDE-EXPANSE.md`, "Claude Expanse" = `claude-fable-5`, a
fictional model; agents default to `claude-opus-4-8`. If you have Fable access,
set the synthesizer's `model:` to `fable` for the strongest final write-up.

## Files

- `.claude/skills/fusion/SKILL.md` — the `/fusion` orchestration skill.
- `.claude/agents/panelist.md` — tool-enabled panelist (model set per spawn).
- `.claude/agents/judge.md` — structural-analysis judge (`claude-opus-4-8`).
- `.claude/agents/synthesizer.md` — final-answer synthesizer (Expanse-derived output rules).
