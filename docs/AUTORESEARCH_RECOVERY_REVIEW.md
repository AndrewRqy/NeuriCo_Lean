# AutoResearch recovery: implementation review and consolidation plan

Reviewed 2026-09-29 on `time-budget`, revision `d71bace2cc77cab404ac205ae5c68ad2fe46f25f` (merge of ordinary research integration). This is an audit, not an implementation. Production files were not changed.

## Historical scope notice — 2026-09-29

This audit intentionally retains its original broader findings. The user subsequently narrowed implementation to manager-based **Full HITL and Auto through the CLI and webpage**. Plain `--autoresearch` is outdated and excluded. R2, R3 and R7, and legacy-only portions of other findings, are not implementation requirements. Standalone bootstrap relevance must be established through actual supported/shared call paths. The [updated implementation plan](AUTORESEARCH_RECOVERY_IMPLEMENTATION_PLAN.md) is authoritative for scope, working-patch disposition and delivery order. Its discussion-before-each-step agreement applies; this historical audit does not authorize legacy fixes.

## Conclusion

NeuriCo already has most of the mechanisms needed for reliable recovery: public checkpoints, private-state snapshots, durable worker requests, continuation records, isolated scoring, replayable decisions, workspace ownership, and cooperative stop control. A second recovery framework would duplicate them.

However, the desired behavior is not implemented uniformly. Full HITL and Auto HITL already share their recovery controller. The largest capability gap is non-HITL AutoResearch, followed by differences between initial construction and iterations, live worker replacement and process restart, and direct and detached launch paths. There are also concrete bugs where an existing preservation path is bypassed.

The right first change is to consolidate recovery around these existing owners, fix the destructive ordering problems, and add only the durable boundaries missing from non-HITL execution. A time budget should subsequently request a stop through that lifecycle; it should not decide independently which checkpoint to restore.

## Implementation scope clarification

The later proposal to ask the user for a new stop-versus-reject policy on scorer errors was withdrawn. This review's recovery work concerns interruption, durable handoffs and replay, not interpretation of scoring evidence. Full and Auto HITL already hand scorer outcomes (including captured errors) to the manager, which decides acceptance, rejection or repair. Preserve that loop. Observations about the separate legacy comparator do not authorize replacing its decision policy either. See the implementation plan for the corrected contract and current progress.

## Scope and execution map

“Non-HITL AutoResearch” below means the direct scored search loop, not ordinary unscored research. Ordinary research now shares substantial infrastructure, but remains outside this delivery's behavior changes.

| Path | Current owner and durable state | Recovery position |
|---|---|---|
| Non-HITL fresh baseline | `construct_fresh_initial_node`, shared pipeline, AutoResearch state | No equivalent of managed initial-stage pending-request recovery. |
| Non-HITL iterative search / continuation | `AutoResearchController`, attempt history, `autoresearch_state.json` | Generally accepted-parent recovery; finer interrupted scoring state is not durable. |
| Non-HITL bootstrap | `construct_bootstrap_initial_node`, bootstrap source marker/history | Source checkpoint and failure rollback exist; process-loss reentry does not first restore the saved source. |
| Full HITL initial / iterative search | Managed pipeline, `HitlAutoResearchController`, runtime state and frontier | Pending requests, isolated scoring, stage boundaries and publication replay exist. |
| Auto HITL initial / iterative search | Same managed owners and persistence as Full HITL | Same recovery capabilities; approval authority differs. |
| Managed bootstrap | `construct_bootstrap_hitl_baseline` | Public/provider-local prepublication boundary and replayable initial publication exist. |
| Web / terminal managed launch | `HitlRunController` → detached `hitl_run_worker` → shared runner | Installs request-specific stop control and signal policy. These are interfaces, not separate research algorithms. |
| Direct managed runner/CLI | Shared `ResearchRunner.run_research` | Does not install the detached worker's complete lifecycle; this affects failure handling. |

Main references: `src/core/runner.py:276`, `src/core/autoresearch.py:1017`, `:1090`, `:1315`, `:1607`; `src/core/hitl_autoresearch.py:284`, `:534`, `:1246`; `src/core/hitl_mode.py`; `src/cli/hitl_run_worker.py:190`.

Switching Full/Auto policy already reuses the pending request and continuation through `HitlRuntimeState.adopt_hitl_mode` (`src/core/hitl_runtime_state.py:390`). Do not create separate recovery implementations for those modes.

## Existing mechanisms to retain

| Responsibility | Existing implementation | What to reuse |
|---|---|---|
| Stop propagation | `src/core/hitl_run_control.py:77` | Request-specific stop file/event, active control, distinct stop exception. |
| Local worker supervision | `src/core/agent_runner.py:223` | Stop polling and process-group handling; improve this owner where needed. |
| Worker replacement | `src/core/hitl_stage_runtime.py:23`; `src/core/hitl.py:3302` | Finalized response takes precedence over late worker failure; pending-command and live continuation replacement. |
| Durable requests | `src/core/hitl_runtime_state.py`; `src/core/hitl.py:2811` | Request identity, provenance, continuation, saved response and scoring handoff. |
| Candidate scoring | `src/core/hitl_scoring_workspace.py:338`; `src/core/hitl_autoresearch.py:2235` | Immutable source checkpoint, isolated scorer workspace, sealed evaluator, source/score evidence and cached result. |
| Initial recovery | `src/core/pipeline_orchestrator.py:431` | Retain validated pending stage/scoring requests, reuse completed stages, otherwise restore an existing boundary. |
| Paired rollback | `src/core/hitl_stage_runtime.py:161` | `HitlStageRollback` public/private descriptor validation and restoration. |
| Iteration publication | `src/core/hitl_autoresearch.py:1729`, `:1785` | Replayable frontier decision: prepared, idea logged, frontier finalized, mirrored, completed. |
| Baseline publication | `src/core/hitl_autoresearch.py:165` | Replayable checkpoint/root/configuration/mirroring publication. |
| Bootstrap safety | `src/core/hitl_autoresearch.py:472`, `:534` | Existing prepublication source and provider-local backup; forward replay once publication starts. |
| Ownership / reporting | `src/core/hitl_lock.py:151`; `src/core/hitl_workspace_view.py:95` | Existing lease and status projection, expanded to cover the lifecycle consistently. |
| Artifact integrity | Workspace write guard, sealed evaluator manifest, checkpoint and private-state stores | Validate the actual recorded attempt, candidate and evaluator before reuse. |

`autoresearch_common.py` currently shares behavior-neutral helpers, not a complete recovery policy. The old pipeline `resume_pipeline` method is not a general scored AutoResearch restart implementation. `STATE.md`, file existence, and a recent Git commit are not substitutes for validated durable phase boundaries.

## Findings

Priorities: P1 = first consolidation work because progress can be lost or recovery can run without adequate ownership. P2 = required parity/safety follow-up, some conditional on backend or interruption position. “Probe” distinguishes isolated reproduction from end-to-end execution.

### R1 — P1: Managed initial shutdown bypasses finer recovery

**Trigger:** Cooperative stop while the initial experiment/scoring boundary remains armed, including a valid pending scoring request or cached score.

`run_pipeline` rethrows `HitlRunStopRequested`, then its `finally` unconditionally restores the pre-experiment boundary if the overall pipeline was not successful (`src/core/pipeline_orchestrator.py:1024–1034`). `_recover_experiment_runner_from_runtime_checkpoint` retires scoring references and restores public/private state (`:1191`). This bypasses the preservation decision already available in `prepare_initial_resume`.

**Impact:** Completed experiment artifacts and the pending scoring request can be removed. Scheduler process termination bypasses this `finally`, so the same saved state can survive a hard interruption but be lost on cooperative shutdown.

**Evidence:** Real Git probe using the existing initial-scoring fixture, for both prepared and cached scoring. Before stop: candidate present, pending request present. After the actual outer pipeline stop/finally: both absent. Provider work was replaced with a stop exception; Git and private-state restoration were real. Existing restart tests independently confirm that the pending scoring path can resume without repeating the experiment.

**Minimal fix:** Use the existing recovery classifier before destructive initial cleanup. Preserve validated pending/completed state on stop; restore the stage boundary only when that is the nearest safe recovery point. Keep objective rejection/explicit repair behavior separate from interruption.

### R2 — P1: Non-HITL scoring failures discard completed work

**Trigger:** An experiment completes but scorer transport/infrastructure execution raises or fails before a valid result is available.

`AutoResearchController.run_iteration` handles broad exceptions, produces unsuccessful scoring evidence, and follows candidate comparison/parent restoration (`src/core/autoresearch.py:1655–1824`). The candidate checkpoint is created after scoring; there is no managed-style durable prepared/scored handoff before it.

**Impact:** An otherwise useful completed experiment cannot reliably resume at scoring. Infrastructure interruption is treated like a failed candidate rather than preserved unfinished work.

**Evidence:** Control-flow probe of the actual method with a scorer `ConnectionError` and mocked checkpoint operations: acceptance false and parent restored. This does not claim all failed evaluations are infrastructure errors; objective invalidity remains a legitimate rejection.

**Minimal fix:** Reuse the immutable source/scoring handoff primitive. Add the missing attempt metadata before dispatching scoring and reuse a validated saved score afterward. Preserve the existing non-HITL comparator; no HITL manager is required.

### R3 — P1: Non-HITL accepted progress is persisted too late

`AutoResearchController.run` updates the accepted reference in memory (`src/core/autoresearch.py:1635`). The continuation owner writes the saved current-best state after the entire loop returns (`:1395`).

**Trigger and impact:** Iteration 1 accepts an improvement; iteration 2 is interrupted. Saved continuation state can still name the old parent. Recovery does not reconcile the accepted decision from history before using that pointer.

**Evidence:** Control-flow probe confirmed an accepted first iteration with the saved pointer still at the original parent after interruption of the second.

**Minimal fix:** Persist each completed decision to the existing history/current-best state and reconcile partial publication on restart. Reuse the managed write-ahead/replay pattern without creating another best-result store. Managed frontier decisions already persist per attempt; their loop-level iteration accounting is a separate issue.

### R4 — P1: Lifecycle behavior depends on launch path; stop cleanup outlives ownership

The runner's workspace ownership decorator covers managed research/AutoResearch/continuation flags, but not non-HITL or the bootstrap flag itself (`src/core/runner.py:114`). Request-specific active stop control is installed in the detached worker (`src/cli/hitl_run_worker.py:215`), not uniformly at every public runner entry.

Manager provider exhaustion requests a preserving run stop when active control exists; without it, the same handler cancels the request as a backend failure and initiates rollback (`src/core/hitl_manager_react.py:2116`). Thus launching the same managed research through another entry point can change the recovery outcome.

The detached worker calls `_finalize_stopped_run` after `run_research` has unwound its lease (`src/cli/hitl_run_worker.py:116`, `:267`). Recovery/cleanup therefore has a potential overlap window with a subsequent owner. This race is inferred from source; it was not stress-tested.

**Minimal fix:** Extend the existing lease and stop-control lifetime to encompass admission, preparation, execution, recovery and final status across AutoResearch entry points. Keep one owner until cleanup finishes. Do not add another lock or stop channel.

### R5 — P2: Live replacement retains more than process restart

The live runtime can replace a worker using its saved continuation even without an outstanding pending command (`src/core/hitl.py:3302`). Iterative restart only preserves this path when a resumable pending command and matching continuation both exist (`src/core/hitl_autoresearch.py:849`, especially `:905`). Otherwise a valid fallback snapshot leads to accepted-parent restoration and attempt-directory removal.

**Evidence:** Two control-flow probes: matching pending request preserved the attempt without restoration; continuation-only state restored the parent and removed the attempt.

**Minimal fix:** Share validated continuation/phase-boundary selection between live replacement and restart. This is not a reason to remove the pending-request check blindly: a saved prompt does not establish workspace integrity, completed tool effects, or provider-session resumability. Where continuation cannot be validated, preserve evidence and use the nearest existing verified phase boundary.

### R6 — P1/P2: Preparation can mutate recovery inputs too early

Managed initial recovery is inspected before helper/resource staging and enables `preserve_existing`; managed iterative recovery occurs after those same operations (`src/core/runner.py:698–754`). Refreshed declared resources can therefore change an interrupted reviewed workspace before provenance checks.

Bootstrap also enters through common workspace preparation before its constructor captures the guarded boundary and calls its own preparation callback. A provider-local backup can capture already-refreshed input rather than the pre-entry state. Separately, non-HITL bootstrap reuses a saved source SHA without restoring it on process-loss reentry (`src/core/autoresearch.py:1140`); managed bootstrap does restore its saved prepublication boundary (`src/core/hitl_autoresearch.py:592`).

**Evidence:** Source ordering. No claim that every preparation call changes files; the defect is that recovery has not yet authorized such changes.

**Minimal fix:** Recover/reconcile before workspace preparation for every AutoResearch path. Reuse the existing preserved-input staging branch. Bootstrap should have one guarded preparation pass and reuse the managed prepublication restoration pattern.

### R7 — P2: Non-HITL recovery rejects potentially recoverable sealed scoring inputs

`validate_continue_autoresearch_workspace` requires public `scoring/eval.py` before entering its auto-restore path (`src/core/autoresearch.py:1442`, `:1466`). A hard interruption while evaluator inputs are sealed can leave that authoritative file outside the public workspace.

**Evidence:** Control-flow probe showed validation failure before any restore attempt when the public evaluator was absent. This establishes the ordering problem, not validity of every possible sealed copy.

**Minimal fix:** Reconnect/verify the existing sealed evaluator authority before public-input validation where recovery evidence authorizes it. Git reset alone is insufficient because evaluator state is intentionally excluded. Continue to fail safely if the authoritative evaluator is missing or mismatched.

### R8 — P2: Some rollback paths validate the second half after altering the first

Initial experiment recovery restores the public checkpoint before checking the required private snapshot fields (`src/core/pipeline_orchestrator.py:1210`). Bootstrap restores public state before checking for a required provider-local backup (`src/core/hitl_autoresearch.py:510`). They retain recovery markers on failure, which is good, but the workspace may already have been partially restored.

**Minimal fix:** Reuse `HitlStageRollback.from_descriptor`'s prevalidation principle: verify all public/private/provider-local dependencies before starting destructive restoration. Retain existing replay markers if restoration itself fails. Prevalidation reduces avoidable partial mutations; it does not make multiple filesystems transactional.

### R9 — P2: Fallback rollback preserves less diagnostic evidence than it could

Iterative HITL fallback archives a runtime incident record, then removes the attempt directory (`src/core/hitl_autoresearch.py:765`, `:949`). That record is not a complete preservation of attempt logs, partial files, or artifacts. Public Git checkpoints also do not preserve ignored training weights or remote-only data.

**Minimal fix:** Retain useful existing attempt logs and known artifact/checkpoint references before removing an interrupted attempt. Keep unaccepted work separate from the accepted frontier. Do not promise arbitrary mid-training resume without a training artifact contract; existing framework checkpoints can only preserve what they actually include.

### R10 — P2, required for remote guarantees: Backend lifecycle is not yet recovery-safe

The DSI Slurm lifecycle deletes a deterministic remote workspace at creation and in stage cleanup (`src/core/dsi_slurm_remote.py:65`, `:136`). This lifecycle does not itself cancel submitted jobs. Preserving a local request alone therefore does not prove that remote output still exists or that an old writer has stopped.

Modal has an existing resource sentinel, sweep, and pull-complete/teardown contract (`src/core/pipeline_orchestrator.py:1276`; `templates/skills/modal-training/scripts/lifecycle.py`). Those mechanisms should be reused, but a training environment or volume record is not necessarily a complete submitted-task registry. Cancellation and destructive environment teardown are different operations.

**Minimal fix:** Reconcile owned submitted work, quiesce/cancel it, preserve required output, then restore or clean up. If existing metadata does not identify the submitted job, add that narrow ownership record to the backend lifecycle. Do not infer remote cancellation from local agent exit. Real Slurm/Modal validation remains necessary; this audit ran no remote jobs.

## Current outcome differences

| Event | Managed paths today | Non-HITL today | Desired shared rule |
|---|---|---|---|
| Explicit user stop | Detached control exists; iterative request recovery can preserve work, initial pipeline can roll back too far. | No equivalent consistently installed run control. | Stop execution, preserve nearest verified resume point, do not silently restart. |
| Provider unavailable after retries | Active control selects run stop; absent control can select request cancellation/rollback. | Broad attempt/scorer handling can turn infrastructure failure into rejection. | Preserve recoverable work; exhaustion ends automatic retries. |
| Scheduler/process termination | Default SIGTERM in detached worker leaves durable state for next invocation; classification decides restart. | Saved accepted pointer may lag; evaluator may remain sealed. | Inspect durable state on next invocation; no inference that interrupted work is complete. |
| Candidate objectively rejected | Recorded frontier decision and rollback/publication machinery. | Comparator rejection and parent restoration. | Preserve intended rejection semantics and history. |
| Crash while committing decision | Managed frontier/initial publication have replay states. | Per-loop state update leaves a gap. | Complete a recorded decision exactly once before proposing new work. |
| Future total time budget exhausted | Not yet implemented as the requested common deadline. | Same. | Reuse the same preservation decision, but prohibit further research under the original exhausted deadline. |

The recovery point and permission to execute again are separate. Equivalent valid durable evidence should select the same preservation point regardless of stop cause; cause determines restart policy. “Accepted result” and “unfinished resumable attempt” should remain separate references.

## Minimal consolidation sequence

1. **Fix existing managed lifecycle defects first:** R1, R4 and recovery-before-preparation in R6. Extend current ownership/control rather than introducing a coordinator beside it. Add regression coverage at the public entry and outer pipeline boundaries, not just helper tests.
2. **Make existing recovery selection consistent:** use the current request, scoring, completed-stage and rollback descriptors in both live replacement and full restart. Prevalidate restoration dependencies and preserve evidence before destructive fallback (R5, R8, R9). Avoid inventing arbitrary provider-session resume.
3. **Bring non-HITL up to the same durable boundaries:** reuse source checkpoint/isolated scoring and decision replay patterns, add only missing fields in existing attempt state, persist accepted decisions immediately (R2, R3, R7). Keep its comparator and user-facing mode unchanged.
4. **Align bootstrap:** reuse its existing prepublication/publication split, including restore-on-reentry and provider-local preservation. Ensure the runner does not prepare inputs before capturing/recovering this boundary (R6).
5. **Verify backend quiescence before claiming remote-safe recovery:** extend existing job/lifecycle metadata only where necessary (R10). This can be separate from local parity work but is a prerequisite for reliable remote deadline enforcement.
6. **Then add the small deadline policy** described in `TIME_BUDGET_CORE_PLAN.md`. It controls admission and requests stop; the consolidated recovery path owns preservation and restart location.

This is consolidation plus a few missing records, not merely replacing two function calls. Conversely, it does not justify replacing the frontier, runtime state, checkpoint store, manager policy or backend lifecycle wholesale.

## Regression contract

Cover non-HITL, Full HITL and Auto HITL; fresh baseline, iteration, bootstrap and continuation; detached and direct entry; live replacement and fresh-process restart. Where Full/Auto execute identical logic, parameterize policy-specific cases rather than duplicate whole suites.

Inject interruption before/after experiment completion, scoring preparation, cached score persistence, approval, decision publication and bootstrap publication. Check:

- A completed experiment with interrupted scoring resumes scoring against the same candidate/evaluator without rerunning the experiment.
- A cached verified score is reused; a mismatched score is not.
- Acceptance survives a later iteration's interruption; decision replay does not duplicate frontier/history entries.
- Stop and provider exhaustion do not turn unfinished work into objective rejection or automatically restart research.
- Pending state remains intact when shutdown has already selected it as recoverable.
- No staging refresh or concurrent owner mutates the workspace during recovery/finalization.
- Missing private/evaluator/artifact evidence produces an explicit recovery-required result without speculative cleanup.
- Bootstrap crash recovery preserves the pre-entry boundary or completes an already-started publication.
- Remote cancellation is verified against actual owned job state; local process exit is insufficient evidence.

Status should report stop reason, accepted result, available resume point and any actual rollback separately. Existing `HitlRecoveryResult.restored_checkpoint_sha` can be populated even when pending work was preserved without restoration, so messages must not indiscriminately say “restored checkpoint.” Reuse the current status projection; no new status database is needed.

## Validation and limits

Targeted repository tests: **159 passed, 1 failed**.

| Tests | Result |
|---|---|
| Provider failure propagation, MCP retry classification, evaluator verification, workspace guard | 94 passed |
| AutoResearch review fixes, initial rule-maker repair recovery, phase state, managed ordinary report integration | 57 passed |
| Timeout recovery | 8 passed, 1 failed |

The failure is `test_run_worker_does_not_translate_sigterm_into_user_stop`: its mocked `_load_request` omits the now-required `workflow` field, causing `KeyError` before its intended assertion. The real loader supplies legacy defaults and validates current requests. This is a stale fixture to fix, not evidence that real launch requests omit the field; the test's intended coverage remains unverified until repaired.

Some initial test invocations stalled importing local dependencies. The timeout suite ran with optional PyGithub import disabled only in that test process; GitPython remained real. No dependencies were installed. This is a targeted suite result, not a claim that the complete repository suite passed.

Additional probes:

- Six isolated control-flow cases with fake provider/Git boundaries: initial stop cleanup, non-HITL accepted-state persistence, scorer transport failure, missing public evaluator, HITL pending-command restart, and continuation-only restart.
- Two real-Git initial-scoring stop cases, prepared and cached, demonstrating removal of candidate and pending state by outer shutdown recovery. These reused the repository fixture and mocked only provider work/stage dispatch and unrelated Modal sweep.

No real provider sessions, GPU training, Slurm jobs, Modal tasks, or concurrent-launch stress runs were performed. Backend and race findings are explicitly source-derived. No production implementation was changed by this review.
