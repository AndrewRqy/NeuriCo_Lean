# Time-budget stop and resume design

Status: proposed design; no production changes. Reviewed current-main revision `d71bace2cc77cab404ac205ae5c68ad2fe46f25f` on branch `time-budget`.

This refines section 5 of `TIME_BUDGET_CORE_PLAN.md`. Budget exhaustion should extend the existing run-control and recovery machinery. It must not introduce a second rollback engine or reinterpret every interruption as an unsuccessful experiment.

## What the current implementation actually does

| Existing component | Observed behavior | Consequence for the design |
|---|---|---|
| `src/core/hitl_run_control.py:86` | Launch-scoped durable stop request and thread-visible event. | Generalize the existing control to carry a reason; retain compatible HITL entry points. Do not add another independent stop file/watchdog. |
| `src/cli/hitl_run_worker.py:116` | Acknowledges stop after recovery; maps reasons to user stop or provider unavailable. | Add budget exhaustion as its own cause, and distinguish research termination from cleanup success. |
| `src/core/pipeline_orchestrator.py:509` | Managed initial runs restore the active stage boundary; completed stages retain their work. | Reuse stage boundaries, including the case where no accepted research baseline exists yet. |
| `src/core/hitl_autoresearch.py:849` | Recovery can classify pending worker requests or frontier decisions without rolling them back. Other interrupted attempts require verified snapshots before restoration. | Preserve these distinctions. A recovery classification is not proof that the public workspace has been restored. |
| `src/core/hitl_runtime_state.py:826`, `src/core/hitl_autoresearch.py:1785` | Frontier decisions have durable, replayable steps: prepared, idea logged, frontier finalized, mirrored, completed. Initial-root publication has its own durable transition. | Complete already-recorded decisions through the existing transition logic; do not invent a new acceptance decision during cleanup. |
| `src/core/hitl_autoresearch.py:1338` | Unscored, nonterminal attempts are automatically relaunched. | Budget exhaustion must bypass replacement and retry behavior. |
| `src/core/autoresearch.py:1635`, `:1395` | Ordinary controller holds the newest best SHA in memory; continuation writes saved state after the full loop returns. Broad exception handlers convert proposal/scoring errors into rejected attempts. | Persist progress per completed decision and explicitly propagate stop causes before generic failure handling. |
| `src/core/hitl_manager_react.py:817` | Manager stop cancels the active backend, joins briefly, and closes its bridge; rollback has turn invalidation machinery. | Reuse cancellation and invalidation. A timed join alone must not authorize state restoration while a late callback can still write. |
| `src/core/hitl_git_state.py:35`, `:274` | Private rollback excludes live launch/control data. | Budget identity/deadline and outstanding job ownership belong to run lifecycle, outside all checkpoint restoration. |
| `tests/test_hitl_timeout_recovery.py:31` | Scheduler SIGTERM intentionally differs from explicit SIGINT stop. | Preserve that distinction. On restart, read the original deadline before admitting recovery work that launches a worker. |

The review also confirmed that local process-group termination is not sufficient for Slurm/Modal job cancellation. Modal has an existing resource sentinel and app-stop helper; its teardown deletes the environment and is deliberately gated on artifact retrieval. Slurm's skill describes held submission and job bundles, but the core artifact helper only moves those bundles.

## One stop protocol, with separate cause and recovery status

Extend the existing control abstraction for ordinary and HITL runs. A budget policy requests a stop through it; it does not perform its own rollback. Preserve launch-scoped request identity so old stop requests cannot affect a later launch. Separately persist a research-budget identity that survives launch replacement.

The result should represent three independent facts:

- Why research ended: for example `budget_exhausted`, `user_requested`, or `provider_unavailable`.
- Whether cleanup is complete, pending, or requires recovery.
- Which accepted checkpoint/frontier and artifacts remain available, if any.

Do not turn budget expiry into `terminal_failure` merely to escape a retry loop. Add an explicit stop outcome and propagate it through the existing result/exception boundaries. A stage timeout with research time remaining can keep its existing retry policy. A stage timeout caused by the total budget cannot.

Persist the stop cause before cancellation and retain it even if cancellation or restoration fails. Do not overwrite it with a generic failure message. A run may be budget-exhausted, have a useful accepted result, and still require remote cleanup.

## Shutdown ordering

1. Close admission for proposals, workers, manager deliberation, repairs, retries, and evaluations. Start this at the research cutoff, leaving the configured cleanup reserve inside the total allowance.
2. Invalidate active request generations and cancel owned local/backend work through existing controls. Prevent late manager/worker responses from changing research state. Stop remote jobs as well as local launchers.
3. Confirm that no owned execution can still mutate the workspace. If confirmation fails, retain recovery markers and report cleanup pending; do not race a rollback against live writers.
4. Classify durable state using existing stage/attempt/frontier recovery information, then perform the appropriate bounded finalization below.
5. Persist the exhausted outcome, accepted-result reference, workspace recovery classification, and any outstanding cleanup. Retain the original deadline.

The reserve is not extra research time and is not a promise that arbitrary I/O will always finish. If cleanup exceeds the deadline, continue necessary cancellation/recovery without restarting research; report the overrun and unconfirmed resources honestly. Strict remote compute limits also require backend-side enforcement when the local coordinator dies.

## Finalization depends on the interruption point

| Durable state at expiry | Required behavior |
|---|---|
| Between completed attempts | Retain the current accepted state; do not launch frontier maintenance or a proposer. |
| Managed initial stage incomplete | Reuse the existing stage rollback boundary. Report no accepted baseline if none has been established. |
| Ordinary proposal, experiment, or scoring incomplete | Record an interrupted attempt with cause `budget_exhausted`; restore the accepted parent after writers stop. Do not manufacture an objective failure score or run scoring to finish the attempt. |
| HITL pending worker command with valid continuation | Stop its live execution but retain the durable request/continuation, blocked by the exhausted budget. Expose the accepted frontier reference separately. Do not claim the working tree is clean or silently discard the pending request. |
| HITL durable acceptance/rejection decision or initial-root publication pending | Replay only the already-recorded deterministic publication steps. No manager call, new evaluation, or new acceptance decision. If this cannot finish, retain the transition for cleanup-only recovery. |
| Other interrupted HITL attempt with complete rollback evidence | Reuse existing checkpoint/private-state/whiteboard recovery. |
| Missing snapshots, corrupt state, or unconfirmed active jobs | Leave recovery evidence intact and report recovery required. Do not guess a checkpoint or clear the marker. |

Finishing a saved decision is different from accepting an unreviewed candidate after time expires. The decision must have been durably recorded before the research cutoff. Make the cutoff/admission check and transition preparation ordered under the existing state lock, so deadline races have a defined outcome.

HITL stores an accepted frontier and a manager-selected node; it does not reduce to ordinary AutoResearch's single numeric best. Preserve those semantics. Git checkpoint preservation also does not guarantee recovery of ignored or overwritten model weights. Result reporting must distinguish accepted research state from validated model artifacts; do not promise the latter without an artifact contract.

## Make ordinary progress durable without copying HITL's state machine

Use the existing attempt history/decision record and `autoresearch_state.json`. Persist the accepted pointer and iteration progress after every completed decision, before admitting another attempt. Recovery must reconcile a decision written just before a crash with an older summary pointer; one atomic file update alone cannot make multiple files transactional.

Prefer a small extension of existing decision metadata with a stable attempt identity and completion state, plus deterministic reconciliation. Do not create a second frontier or another independent best-result store. Do not treat an arbitrary Git HEAD or incomplete candidate as accepted. Retain existing safeguards against deleting unrelated untracked files.

## Continuation rules

Load the original budget before any continuation path can dispatch an agent, scorer, or manager call.

- If time remains, recover the existing stage/attempt and continue under the original deadline. Downtime consumes wall time.
- If the research cutoff has passed, permit cancellation and deterministic state recovery only. Pending worker execution stays blocked. A new launch ID does not grant time.
- An explicitly requested new research session may start from an accepted result with a new budget identity. This is not an automatic continuation reset and need not be part of the first implementation.
- Legacy runs with no budget keep their existing behavior; applying a new budget requires an explicit budget start rather than inferred old timestamps.

Keep the budget outside both public and private rollback. Retaining it only in launch status is insufficient because a continuation has a new launch identity.

## Remote cancellation: adapt existing ownership records

Use a narrow backend contract to register owned execution, request cancellation, and confirm terminal state. Derive job limits from the single deadline. Backend ownership metadata is necessary; a separate backend budget counter is not.

- Local: strengthen the shared process supervisor, including descendants that outlive a terminated parent; reuse manager backend cancellation and stale-response invalidation.
- Slurm: make the existing held-submission/job-bundle approach runtime-enforced, persist ownership before release, cancel exact owned IDs, and confirm their terminal state. Handle interruption between submission and recording with a run-scoped submission identity that can be reconciled. Never cancel all jobs for a user. Queue delay must not grant extra wall time.
- Modal: reuse the existing sentinel and lifecycle app-stop primitive, adding run association and cancellation status where needed. Stop compute without deleting volumes/environment. Preserve the pull-before-destructive-teardown safeguard. Verify that the chosen primitive covers the actual execution mode before claiming cancellation support.

Ownership must survive rollback and stage cleanup. Missing/unreachable cancellation confirmation remains visible as pending cleanup. A backend cannot claim strict deadline support until its submission race, cancellation, and coordinator-loss behavior are covered. Deliver local support first if necessary, with remote limitations explicit.

## Implementation order and acceptance checks

1. Generalize stop reasons and the existing shared execution/control path; add no-retry handling at ordinary/HITL boundaries.
2. Add durable shared budget identity/deadline and gate all continuation dispatch.
3. Make ordinary decisions incrementally durable; extend existing recovery classification for budget stops and frozen pending work.
4. Integrate owned-job cancellation through the current backend lifecycle records.

Use fake clocks, short subprocesses, temporary repositories, and fake remote backends. Required cases include expiry during each stage; no retry/replacement after exhaustion; an accepted improvement surviving interruption in the next attempt; deadline unchanged after restart/rollback; pending HITL worker not relaunched after expiry; each partial frontier publication replayed exactly once; late callbacks unable to mutate restored state; missing recovery evidence retained; remote cancellation failure visible; and unbudgeted/SIGTERM behavior unchanged.

This focused review is based on source inspection, including existing regression test definitions. No new tests or remote jobs were run for this design refinement. Earlier audit probes and their validation limitations remain documented in `TIME_BUDGET_IMPLEMENTATION_REVIEW.md`.
