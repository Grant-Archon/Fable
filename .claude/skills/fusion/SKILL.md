---
name: fusion
description: Answer a question by fanning it out to a panel of models in parallel (each with web search and bash), having a judge extract the structure across their answers, then a synthesizer write the final answer. Triggered by the user as /fusion.
disable-model-invocation: true
arguments: [question]
---

You are orchestrating a Fusion pipeline to answer the user's question. The question is:

$ARGUMENTS

Run these three phases yourself (you are the orchestrator; panelists, judge, and synthesizer are subagents). The panelists are tool-enabled; diversity comes from giving each a distinct analytical lens (Phase 1) plus their independent tool paths — not from running different models.

Pick a tier first. The flagship is an Opus + Opus fusion — two top-tier panelists, each given a different analytical lens so they genuinely diverge rather than echo each other. Reserve lower-tier models for genuinely lower-level or simple questions.

- standard (default): panel = two `claude-opus-4-8`; judge = `claude-opus-4-8`; synthesizer = `claude-opus-4-8`.
- quick: for low-level / simple questions, or when the user's text includes "quick" — panel = `claude-sonnet-4-6` + `claude-haiku-4-5`; judge = `claude-haiku-4-5`; synthesizer = `claude-sonnet-4-6`.
- deep: when the user's text includes "deep", or the question is especially hard — panel = three `claude-opus-4-8`; judge and synthesizer = `claude-opus-4-8`.

Use the explicit "quick"/"deep" keyword when present; otherwise judge the task level yourself and default to standard, dropping to quick only when the question is clearly low-level (a lookup, a definition, a simple how-to) where Opus would be wasted.

Phase 0 — Understand, then frame (you do this yourself, before fanning out). First make sure you understand the user's objective and context. If the question is materially ambiguous — more than one materially different interpretation that would change the answer, or it omits the objective/context you'd need (what they're trying to decide or accomplish, the relevant setting or constraints, key undefined specifics) — STOP. Ask the user 1–3 specific clarifying questions (use the AskUserQuestion tool; list candidate interpretations so they can just pick one) and do NOT spawn any subagents until they answer. Err toward proceeding for ordinary questions: only ask when the ambiguity is genuinely material and you can't frame the work without an answer, and ask at most one round — don't interrogate over preferences.

If the objective is clear but the question is trivial — a factual lookup, a definition, a basic how-to, a short calculation, anything with a single well-established answer where one careful answer is as good as an ensemble — do NOT run the pipeline. Just answer it directly and stop; spinning up a panel, judge, and synthesizer would waste tokens for no quality gain. When in doubt between trivial and panel-worthy, run the panel.

Once the objective and scope are clear and the question is panel-worthy, write a short shared context brief so the panel stays on one topic and their answers are comparable. The brief states: the interpretation everyone should answer (for minor ambiguity, resolve to the most useful reading and note an important alternate in one line), definitions for any load-bearing or ambiguous terms, what is in and out of scope, any fixed assumptions to hold constant, and the handful of dimensions a complete answer should cover. Critical: fix the FRAME, not the ANSWER — do not take a position, suggest a conclusion, or hint at what's correct, or you collapse the independent reasoning the panel exists for. Keep it to a sentence or two for simple questions; don't manufacture scope.

Phase 1 — Panel (parallel fan-out). In a single turn, spawn the `panelist` subagent once per panel model concurrently (multiple Agent tool calls in one message), setting the Agent tool's `model` parameter to that panelist's model from the tier above. Give each panelist the question plus the shared context brief from Phase 0 — and a DISTINCT analytical lens, so same-model panelists explore different ground instead of echoing each other (this is where the panel's diversity actually comes from). Suggested lenses: panelist 1 — reason from first principles toward the most defensible answer; panelist 2 — stress-test, hunt failure modes and the strongest counter-case; panelist 3 (deep tier) — weigh the practical evidence and tradeoffs. Tell each the lens shapes its approach, not its conclusion. Keep the brief and dimensions identical across panelists so their answers stay comparable. Keep each panelist's full returned answer.

Phase 2 — Judge. Spawn one `judge` subagent, setting the Agent tool's `model` to the tier's judge model. Pass it the question, the shared context brief, and every panelist's full answer, clearly labelled by panelist (and which model produced each). The judge returns a structural analysis: consensus, contradictions, partial coverage, unique insights, and blind spots.

Phase 3 — Synthesis. Spawn one `synthesizer` subagent, setting the Agent tool's `model` to the tier's synthesizer model. Pass it the question, the shared context brief, and the judge's structural analysis. At the quick and standard tiers, do NOT also pass the raw panel answers — the judge's analysis is self-contained and carries the load-bearing specifics and sources, and re-sending the raw answers just re-pays for the largest input blob on the most expensive call. At the deep tier, DO also pass the raw panel answers, so the synthesizer can check the judge on the hardest questions (quality over tokens where it matters). The synthesizer returns the final user-facing answer.

Final output. Present the synthesizer's answer as your response, preserved as-is — do not re-summarize, re-format, or wrap it in your own framing, because that answer already follows the required output rules (clear prose, minimal formatting, honest calibration, sourced where applicable).

Run silently — the user does not need to know how the sausage is made. Do NOT narrate the pipeline at any point: no announcing the tier or task-level assessment, no "spawning panelists", no phase/step labels, no progress commentary, no mention that a panel, judge, or synthesizer ran, and no trailing note about how many models were involved. The only things the user should ever see are (a) clarifying questions, on the rare occasion the question is materially ambiguous, and (b) the final answer. Everything else stays behind the scenes. Do not dump the raw panel answers or the judge analysis unless the user explicitly asks to see the process.

Notes. The panelists' web-search work happens inside their own isolated contexts and does not reach yours — you receive only their final answers. If a panelist fails or returns nothing, silently continue with the panelists that succeeded rather than aborting. Keep the model list above exact (these are the callable model IDs).
