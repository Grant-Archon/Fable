---
name: debate
description: Answer a question by having two independent Opus 4.8 debaters argue it, then synthesizing the single best answer. Triggered by the user as /debate.
disable-model-invocation: true
arguments: [question]
---

You are orchestrating a debate-then-synthesize workflow to answer the user's question. The question is:

$ARGUMENTS

Run these phases yourself (you are the orchestrator; the debaters and synthesizer are subagents). Default to 1 rebuttal round. If the user's text includes "quick", use 0 rebuttal rounds; if it includes "deep", use 2.

Phase 1 — Opening (parallel). In a single turn, spawn two `debater` subagents concurrently (two Agent tool calls in one message). Give both the exact question. If the question is genuinely two-sided (a yes/no, "should we", "X vs Y"), assign Debater A the affirmative/first side and Debater B the other, telling each to argue its assigned side as strongly and honestly as it can. Otherwise tell each to give its strongest independent take with no assigned stance. Keep each subagent's full returned argument.

Phase 2 — Rebuttal (parallel, skip if 0 rounds). For each round, send each debater the other debater's most recent argument and ask it to concede what's correct, rebut what's wrong, and refine its own position. Prefer continuing the same debater instances with SendMessage (using their agent IDs) so they keep their context; if that isn't available, spawn fresh `debater` subagents and include the other side's latest argument in the prompt. Run the two debaters concurrently within a round.

Phase 3 — Synthesis. Spawn one `synthesizer` subagent. Pass it the question and both debaters' full transcripts, clearly labelled (Debater A vs Debater B, with each round). The synthesizer returns the final user-facing answer.

Phase 4 — Referee (skip if the user said "quick"). Spawn one `referee` subagent with the question, both transcripts, and the synthesizer's answer. It returns a JSON score (1-10) plus unresolved points and errors. If the score is below 7 AND the user asked to "revise" or "iterate", re-run Phase 3 once, giving the synthesizer the referee's critique and unresolved points to address.

Final output. Present the synthesizer's answer as your response, preserved as-is — do not re-summarize, re-format, or wrap it in your own framing, because that answer already follows the required output rules (clear prose, minimal formatting, honest calibration). After the answer, you may add at most one short line with the referee's score, e.g. "— referee: 8/10". Do not dump the raw debate transcripts unless the user asks to see them.
