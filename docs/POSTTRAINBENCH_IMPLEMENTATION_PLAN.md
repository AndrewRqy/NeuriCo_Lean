# NeuriCo implementation plan for PostTrainBench

Date: 2026-09-29. Branch: `time-budget`, based on upstream main `d71bace2`.

Status: proposed implementation plan. No production changes or benchmark runs are included in this document. This plan follows `TIME_BUDGET_IMPLEMENTATION_REVIEW.md`.

## Intended result

Given an assigned base model and target benchmark, NeuriCo should autonomously research, train, evaluate, and improve candidates inside the official one-H100, ten-hour allocation; retain its best valid model; finish with a loadable `final_model`; and provide a complete trace suitable for organizer review.

Success has two separate meanings:

- **Participation readiness:** reproducible, compliant execution; reliable timing, artifacts, evaluation, and audit evidence.
- **Research effectiveness:** improvements over the assigned base and a matched underlying-CLI baseline. This is an experimental outcome to measure, not something the implementation can promise.

The architecture must have one shared run-budget policy. Existing stage, transport, and backend timeouts become consumers of that policy, not parallel definitions of the budget.

## Scope and architecture

Use the official PostTrainBench harness as the outer task/evaluation authority. Run NeuriCo inside its assigned task environment. Keep the existing proposal–experiment–score–compare loop, with a fixed benchmark adapter in place of generated scoring rules.

The first official deployment target is NeuriCo running in the environment that owns the allocated H100. Do not require a generalized remote scheduling platform before the first submission. If Slurm provides the allocation, prefer running the complete measured task inside that allocation. Delegating training to a separate DSI/Modal job requires additional owned-job cancellation and accounting before strict-budget support can be claimed.

| Shared NeuriCo functionality | PostTrainBench adapter functionality |
|---|---|
| One budget object and durable deadline | Import the official start/deadline and resource contract |
| Common subprocess supervision and cancellation | Execute within the approved task/container |
| Consistent remaining-time prompts/status | Supply the official task prompt and permitted assets |
| Existing ordinary/HITL lifecycle and recovery | Use noninteractive autonomous execution for measured runs |
| Metric comparison and attempt history | Invoke and parse the unchanged official evaluator |
| Generic artifact-selection lifecycle hooks | Preserve/export model weights and configuration |
| Structured stop reasons and accounting | Produce the organizer's required review bundle |

The preparation plan, leaderboard, other runs' traces, and cross-run memories must not become context for the evaluated agents. Package a clean runtime and task context; do not mount the development repository and its review documents into a measured task.

## Milestone 0 — Freeze the external contract

Complete before committing to the full evaluation matrix; core runtime engineering can proceed independently.

- Select the accepted benchmark version/commit and record exact model revisions, task variants, container, aggregation, and integrity checks.
- Confirm custom-scaffold eligibility, internal agent-call disclosure, repetitions, trace delivery, and submission review procedure with the organizers. Sending the inquiry needs user authorization.
- Confirm the official timer boundary and how setup, final verification, retries, and interruption are treated. Preinstall generic dependencies and stage only permitted immutable assets; all agent-driven research remains in the measured window.
- Select the underlying agent model/configuration and concrete H100 environment. Match these in the comparison baseline.
- Resolve the teacher-policy ambiguity before enabling local teacher distillation. The initial profile can restrict generation to the assigned model to avoid depending on that ambiguity.

**Exit condition:** a versioned run contract, with unresolved organizer questions explicitly marked. The current site shows v1.1 while the maintainers have announced preparation of v1.2; do not hardcode a forever-fixed task matrix.

References: [official benchmark](https://posttrainbench.com/), [repository](https://github.com/aisa-group/PostTrainBench), [latest reviewed maintainer clarification](https://posttrainbench.com/blog/epoch-ai-review-response/).

## Milestone 1 — Consolidate and repair execution controls

**Purpose:** make the existing per-call limits trustworthy before adding a total budget.

1. Reuse `core.agent_runner.run_prebuilt_cli_agent` for the ordinary proposer/resource finder, manifest trimmer, bootstrap rule-maker, and applicable legacy/standalone paths that currently block on stdout before applying their timeout.
2. Preserve each caller's command, working directory, prompt, logs, environment policy, and success/error contract. In particular, preserve the ordinary proposer's attempt-directory working directory.
3. Use monotonic time for live timeout accounting; supervise prompt input as well as output so a blocked stdin write cannot bypass the limit.
4. On timeout or cancellation, confirm termination of remaining processes in the owned group even if the parent has already exited. Keep bounded graceful termination followed by escalation.
5. Return consistent timeout/stop metadata. Include cleanup time in the operation's accounting and reserve, rather than silently allowing unlimited cleanup.

**Primary code:** `src/core/agent_runner.py`, agent launchers, legacy execution in `src/core/runner.py`.

**Exit checks:** silent and streaming workers time out; large-input workers cannot block supervision; termination-resistant children are reaped; clean completion and existing log formats still work. No real provider calls are necessary for these checks.

## Milestone 2 — Add one shared run budget and awareness contract

**Purpose:** all stages and all iterations consume the same allocation.

### Configuration and lifecycle

- Resolve explicit `constraints.time_limit` and an optional CLI override once. Any override feeds the same canonical value. Keep monetary `constraints.budget` separate.
- Document the migration from advisory `time_limit` to enforced budgeted execution. Do not make the legacy implicit one-hour prompt default a hard runtime deadline. Runs with no explicitly enabled total budget preserve current behavior.
- Create/load the budget at the top-level research-run boundary. When an authoritative harness deadline exists, adopt it; a local configuration may shorten, but never extend, that deadline.
- Store budget identity, total, original start/deadline, finishing reserve, and terminal reason using atomic state writes. Protect this record from checkpoint rollback and ordinary worker modification. A read-only agent-visible view is not the authoritative mutable record.
- Reuse the deadline across iteration retries, checkpoint restoration, and process restarts. Count wall-clock waiting/downtime. Reject a silent reset or conflicting budget on continuation.

### Enforcement and prompts

- Pass one budget policy through the initial pipeline, ordinary/HITL loops, manager calls, scorer/verifier, and execution adapters.
- Calculate each stage allowance as the minimum of its existing cap and the remaining usable time. Exhausted/nonpositive allowances prevent launch.
- Use one prompt renderer and one read-only status query for total, elapsed, remaining, stage allowance, and finishing reserve. The proposer and worker should use this information to scope experiments.
- Recompute before every retry/replacement and after setup/transfer work. Manager reasoning, repair loops, API calls, and human waits consume the same allowance when a budget is enabled.
- Add a distinct `budget_exhausted` outcome. It must not be misclassified as a retryable candidate failure or a user cancellation.
- In budgeted AutoResearch, permit running until the usable budget is exhausted, with an optional explicit iteration maximum. Preserve ordinary defaults without a budget and the existing explicit zero-iteration behavior. Do not leave the current default of one iteration as an accidental stop in the benchmark profile.
- Keep phase caps configurable in the run profile and calibrate them in the pilot. Do not allocate ten equal slices or introduce runtime prediction in the first version.

**Primary code:** top-level runner, a small provider-neutral budget module, `autoresearch.py`, `hitl_autoresearch.py`, pipeline orchestrator, prompt generation, HITL run control/manager and provider backend adapters.

**Exit checks:** time decreases across setup and multiple iterations; ordinary and HITL use the same calculation; replacement workers and manager retries cannot reset it; expiry stops new work; restart/rollback cannot add time; prompts agree with runtime; no-budget behavior remains compatible. Any unsupported budgeted backend must be rejected clearly rather than accepted with false enforcement claims.

## Milestone 3 — Add a thin official-task adapter

**Purpose:** NeuriCo optimizes the official task without replacing its definition.

1. Launch NeuriCo in the provided task directory with the assigned model, target, official prompt, evaluator/templates, timer information, and allowed environment.
2. Prepare a fixed scoring bridge that calls the unchanged official evaluator and translates its result into NeuriCo's comparison schema. Preserve the original metrics and logs. Specify one stable primary metric and direction per task, with validity separate from score.
3. Skip generated rule-making and redundant evaluator-verification stages for this fixed official harness. Do not let an internal rule-maker or verifier redefine official scoring. Keep evaluator files protected and verify their identity.
4. Disable interactive human gates, paper writing, automatic repository publication, unrelated services, and extra compute backends in the benchmark profile. Retain autonomous proposal/execution cycles and permitted general research.
5. Establish the initial baseline through the same evaluator and comparison contract, within the official accounting boundary. Preserve the distinction between development subsets and full final evaluation. Compare candidates using matching development settings, not different sample sizes or decoding budgets.
6. Provide the official decontamination tool and preserve dataset provenance/checker outputs. Apply official rules to model identity, test-item-derived training, API use, and benchmark lookup.
7. Preflight the official final evaluator and review dependencies before consuming a full agent run. Final scoring uses a pristine agreed environment, not merely whatever packages the training worker installed.

**Proposed location:** one benchmark integration package, such as `src/benchmarks/posttrainbench/`, plus the upstream launcher integration. Names are implementation choices; avoid changes scattered through generic templates that force every NeuriCo task to behave like PostTrainBench.

**Exit checks:** an offline/fake official task exercises the complete bridge; malformed, missing, stale, nonfinite, and failed scores are rejected; official assets remain unchanged; a small real artifact loads under the final evaluator.

## Milestone 4 — Preserve the best model and review evidence

**Purpose:** useful research progress survives a failed final iteration or deadline.

- Keep candidate weights in immutable versioned directories within the permitted workspace, outside Git. Record the relationship among code revision, model artifacts, config/tokenizer, data provenance, evaluation settings, and score.
- Promote a candidate only after successful compatible scoring. Atomically update the accepted-model manifest; do not overwrite accepted weights while trying the next experiment.
- Use the existing controller's acceptance/rejection lifecycle through a narrow artifact hook. Do not implement a second selection loop inside the benchmark adapter.
- Maintain a recoverable accepted export as research proceeds. Reserve time for materializing/validating the final `final_model`, including any required adapter merge. Prefer same-filesystem atomic operations where allowed; measure large-file export costs.
- On exhaustion/interruption, stop active work and retain the accepted model. An unscored partial candidate or an unchanged base fallback must not be labeled a successfully post-trained submission; use official failure handling.
- Capture every internal agent transcript, attempt outcome, stage timing, resource log, script, checker output, and metric. Preserve failed/rejected attempts outside rollback and supply a merged chronological trace for review.
- Produce a run manifest with the benchmark/runtime revisions, agent model/configuration, hardware, repetition ID, original deadline, actual duration, artifact hashes, official results, and integrity verdicts.

**Exit checks:** reject, timeout, crash, and resume scenarios preserve the selected artifact and its matching score; a disk/export error is reported honestly; the review trace includes proposer and worker activity; full evaluation succeeds in the pristine environment.

## Milestone 5 — Generate tasks and validate with pilots

Create one declarative idea/task template parameterized by base model, target, and repetition. Generate isolated run configurations from the versioned matrix. Do not hand-maintain 28 divergent prompts or bake a preferred training recipe into each idea.

For the currently reviewed four-model/seven-target matrix, a complete repetition is 28 independent runs. Each starts from its assigned base model and fresh agent/workspace state; no checkpoint, whiteboard, or research-history transfer between scored tasks/repetitions.

Validation sequence:

1. **CPU-only integration checks:** short fake training/evaluation processes exercise accounting, score translation, selection, cleanup, and resume. Fix the local dependency-import issue before claiming the complete recovery test suite passes.
2. **Short H100 wiring test:** validate CUDA, model access, evaluator dependencies, artifact loading, and audit outputs. Label it an engineering test, not an official ten-hour result.
3. **Full Qwen3-1.7B Base → GSM8K pilot:** one H100, ten hours shared across all iterations. Record baseline, score changes, stage durations, time to first valid trained model, failed-attempt cost, and export duration.
4. **Second target:** HumanEval checks a different output/evaluation path. Add a grader-backed task before claiming all seven targets are supported.
5. **Tune general orchestration only:** use the pilot to calibrate stage caps and the finishing reserve, then freeze configuration for the measured campaign. Disclose pilot development and obtain organizer agreement about development/scored-run separation; do not feed prior run solutions or traces to the measured agent.

**Exit condition:** the full pilot stops within its authoritative limit with a valid export, complete trace, reproducible official evaluation, and no unaccounted background compute. A higher score is desirable evidence, not a substitute for these requirements.

## Milestone 6 — Measure NeuriCo's contribution and submit

- Compare NeuriCo against the same underlying model/configuration running the official CLI scaffold directly, under the same model/target, hardware, and time budget. Report differences in API tokens/cost rather than hiding extra orchestration expenditure.
- Freeze the accepted version, scaffold revision, prompts, run profile, matrix, and repetitions before the campaign. Record all outcomes; do not omit failed cells or selectively rerun low scores.
- Apply the official integrity checks and aggregation. Keep task-level scores, base-model improvements, runtime/cost, repetition variation, and invalid-run counts beside the overall score.
- Submit the artifacts for organizer review. Only describe the entry as official after acceptance.

For current planning, one full repetition allocates up to 280 H100-hours; three allocate 840. Three repetitions for both NeuriCo and the matched CLI allocate 1,680. These are arithmetic agent-phase allocations, not a confirmed required repetition count or a spending authorization. Setup, verification, judging, storage, and reruns are additional. Recalculate if the accepted version changes the matrix.

## Proposed delivery order

| Change set | Deliverable | Dependency |
|---|---|---|
| A | Consolidated subprocess execution and reliable timeout cleanup | None |
| B | One durable budget, ordinary/HITL integration, shared awareness/status | A |
| C | Official-task/scoring adapter and autonomous benchmark profile | Contract for selected pilot; A and B |
| D | Accepted-model lifecycle, deadline-safe export, complete review bundle | C |
| E | Parameterized task generation, pilot checks, documented run procedure | C and D |
| F | Frozen comparative campaign and reviewed submission | Pilot evidence and organizer contract |

Keep these as reviewable changes on the `time-budget` workstream; branch/PR splitting can follow the project's normal review practice. No paid campaign is launched by implementing A–E.

## What is deliberately deferred

Predictive experiment scheduling, automatic budget extensions, separate ordinary/HITL accounting policies, generalized multi-GPU scheduling, cross-run memory, a broad remote-compute rewrite, prescribed training recipes, and paper generation are unnecessary for the first valid entry.

The immediate engineering target is **a reliable, auditable single-task run**, not all 28 tasks at once. Once that works, expanding coverage is mostly configuration and evaluator-specific validation rather than another research engine.
