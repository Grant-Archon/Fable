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

Phase 1 — Panel (parallel fan-out). In a single turn, spawn the `panelist` subagent once per panel model concurrently (multiple Agent tool calls in one message), giving each the exact same question and setting the Agent tool's `model` parameter to that panelist's model from the tier above. (At standard/deep the panelists share the Opus model — that's intended; they still diverge via independent tool use and sampling.) Keep each panelist's full returned answer.

Phase 2 — Judge. Spawn one `judge` subagent, setting the Agent tool's `model` to the tier's judge model. Pass it the question and every panelist's full answer, clearly labelled by panelist (and which model produced each). The judge returns a structural analysis: consensus, contradictions, partial coverage, unique insights, and blind spots.

Phase 3 — Synthesis. Spawn one `synthesizer` subagent, setting the Agent tool's `model` to the tier's synthesizer model. Pass it the question, the judge's structural analysis, and the panel answers. The synthesizer returns the final user-facing answer.

Final output. Present the synthesizer's answer as your response, preserved as-is — do not re-summarize, re-format, or wrap it in your own framing, because that answer already follows the required output rules (clear prose, minimal formatting, honest calibration, sourced where applicable). Do not dump the raw panel answers or the judge analysis unless the user asks to see them; if useful you may add one short trailing line noting how many models were on the panel.

Notes. The panelists' web-search and bash work happens inside their own isolated contexts and does not reach yours — you receive only their final answers. If a panelist fails or returns nothing, continue with the panelists that succeeded rather than aborting, and note it. Keep the model list above exact (these are the callable model IDs).
