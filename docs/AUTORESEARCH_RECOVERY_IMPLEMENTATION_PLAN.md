# AutoResearch recovery: implementation plan

Updated 2026-09-29 on `time-budget`. Intended destination: NeuriCo main. Audit baseline: `d71bace2cc77cab404ac205ae5c68ad2fe46f25f`.

Status: the local recovery review is closed after steps 1 and 2 and a follow-up source check found no additional confirmed scoring/replay defect. Later investigation sections below are deferred verification items, not confirmed bugs. Real remote lifecycle verification remains outstanding. Production fixes are committed locally as `2d31512`; all test changes remain uncommitted at the user's request. No deadline implementation is included. This plan governs the recovery prerequisite to [TIME_BUDGET_CORE_PLAN.md](TIME_BUDGET_CORE_PLAN.md); the [original audit](AUTORESEARCH_RECOVERY_REVIEW.md) remains historical evidence.

## Scope and working agreement

Focus exclusively on the manager-based AutoResearch modes exposed by the CLI and webpage:

| Mode | Interface | Recovery owner |
|---|---|---|
| Full HITL | CLI and webpage | Existing managed pipeline, `HitlAutoResearchController` and runtime state |
| Auto | CLI and webpage | The same managed pipeline, controller and runtime state; approval authority differs |

Cover initial baseline construction, iterative research and continuation reached by these flows, including direct/detached entry differences and their existing local/remote execution backends. Trace actual call paths before including standalone bootstrap commands. A HITL name alone does not establish that a command belongs to this delivery.

The outdated plain `--autoresearch` comparator loop is excluded. Do not add durability, scoring handoffs, per-decision persistence or manager behavior to it. Ordinary unscored research and benchmark-specific adapters are also excluded. Shared helpers may change where necessary for the supported flows; preserve other callers through compatibility checks.

Before each implementation step:

1. Explain the reproduced problem and its root cause in the supported paths.
2. Identify the existing mechanism that should handle it, the smallest change needed, and its regression test.
3. Discuss the proposal with the user and obtain agreement before editing code for that step.
4. Implement and validate the agreed change, then report the outcome before proposing the next step.

This plan is not blanket approval to implement all later steps. Stop and discuss important new decisions or findings. Platform scope is macOS/Linux. The user explicitly deferred Windows support to a separate later fix; it is not a gate for this delivery.

## Design constraint and recovery contract

The existing managed system already supplies checkpointing, private snapshots, durable requests, worker continuation, isolated scoring, manager review, frontier publication and replay. The task is to fix demonstrated integration defects in that system, not build another mechanism or make the legacy controller match it.

Under the existing workspace lease, quiesce or reconcile owned writers and inspect durable state before changing research inputs. Prefer, subject to existing validation:

1. Retain finalized results or complete an already-recorded publication.
2. Preserve a matching pending request and any saved scorer outcome for its manager handoff.
3. Reuse a valid continuation or completed-stage boundary.
4. Restore the nearest verified existing phase boundary.
5. Restore the accepted parent only when no finer safe boundary exists.

Absence of finer progress can justify the existing fallback. Corrupt or inconsistent evidence required for a proposed restoration must be retained and reported, not bypassed to perform destructive recovery. Validate restoration dependencies before modifying public or private state. Keep interrupted attempt evidence where existing history permits it. Git checkpoints alone do not preserve arbitrary ignored model weights.

Scoring outcomes, including failures, belong to the manager loop. A saved `isolated_scoring.status == "scored"` means an outcome was captured, not that it succeeded or was accepted. Before outcome persistence, reconnect the existing scoring handoff. After persistence, resume manager review without rescoring. After a recorded decision, replay that decision without rejudging. Recovery introduces no new stop/reject/retry policy for scorer outcomes.

The stop cause is separate from recovery selection. Explicit cancellation and exhausted provider retries end the current execution; scheduler/process loss is reconciled on a later invocation. Preserve the accepted frontier and valid unfinished progress independently. Intentional manager rejection/repair retains its existing semantics. Total-time accounting is subsequent work.

## Existing mechanisms to reuse

| Responsibility | Existing owner | Intended repair boundary |
|---|---|---|
| Ownership and cooperative stop | `hitl_workspace_run_lease`, `HitlRunStopControl`, runner ownership wrapper, detached worker finalizer | Keep the same ownership/control through preparation, execution, recovery and final status; reuse one request identity. |
| Initial resume | `PipelineOrchestrator.prepare_initial_resume`, stage rollback descriptors | Call existing validated selection instead of unconditional rollback during shutdown. |
| Iterative recovery | `recover_interrupted_hitl_attempt_if_needed`, existing attempt/history records | Reconcile before staging; preserve validated pending inputs and existing evidence. |
| Request/worker continuation | `HitlRuntime`, runtime state, `resume_pending_worker_command`, `run_worker_with_replacements` | Reconnect matching work; compare live and restarted paths without weakening provenance checks. |
| Scoring and manager review | Existing source fingerprints, isolated scoring, `review_initial_scoring_result`, `review_frontier_candidate` | Preserve and reconnect the existing handoff; no decision policy change. |
| Frontier/baseline publication | Existing transition records and publication replay | Complete recorded transitions exactly once; retain accepted-result authority. |
| Local cleanup | Existing subprocess supervisor and process-group termination | Stop owned writers before restoration, including descendants after timeout/stop. |
| Remote cleanup | Existing backend submission/workspace records and cancellation/artifact utilities | Establish actual ownership, reconcile/cancel the submitted jobs and preserve required output. |

No new recovery engine, checkpoint database, job registry, generic scheduler or parallel status store. Add no persistence fields unless a reproduced in-scope defect proves the current record insufficient and the change has been discussed.

## Disposition of the original audit

The audit covered more paths than the corrected delivery. Its ten findings are not ten implementation requirements.

| Finding | Disposition |
|---|---|
| R1 — initial shutdown bypasses preservation | In scope; working patch reuses `prepare_initial_resume`. |
| R2 — legacy scoring durability | Excluded. Managed scoring already has durable handoffs and manager review; test their recovery integration without redesigning them. |
| R3 — legacy accepted pointer saved only after loop | Excluded; no legacy per-decision persistence work. |
| R4 — ownership/control lifetime | In scope only for supported managed entries and necessary shared helpers. |
| R5 — live replacement versus process restart | In scope for investigation; different evidence may justify different safe behavior. Do not assume every difference is a bug. |
| R6 — preparation/recovery ordering | Managed iteration portion in scope; legacy portion excluded. Standalone bootstrap changes require reachability review. |
| R7 — legacy sealed-evaluator reconnection | Excluded. Existing managed evaluator validation remains authoritative. |
| R8 — validate rollback dependencies before restoration | Managed initial recovery in scope; bootstrap portion conditional on supported/shared reachability. |
| R9 — evidence retention and reporting | In scope for interrupted managed attempts; no new model-artifact subsystem. |
| R10 — remote ownership/cancellation | In scope where current managed modes use those backends; remote guarantees require backend validation. |

## Current working patch: retain, remove or review

Step 1 dispositions below have been applied. No later implementation step has begun.

**Relevant changes already present:** initial stop preserves prepared/cached scoring through existing resume selection; detached ownership lasts through finalization/status; exact-request nested lease reuse and direct managed stop control; initial rollback dependency validation before destructive actions; managed iterative recovery before staging; interrupted attempt logs retained with accurate incident reporting; existing process-group cleanup also runs after stop/timeout.

**Removed after approval:** legacy stop propagation and bootstrap restoration additions in `src/core/autoresearch.py`; legacy-only workspace ownership/control wiring and `_run_stack` plumbing in `src/core/runner.py`; standalone bootstrap preparation/backup-order changes; five test cases dedicated to those excluded additions. `src/core/autoresearch.py` and `tests/test_hitl_autoresearch_review_fixes.py` now match the branch baseline. The mixed runner recovery test file retained its three managed tests at cleanup; step 2 subsequently expanded their coverage. Legacy fresh-GitHub setup/pull gaps are not follow-up requirements.

**Bootstrap trace:** the detached CLI/web worker dispatches `hitl_autoresearch` or `hitl_continue_autoresearch`. Fresh managed research calls `run_fresh_hitl_autoresearch_initial_node`; it does not call either standalone bootstrap constructor. Runner mode validation makes those bootstrap commands mutually exclusive with the managed research modes. `_rollback_bootstrap_prepublication_boundary` is used only by the standalone managed bootstrap constructor, so its working change was also removed. This restores baseline bootstrap behavior; it does not claim its audited defects are fixed.

**Windows deferred by user:** removed the Windows byte-lock adapter and its three test cases. Restored the existing POSIX `fcntl` lock operations and platform check; retained exact-request nested lease reuse for the managed lifecycle. Windows support is separate future work, not an incomplete part of this patch.

No scorer acceptance-policy changes were implemented. The earlier proposed scorer stop-versus-reject question was withdrawn.

Scope-cleanup baseline: **317 tests passed in 10.74 seconds** across `tests/` on macOS; `git diff --check` passed. Starting from the broader 325-test patch, five legacy/bootstrap cases and three Windows adapter cases were removed. Optional PyGithub was disabled only in the test process; GitPython and real temporary Git recovery remained enabled. No real provider, GPU or remote jobs ran. Linux was not separately executed during this check. At this validation point, no commits or PR had been created.

## Ordered delivery

### 1. Review and remove out-of-scope working changes — completed

**Problem/root cause:** the earlier plan treated the obsolete comparator path as a supported mode, leading to unrelated lifecycle and bootstrap edits.

**Minimal proposal:** classify each existing hunk by supported call path; remove legacy-only additions and their dedicated tests, retain relevant managed/shared fixes, and resolve conditional bootstrap/Windows scope explicitly. Preserve unrelated work and historical audit evidence.

**Result:** user approved proceeding; the call-path review and selective cleanup above are complete. The full remaining test suite passes. Shared managed helpers were retained. The later user instruction deferred Windows support, so its adapter and dedicated tests were also removed.

### 2. Validate and finish managed lifecycle and restoration ordering — completed

**Problem/root cause:** correct recovery helpers can be bypassed by outer cleanup, run after ownership ends, or see inputs already modified by preparation. Restoring public files before validating required private evidence can leave a partially restored workspace.

**Minimal proposal:** verify the retained fixes at actual managed entry/finalization boundaries; change only any remaining reproduced integration defect. Keep one lease/control through finalization, use the existing initial resume selector, reconcile iterative state before staging, and validate required restoration references first. Confirm owned local writers are stopped before restoration.

**Validation:** Full/Auto prepared and cached initial-score interruptions; direct/detached stop and provider exhaustion; competing launch during finalization; missing private state leaves public files intact; retained pending inputs survive staging; a surviving child cannot write after cleanup. Keep current scheduler-signal semantics.

**Additional root cause reproduced:** manager-host cleanup was attached to individual execution branches. A provider-setup exception after host startup bypassed those branches; a final status-write exception skipped the subsequent `host.stop()`. Eight regression cases reproduced missing shutdown or recovery before shutdown across fresh/continue and Full/Auto.

**Minimal fix applied:** register the runner-owned host's existing `stop()` method immediately after successful startup with an `ExitStack` supplied by the existing managed ownership wrapper. Close this host scope before outer recovery and run-lease release. Remove the three branch-local shutdown calls. Caller-supplied hosts retain their existing external ownership. No new stop control, host shutdown implementation or recovery policy was added. If host shutdown raises, the direct wrapper does not proceed to destructive recovery.

**Verification:** the initial 29 targeted tests passed after the fix. Expanded coverage includes provider setup failure, final status failure and host shutdown failure for Full/Auto fresh/continue; pending request and frontier transition preservation before staging for CLI/web Full/Auto; ownership through recovery and final stopped-status writes for user stop/provider-unavailable requests across both interfaces/modes. Existing real-Git initial-score preservation, missing-private-state and real POSIX child cleanup tests also pass. The complete suite now reports **343 passed in 13.38 seconds on macOS**; `git diff --check` passes. Runner failure tests inject a fake host/provider at the module boundary, avoiding optional terminal UI dependencies; they verify runner lifecycle integration, not real provider or UI sessions. Optional PyGithub remains disabled only in the test process. Linux and real remote lifecycle execution remain unverified. This step does not establish remote-safe shutdown or complete the later replay/continuation investigations.

### 3. Verify the existing request, scoring and decision replay across modes — next discussion

**Problem to investigate:** durable state can still be lost or repeated if a caller reconnects the wrong phase after interruption. This is a verification step, not an assumption that the managed loop lacks persistence.

**Minimal proposal:** exercise the existing managed handoffs from CLI/web dispatch through recovery. Fix only a demonstrated missed call, ordering error or incorrect classification. Reuse scorer outcome caching and manager/frontier replay without extracting a replacement scoring framework.

**Validation:** interruption before/after score persistence; saved successful and failed outcomes resume manager review; interruption after decision persistence replays once; accept A then interrupt B retains A and valid B progress; repeated recovery does not duplicate review/publication/accounting. Switching Full/Auto retains request identity and lineage.

### 4. Resolve demonstrated live/process recovery differences and reporting gaps

**Problem to investigate:** a live worker may have evidence a restarted process lacks. Compare identical durable evidence before deciding that their differing recovery behavior is defective.

**Minimal proposal:** reuse existing continuation validation and phase boundaries where equivalent evidence supports reuse. Keep conservative fallback when no finer valid boundary exists, retaining attempt evidence and explaining the actual rollback. Use existing status projections; do not introduce speculative tool-call checkpoints or arbitrary session resume.

**Validation:** equivalent-evidence fixtures across Full/Auto and live/process restart; stale or mismatched continuation refused; finalized response takes precedence over late worker failure; evidence retained on fallback; reporting distinguishes pending-work preservation from restoration. Verify on macOS/Linux; Windows support is outside this delivery.

### 5. Verify owned remote job cleanup in supported flows

**Problem/root cause:** ending a local agent does not necessarily end its submitted remote job; restoring or deleting a workspace with an active writer is unsafe.

**Minimal proposal:** trace actual managed submission/cancellation paths, reuse durable backend ownership records, reconcile jobs before resubmission, and confirm cancellation/quiescence before destructive recovery. Preserve required output through existing artifact utilities. Persist a missing submitted identifier only in its existing backend record when needed and after discussion. Do not infer ownership from a job name or introduce a generic registry.

**Validation:** backend tests for exact ownership, cancellation/preservation/teardown ordering, uncertain submission, failed cancellation and retained metadata; controlled real Slurm/Modal lifecycle checks before declaring remote-safe recovery. An unresolved remote writer remains a recovery block, even while local work is validated.

## Completion and subsequent budget work

Recovery work is complete when the applicable managed defects are resolved, each retained fix has meaningful regression coverage, supported entry/mode combinations preserve equivalent validated progress, and unresolved platform/backend limits are explicitly recorded. Local tests alone cannot establish remote completion. Excluded legacy findings are not delivery gates.

Keep existing on-disk authorities, atomic write/retention conventions and safe behavior for older workspaces. Do not infer completion from missing markers or migrate uncertain evidence destructively. No deadline implementation or benchmark adapter belongs in these recovery fixes.

After this recovery work, discuss the small shared deadline policy separately. It should request `budget_exhausted` through the existing lifecycle, preserve the original deadline across continuation, and leave recovery and manager decisions with their current owners.
