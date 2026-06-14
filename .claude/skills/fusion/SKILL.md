---
name: fusion
description: Answer a question by fanning it out to a panel of models in parallel (each with web search and bash), having a judge extract the structure across their answers, then a synthesizer write the final answer. Triggered by the user as /fusion.
disable-model-invocation: true
arguments: [question]
---

You are orchestrating a Fusion pipeline to answer the user's question. The question is:

$ARGUMENTS

Run these three phases yourself (you are the orchestrator; panelists, judge, and synthesizer are subagents). The panelists are tool-enabled and run on different models for genuine diversity.

Phase 1 — Panel (parallel fan-out). In a single turn, spawn the `panelist` subagent multiple times concurrently (multiple Agent tool calls in one message), giving each the exact same question. Use the Agent tool's `model` parameter to give each panelist a DIFFERENT model so the panel reasons diversely. Default panel: `claude-opus-4-8`, `claude-sonnet-4-6`, and `claude-haiku-4-5` (three panelists). If the user's text includes "deep", add a fourth on `claude-opus-4-7`; if it includes "quick", run just two (`claude-opus-4-8` and `claude-sonnet-4-6`). Keep each panelist's full returned answer.

Phase 2 — Judge. Spawn one `judge` subagent. Pass it the question and every panelist's full answer, clearly labelled by panelist (and which model produced each). The judge returns a structural analysis: consensus, contradictions, partial coverage, unique insights, and blind spots.

Phase 3 — Synthesis. Spawn one `synthesizer` subagent. Pass it the question, the judge's structural analysis, and the panel answers. The synthesizer returns the final user-facing answer.

Final output. Present the synthesizer's answer as your response, preserved as-is — do not re-summarize, re-format, or wrap it in your own framing, because that answer already follows the required output rules (clear prose, minimal formatting, honest calibration, sourced where applicable). Do not dump the raw panel answers or the judge analysis unless the user asks to see them; if useful you may add one short trailing line noting how many models were on the panel.

Notes. The panelists' web-search and bash work happens inside their own isolated contexts and does not reach yours — you receive only their final answers. If a panelist fails or returns nothing, continue with the panelists that succeeded rather than aborting, and note it. Keep the model list above exact (these are the callable model IDs); if you have access to `claude-fable-5`, you may use it as the synthesizer's model for the strongest final write-up.
