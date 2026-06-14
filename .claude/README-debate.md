# /debate — Opus debate → synthesis (Claude Code)

Answer a question by having two independent Opus 4.8 debaters argue it, then
synthesizing the single best answer. This is the Claude-Code-native version of
the workflow; a programmatic API version lives in `../debate/`.

## Use

```
/debate Should a 5-person startup use a monorepo?
/debate deep   Is nuclear the right bet for grid decarbonization?
/debate quick  What's the best way to rate-limit an API?
```

Keywords in the question tune it: `quick` = 0 rebuttal rounds and no referee;
`deep` = 2 rebuttal rounds; add `revise` to let the synthesizer rewrite once if
the referee scores below 7. Default is 1 rebuttal round plus a referee score.

## How it works

The `debate` skill runs in your main session as the orchestrator and drives four
phases: (1) spawn two `debater` subagents in parallel on the question — opposing
sides if it's two-sided, independent takes otherwise; (2) an optional rebuttal
round where each debater sees the other's argument; (3) a `synthesizer` subagent
reads both transcripts and writes the final answer; (4) a `referee` subagent
(Haiku) scores it. The main session then presents the synthesizer's answer as-is.

Orchestration stays in the main session (rather than a subagent spawning
subagents) because nested subagent spawning needs Claude Code 2.1.172+.

## Why a synthesizer subagent rather than the full CLAUDE-EXPANSE.md prompt

`CLAUDE-EXPANSE.md` is a full consumer chat-product system prompt — product info,
memory, artifacts, computer use, search, copyright rules, ~20 tool schemas — most
of which would misfire on a synthesizer that just reads two transcripts. Claude
Code also can't swap the main system prompt per command. So the synthesizer is a
dedicated subagent (`.claude/agents/synthesizer.md`) whose body is a slim prompt
distilled from the genuinely useful Expanse sections: `evenhandedness`,
`tone_and_formatting` / `lists_and_bullets`, `responding_to_mistakes_and_criticism`,
and a conditional `citation_instructions`. Because it's a subagent, those output
rules fully govern the final answer the user sees.

In `CLAUDE-EXPANSE.md`, "Claude Expanse" = `claude-fable-5`, a fictional
Mythos-class model. The agents default to `claude-opus-4-8`; if you have Fable
access, change `model:` to `fable` (or `claude-fable-5`) in
`.claude/agents/synthesizer.md`.

## Files

- `.claude/skills/debate/SKILL.md` — the `/debate` orchestration skill.
- `.claude/agents/debater.md` — Opus 4.8 debater subagent.
- `.claude/agents/synthesizer.md` — synthesizer subagent (Expanse-derived output rules).
- `.claude/agents/referee.md` — Haiku referee subagent (JSON score).
