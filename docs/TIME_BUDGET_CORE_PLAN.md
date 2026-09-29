# Minimal AutoResearch time budget: design and reuse map

Status: deadline design only; prerequisite recovery fixes are in progress on `time-budget`. The audit baseline is `d71bace2cc77cab404ac205ae5c68ad2fe46f25f`. See the recovery implementation plan for current changes and validation.

This is the active scope, superseding the broader proposals in the earlier benchmark and stop/resume documents. Those remain supporting analysis, not additional implementation requirements. Intended destination: NeuriCo main.

## Scope update: managed recovery first

The supported scope is **Full HITL and Auto through the CLI and webpage**, including their initial baseline, iterations and continuation. These modes already share the managed controller and recovery mechanisms. Plain `--autoresearch` is outdated and excluded, as are ordinary unscored research and benchmark adapters. Standalone bootstrap commands are included only if actual supported call paths or necessary shared dependencies establish their relevance.

The [recovery implementation plan](AUTORESEARCH_RECOVERY_IMPLEMENTATION_PLAN.md) governs current work, scope cleanup, validation and sequencing. The [original audit](AUTORESEARCH_RECOVERY_REVIEW.md) includes historical legacy findings; those are not additional delivery requirements. No legacy per-decision persistence or scoring-framework expansion is planned.

Reuse the existing workspace lease, stop control, stage/attempt recovery, durable requests, isolated scoring and publication replay. Preserve the latest validated progress and use the nearest safe existing boundary when rollback is necessary. Scoring outcomes remain manager-owned. Recovery reconnects their handoff and replays recorded decisions; it does not choose a new scorer failure policy. Stop owned writers and validate dependencies before destructive restoration.

The approved scope cleanup is complete: legacy-only and standalone-bootstrap additions were removed, Windows-specific additions were subsequently removed at the user's request, and all 317 remaining tests pass on macOS. Managed lifecycle/restoration ordering has since been verified and a reproduced manager-host shutdown gap fixed; the full suite now has 343 passing tests on macOS. The local recovery review is now closed; a follow-up source check found no additional confirmed scoring/replay defect. Real remote lifecycle verification remains outstanding. The next topic is the deadline policy; no deadline code has been implemented. Each implementation step requires a root-cause explanation, a minimal fix using existing mechanisms, a regression test and user agreement before editing. Platform scope is macOS/Linux; Windows support is deferred to a separate later fix. Current progress and platform/backend limitations are recorded in the recovery implementation plan.

The deadline design below is subsequent proposed work, not authorization to implement it now. It must attach to the repaired managed lifecycle without introducing another recovery system.

## Decision

Add one small run-budget policy to the existing execution, stop, recovery, and reporting paths. The policy answers when the deadline is, how much usable time remains, and whether an operation may start. Existing mechanisms decide what to execute, how to cancel it, what result is accepted, and how interrupted state is recovered.

## First implementation contract

Scope is manager-based Full HITL and Auto through the CLI and webpage, including baseline construction and continuation. Plain `--autoresearch` and standard research do not activate this policy. Shared helpers may be improved where required, with compatibility checks for other callers.

- Input: explicit `idea.constraints.time_limit` in seconds. Omission keeps current behavior. The schema/prompt's implicit one-hour default must not create a runtime budget.
- Start: after validating the request and resolving the workspace, before resource preparation, baseline work, or manager execution. Initial baseline and all iterations share one deadline. Do not start a new budget when the runner internally switches from baseline construction to continuation.
- Accounting: wall time, including provider calls, research, scoring, retries, human waits, and downtime. Existing iteration counts remain an additional stopping condition. No pause accounting or automatic extension.
- Resume: load the existing budget before dispatch. A different launch ID, changed idea duration, or rollback cannot reset it. Reject conflicting saved/requested settings rather than silently starting over.
- Stop: request `budget_exhausted` through existing run control. No further research calls, retries, worker replacement, or paper generation. Existing recovery determines the accepted state and disposition of pending work.
- Result: extend existing results/status with a stop reason and budget snapshot; retain accepted checkpoint/frontier references. Keep cleanup errors visible without replacing the exhaustion cause.

## Concrete shape of the change

**One small `RunBudget` helper** in a focused core module. Its public operations are `remaining_seconds()`, `timeout_for(existing_timeout)`, and `snapshot()`, plus create/load at the run boundary. A nonpositive research allowance is handled by the existing stop control before any operation starts. The helper does not execute or cancel anything.

**One immutable timing record**, proposed `.neurico/run_budget.json`, written through the existing atomic helper. Store a version, budget identity, start and deadline. Exclude it from checkpoints, private-state restoration, and agent-editable research state. No periodically updated remaining-time counter. Creation/loading uses existing workspace ownership/locking; reuse the managed ownership lifetime through finalization instead of allowing competing coordinators or inventing another lock service.

**One active run control.** Attach the optional budget to the existing control/context so nested workers and manager threads use the same policy. Extend the existing stop exception/record with a reason while preserving current callers. Ensure activation covers fresh and continuation paths and is removed on exit; no process-wide budget can leak into the next run.

**Existing execution adapters enforce it.** Immediately before each call, calculate `min(existing operation cap, remaining usable time)`; when the old cap is `None`, use remaining usable time. The existing supervisor, backend call boundaries, and wait/retry loops enforce that allowance. Keep a bounded internal shutdown grace within the original deadline; it does not grant extra research time. Classify a total-budget stop distinctly from an ordinary stage timeout.

**Existing prompts/status explain it.** Render total, remaining-at-render-time, absolute deadline and current operation cap from that same helper. Tell agents to choose work that fits and leave time for scoring/saving an accepted result. Runtime enforcement remains authoritative. Refresh at existing launch/manager-turn boundaries; do not introduce another timer, status service, or MCP tool. Status countdowns derive from the deadline rather than cached decreasing values.

This is a ceiling on the existing search loop. It does not automatically choose the number or duration of experiments, reserve a full final evaluation, or promise a final accepted model if the first attempt consumes the budget.

## Reuse map

Line references refer to the reviewed revision.

| Responsibility | Existing mechanism | Minimal integration |
|---|---|---|
| Duration input | `ideas/schema.yaml:225`, `src/core/idea_manager.py` validation | Use explicit `constraints.time_limit`. No second budget field or CLI override is necessary initially. Do not enforce the implicit prompt default. |
| Run ownership | `ResearchRunner.run_research`, `src/core/runner.py:276` | Create/load one budget before measured setup/research and manager startup; continuation reloads it. |
| Persistence | `atomic_write_json`, `src/core/hitl_util.py:58`; public/private checkpoint exclusions | One budget record outside rollback. Reuse atomic persistence and existing workspace ownership checks. |
| Worker execution | `run_prebuilt_cli_agent`, `src/core/agent_runner.py:223` | Derive the effective timeout from the existing operation cap and remaining allowance at launch. Reuse its polling and process-group cancellation. |
| Stop propagation | `HitlRunStopControl` and `HitlRunStopRequested`, `src/core/hitl_run_control.py:77` / `:21` | Extend existing control with an explicit reason and optional shared budget. Use it across supported managed entry points; no parallel stop channel or mass rename. |
| Worker replacement | `run_worker_with_replacements`, `src/core/hitl_stage_runtime.py:23` | Reuse stop checks; check admission before dispatch and preserve the actual stop reason. |
| Attempt retries | `src/core/hitl_autoresearch.py:1338` | Propagate exhaustion before generic failure handling; do not relaunch or record it as objective failure/provider outage. |
| Manager calls | `_send` / `_send_once`, `src/core/hitl_manager_react.py:2316`; `LLMBackend.send` / `cancel_active`, `src/interactive/llm_backend.py:88` / `:65` | Pass remaining allowance through existing timeout support; bound result waits, MCP startup and retries. Reuse manager cancellation and stale-turn invalidation. |
| Verifier | `_call_verifier_api`, `src/agents/eval_verifier.py:390` | Cap its existing total async timeout; do not introduce another supervisor. |
| Managed initial-stage recovery | `prepare_initial_resume`, `src/core/pipeline_orchestrator.py`; `HitlStageRollback`, `src/core/hitl_stage_runtime.py:161` | Reuse stage boundaries and completed-stage behavior. |
| HITL attempt recovery | `recover_interrupted_hitl_attempt_if_needed`, `src/core/hitl_autoresearch.py:849` | Keep existing pending-worker, pending-decision and rollback classifications. Budget policy does not choose a new recovery action. |
| HITL decision persistence | `_commit_frontier_decision`, `src/core/hitl_autoresearch.py:1785`; transition records in `src/core/hitl_runtime_state.py:826` | Reuse replay of already-recorded decisions. Prevent the expired continuation tail from launching manager/frontier maintenance or research. |
| Continuation | Existing managed continuation dispatch and runtime/frontier recovery | Check original deadline before research dispatch. Recovery grants no additional time. |
| Reporting | `_finalize_stopped_run`, `src/cli/hitl_run_worker.py:116`; runner results; `HitlWorkspaceView.live_status`, `src/core/hitl_workspace_view.py:95` | Add reason and budget snapshot to existing results/status. Distinguish accepted-result availability from cleanup errors. |
| Agent awareness | Existing research/proposer/worker prompt builders and manager context | One snapshot renderer for total, remaining and absolute deadline. Refresh at existing launch/turn boundaries. No new MCP tool required initially. |

## Genuinely new budget code

One focused helper with a testable clock owns initialization/loading, remaining-time calculation, capped operation allowance, and snapshot formatting. It owns no threads, subprocesses, rollback, or result selection.

Persist immutable identity/timing fields such as `version`, `budget_id`, `started_at`, and `deadline_at`; derive remaining time. Proposed location: `.neurico/run_budget.json`, explicitly excluded from public and private rollback. Existing agent `RunTracker` records are per invocation and launch status is per launch, so neither alone can hold a deadline that spans continuations.

Use UTC for restart persistence and monotonic timing in a live process. Do not silently replace malformed or conflicting saved state. Omitted budget preserves existing behavior. Legacy runs need an explicit budget start if the user wants to apply one.

Fit bounded existing cancellation grace within the total allowance. Start with an internal cleanup allowance instead of another configuration family. Unconfirmed cleanup at the deadline remains visible; it must not be reported as successful enforcement.

## Integration checks before budget implementation

Trace the supported managed paths before proposing further edits. Earlier observations about standalone or legacy launchers are not requirements for this scope.

1. **Worker execution:** pass the remaining allowance through reachable shared execution adapters and existing process-group cleanup. Verify bounded waits, including blocked input, only where these flows use them.
2. **Manager waits:** bound manager/backend calls and retries from the same deadline; use existing cancellation and stale-response protection. Transport or SDK retry limits alone do not establish a total deadline.
3. **Retries and finalization:** exhaustion must bypass retries/replacement and prevent new scoring, frontier research or paper generation. Existing deterministic recovery remains available.
4. **Recovery:** reuse the validated managed request, scorer handoff and frontier publication paths. No legacy decision-persistence change belongs here.

Discuss any newly demonstrated defect and its smallest fix before implementation. These checks are not a general cleanup project.

## Stop and resume contract

Expiry requests a stop with reason `budget_exhausted` through existing control. Cancel owned writers before restoring state; use existing manager invalidation to prevent late responses from writing afterward. Existing stage/attempt recovery remains authoritative.

Do not add a new frozen-attempt state. A pending HITL request retains its current continuation record; the deadline gate prevents its execution after expiry. Do not force every saved decision to complete at shutdown: retain it for existing deterministic recovery when necessary, without entering new research work.

With time remaining, use the current resume flow. After expiry, allow cancellation and deterministic recovery only. Keep the original deadline across launch replacement and checkpoint restoration. New-session/extension commands are outside this first change.

Keep scheduler SIGTERM distinct from deliberate stop, as covered by `tests/test_hitl_timeout_recovery.py`. Following unexpected process death, existing recovery runs with the original budget checked before research dispatch.

Preserving research checkpoints does not guarantee recovery of ignored model weights. A model-artifact preservation subsystem is outside this core change.

## Remote lifecycle mapping

| Backend | Reuse | Missing connection |
|---|---|---|
| Local | Shared supervisor, process-group helpers, manager cancellation | Targeted fixes above. |
| Modal | Resource sentinel; `app_stop` in `templates/skills/modal-training/scripts/lifecycle.py:301`; artifact-aware teardown at `:632` | Stop recorded owned execution and confirm coverage of its actual job mode. Preserve artifacts; do not substitute destructive teardown for compute cancellation. |
| DSI Slurm | Workspace lifecycle in `src/core/dsi_slurm_remote.py`; existing skill's held-submission/job bundles; `src/core/dsi_slurm_artifacts.py` | Core currently lacks guaranteed owned-job cancellation. Extend existing submission/lifecycle records for exact ownership and cancellation confirmation; prevent workspace deletion before cleanup. |

Do not add a general scheduler or job-registry framework. Close each backend's narrow lifecycle gap before claiming strict budget support there. Local cancellation alone does not cancel remote jobs; backend-side enforcement must address coordinator death and queue delay. Where existing mechanisms cannot supply this guarantee, strict support remains explicitly unavailable until the gap is closed.

## Delivery and checks

Complete the unified-recovery prerequisite above first. The following sequence then applies to the budget feature.

1. Shared policy, persisted deadline, stop reason, continuation gate.
2. Shared execution integration, manager/verifier allowances, retry gates, prompt awareness.
3. Existing managed recovery integration, original-deadline continuation and frontier publication replay.
4. Verify owned-job cancellation for every backend mode advertised as strictly supported. Local execution is the first supported path; reject a budgeted remote mode at preflight until its cancellation integration is verified.

Keep current iteration counts and explicit zero-iteration behavior. A budget is an additional ceiling, not a new unbounded search mode. Exclude benchmark adapters, new CLI control families, automatic extensions, separate HITL timers, new recovery engines, predictive scheduling and telemetry services.

Verification:

- Fake-clock tests: standard research and unbudgeted AutoResearch stay unchanged; initial stages and iterations share one allowance; restart/rollback/retry cannot reset it; no-budget behavior is unchanged.
- Short subprocess tests: silent/streaming/blocked-input workers and children surviving parent exit.
- Extend `test_hitl_mcp_retry_classification.py` and `test_hitl_provider_failure_propagation.py` for exhaustion bypassing retries/replacement while ordinary provider failures retain current handling.
- Extend `test_hitl_timeout_recovery.py` and `test_hitl_autoresearch_review_fixes.py` for original-deadline continuation, current recovery semantics, and no new research during expired recovery.
- Managed temporary-workspace test: accept candidate A, exhaust candidate B, restart, retain A and valid B evidence through existing frontier/request state; the expired deadline prevents more research in both Full HITL and Auto.
- Backend fakes: cancel only owned jobs, retain artifacts and report unconfirmed cleanup. Real lifecycle verification is required before claiming strict remote support.

This scope refinement changes documentation only. Current recovery validation and its limitations are recorded in the recovery implementation plan; the earlier implementation review remains historical evidence. No deadline implementation has been validated.
