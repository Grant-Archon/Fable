---
name: fusion
description: Answer a question by fanning it out to a panel of models in parallel (each with web search and bash), having a judge extract the structure across their answers, then a synthesizer write the final answer. Triggered by the user as /fusion.
disable-model-invocation: true
arguments: [question]
---

You are orchestrating a Fusion pipeline to answer the user's question. The question is:

$ARGUMENTS

Run these three phases yourself (you are the orchestrator; panelists, judge, and synthesizer are subagents). The panelists are tool-enabled and run on different models for genuine diversity.

Pick a tier first. The flagship is an Opus + Opus fusion — two top-tier panelists, with diversity coming from their independent tool paths and reasoning rather than from mixing in weaker models. Reserve lower-tier models for genuinely lower-level or simple questions.

- standard (default): panel = two `claude-opus-4-8`; judge = `claude-opus-4-8`; synthesizer = `claude-opus-4-8`.
- quick: for low-level / simple questions, or when the user's text includes "quick" — panel = `claude-sonnet-4-6` + `claude-haiku-4-5`; judge = `claude-haiku-4-5`; synthesizer = `claude-sonnet-4-6`.
- deep: when the user's text includes "deep", or the question is especially hard — panel = three `claude-opus-4-8`; judge and synthesizer = `claude-opus-4-8`.

Use the explicit "quick"/"deep" keyword when present; otherwise judge the task level yourself and default to standard, dropping to quick only when the question is clearly low-level (a lookup, a definition, a simple how-to) where Opus would be wasted.

Phase 0 — Understand, then frame (you do this yourself, before fanning out). First make sure you understand the user's objective and context. If the question is materially ambiguous — more than one materially different interpretation that would change the answer, or it omits the objective/context you'd need (what they're trying to decide or accomplish, the relevant setting or constraints, key undefined specifics) — STOP. Ask the user 1–3 specific clarifying questions (use the AskUserQuestion tool; list candidate interpretations so they can just pick one) and do NOT spawn any subagents until they answer. Err toward proceeding for ordinary questions: only ask when the ambiguity is genuinely material and you can't frame the work without an answer, and ask at most one round — don't interrogate over preferences.

Once the objective and scope are clear (immediately, for clear questions), write a short shared context brief so the panel stays on one topic and their answers are comparable. The brief states: the interpretation everyone should answer (for minor ambiguity, resolve to the most useful reading and note an important alternate in one line), definitions for any load-bearing or ambiguous terms, what is in and out of scope, any fixed assumptions to hold constant, and the handful of dimensions a complete answer should cover. Critical: fix the FRAME, not the ANSWER — do not take a position, suggest a conclusion, or hint at what's correct, or you collapse the independent reasoning the panel exists for. Keep it to a sentence or two for simple questions; don't manufacture scope.

Phase 1 — Panel (parallel fan-out). In a single turn, spawn the `panelist` subagent once per panel model concurrently (multiple Agent tool calls in one message), setting the Agent tool's `model` parameter to that panelist's model from the tier above. Give every panelist the identical prompt: the question plus the shared context brief from Phase 0, instructing them to stay within that frame and address its dimensions while reasoning independently on the substance. (At standard/deep the panelists share the Opus model — that's intended; they still diverge via independent tool use and reasoning, but on the same framed question.) Keep each panelist's full returned answer.

Phase 2 — Judge. Spawn one `judge` subagent, setting the Agent tool's `model` to the tier's judge model. Pass it the question, the shared context brief, and every panelist's full answer, clearly labelled by panelist (and which model produced each). The judge returns a structural analysis: consensus, contradictions, partial coverage, unique insights, and blind spots.

Phase 3 — Synthesis. Spawn one `synthesizer` subagent, setting the Agent tool's `model` to the tier's synthesizer model. Pass it the question, the shared context brief, the judge's structural analysis, and the panel answers. The synthesizer returns the final user-facing answer.

Final output. Present the synthesizer's answer as your response, preserved as-is — do not re-summarize, re-format, or wrap it in your own framing, because that answer already follows the required output rules (clear prose, minimal formatting, honest calibration, sourced where applicable). Do not dump the raw panel answers or the judge analysis unless the user asks to see them; if useful you may add one short trailing line noting how many models were on the panel.

Notes. The panelists' web-search and bash work happens inside their own isolated contexts and does not reach yours — you receive only their final answers. If a panelist fails or returns nothing, continue with the panelists that succeeded rather than aborting, and note it. Keep the model list above exact (these are the callable model IDs).
