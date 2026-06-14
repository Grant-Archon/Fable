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

## Tiers

The flagship is an **Opus + Opus** fusion — two top-tier panelists, with
diversity coming from their independent tool paths and reasoning rather than from
mixing in weaker models. Lower-tier models are reserved for lower-level tasks.

| Tier | Panel | Judge | Synthesizer | When |
|------|-------|-------|-------------|------|
| `quick` | Sonnet 4.6 + Haiku 4.5 | Haiku 4.5 | Sonnet 4.6 | `quick` keyword, or a clearly low-level question |
| standard (default) | Opus 4.8 × 2 | Opus 4.8 | Opus 4.8 | Most questions |
| `deep` | Opus 4.8 × 3 | Opus 4.8 | Opus 4.8 | `deep` keyword, or an especially hard question |

If you don't say `quick`/`deep`, the orchestrator judges the task level and
defaults to standard, dropping to quick only for clearly low-level questions
(a lookup, a definition, a simple how-to) where Opus would be wasted.

## How it works

The `fusion` skill runs in your main session as the orchestrator and drives
these phases, on the models the tier selects:

0. **Framing.** Before fanning out, the orchestrator writes a short shared context
   brief for the question — interpretation, key definitions, scope, fixed
   assumptions, and the dimensions to address — so the panel stays on one topic
   and the answers are comparable. It fixes the frame, not the answer.
1. **Panel (parallel).** It spawns the `panelist` subagent once per panel model
   at once, setting each one's model via the Agent tool's `model` override, and
   gives every panelist the same question *plus the shared brief*. Each panelist
   has web search + bash and answers independently — no cross-talk. At
   standard/deep the panelists share the Opus model; they still diverge via
   independent reasoning, but on the same framed question.
2. **Judge.** The `judge` subagent reads every panel answer and extracts the
   structure: consensus, contradictions, partial coverage, unique insights,
   blind spots.
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
citations). All roles run on the callable models the tier selects — the panel
and synthesizer on `claude-opus-4-8` at the standard tier.

## Files

- `.claude/skills/fusion/SKILL.md` — the `/fusion` orchestration skill.
- `.claude/agents/panelist.md` — tool-enabled panelist (model set per spawn).
- `.claude/agents/judge.md` — structural-analysis judge (`claude-opus-4-8`).
- `.claude/agents/synthesizer.md` — final-answer synthesizer (Expanse-derived output rules).
