# Managed AutoResearch time budget: reviewed minimal design

Status: per-Start optional CLI/web input, launch-scoped deadline persistence, managed YAML filtering and current runtime budget context are committed locally as `d3ec644`. The verifier-only cancellation fix is implemented locally on 2026-09-30 and remains uncommitted. The earlier YAML-based workspace deadline has been replaced. The blocked-worker-input issue and remote cancellation verification remain deferred; this is not yet a strict end-to-end runtime guarantee. Platform scope: macOS/Linux; modes: Full HITL and Auto through CLI/web. Plain `--autoresearch`, standalone bootstrap, Windows support and benchmark adapters are excluded.

This document supersedes the earlier budget proposals. Historical reviews remain evidence, not additional requirements. Before each implementation step, discuss the concrete gap, existing mechanism to reuse, smallest change and verification. Test changes remain local and uncommitted at the user's request.

## Foundation review: existing mechanisms to reuse

| Existing capability | Source | Finding |
|---|---|---|
| Duration input | `ideas/schema.yaml`, `src/core/idea_manager.py`, `src/templates/prompt_generator.py` | `idea.constraints.time_limit` exists in seconds. Schema/prompt defaults mention one hour; idea validation does not establish a runtime deadline. Disabling the implicit default does not suppress an explicit YAML value. Full HITL/Auto must instead use optional CLI/web launch input exclusively. Validate that input as a positive integer number of seconds, excluding booleans. |
| Shared lifecycle | `src/core/runner.py`, `src/cli/hitl_run_worker.py` | Direct/detached managed entries share the workspace lease and active stop control. The owned manager stops before outer recovery and lease release. Initialize the budget once inside that ownership lifetime. |
| Cross-thread stop visibility | `src/core/hitl_run_control.py` | Active control has both a context variable and a process-level fallback; existing manager/scoring threads can already find it. Reuse this, without another global/context registry. |
| Persistence outside rollback | `src/core/hitl_paths.py`, `src/core/hitl_git_state.py`, `src/core/autoresearch.py` | `.neurico/hitl/control/` holds runtime control records and is excluded from private restoration and public checkpoints. Prefer it over the earlier proposed `.neurico/run_budget.json`. Confirm survival with real-Git tests, including older snapshots. |
| Worker supervision | `src/core/agent_runner.py:run_prebuilt_cli_agent` | Existing operation timeout, stop polling and process-group cleanup. Managed resource finder, proposer, rule maker and experiment/scoring workers use shared execution paths. However, prompt writing happens synchronously before polling starts; it must also be bounded for budgeted execution. |
| Manager execution | `src/core/hitl_manager_react.py:_send`, `_send_once`; `src/interactive/llm_backend.py` | Backend supports timeout/cancellation; manager supplies `timeout_seconds=None` and waits on an unbounded queue read. MCP-readiness retries have separate counters and can keep extending startup windows. These must consume the same remaining allowance. |
| Verifier | `src/agents/eval_verifier.py:_call_verifier_api` | Already has an end-to-end async timeout; cap it from the run allowance. Do not introduce a new verifier supervisor. |
| Stop propagation and recovery | `src/core/hitl_run_control.py`, `src/core/hitl_stage_runtime.py`, `src/core/hitl_autoresearch.py`, `src/core/pipeline_orchestrator.py` | Cooperative stop checks and pending-request/frontier preservation already exist. Add exhaustion as a stop cause; do not add checkpoint or scorer-decision policy. |
| Status and agent context | `src/cli/hitl_run_worker.py:_finalize_stopped_run`, `src/core/hitl_workspace_view.py`, manager `_messages`, existing worker prompt builders | Existing stop reporting distinguishes provider unavailability from user stop. It needs an exhaustion reason and a deadline projection. Existing prompts need remaining time rather than repeated full original durations. |

The root gap is missing **run-level time ownership**: operation caps restart at each call and do not account for baseline work, manager calls, retries, replacements or continuation together. Existing recovery is not the owner of that policy.

## Current agreed contract

1. **CLI/web launch input is the sole authority.** Full HITL/Auto accept an optional total duration for each explicit Start. Blank/omitted means no run time limit. Do not default from YAML, schema, earlier runs or saved research plans. YAML time constraints must not become active budget instructions in these modes.
2. **One deadline per Start.** Reuse the existing launch `request_id`. A later explicit Start can choose a different duration or no limit, even when continuing the same research. Automatic retries, worker replacements, iterations and reattachment within the same request retain that request’s original deadline. Research continuation and budget identity are separate concerns.
3. **Start after validation and ownership.** Initialize once under the existing workspace lease, before run preparation, resource staging or manager startup. Baseline work and all iterations share the same allowance. This does not establish alignment with an external benchmark harness’s allocation start.
4. **Count wall time.** Preparation, provider calls, experiments, scoring, retries and human waits consume the allowance. Reattachment to the same request counts elapsed downtime. Live remaining time must not increase on a backward wall-clock adjustment. No pause accounting or per-stage allocation.
5. **Existing operation limits remain caps.** Use the smaller of an operation’s existing cap and the remaining run allowance. An unlimited operation is bounded when the run has a budget. No run budget does not remove existing operation/provider/backend limits. Iteration count remains an independent stopping condition.
6. **Expiry uses existing stop/recovery.** Preserve `budget_exhausted`; do not start another research call or retry. Existing deterministic cleanup/recovery preserves accepted results and pending progress. No new rollback or scoring policy. Report cleanup failure separately.
7. **Preserve source and history.** Ignore YAML time configuration in the managed runtime without deleting it from the submitted idea. Preserve other constraints and reviewed research artifacts. Historical prompts/plans cannot override the current launch budget, including an explicitly unlimited launch.

## Pre-change review: how YAML could contradict the launch budget

Confirmed in source and local prompt rendering (no provider calls):

| Path | Current behavior | Minimal correction |
|---|---|---|
| `runner.py` managed initialization | Reads `idea.constraints.time_limit` directly. | Pass validated launch input to existing control; no YAML fallback. |
| `hitl_run_control.py` saved record | One immutable workspace deadline reloads across new Starts, even with omitted duration. | Scope the existing record to the existing request ID; a new Start selects its own optional budget. |
| Experiment execution prompt | `include_implicit_time_limit=False` still renders an explicit YAML value. Local probe rendered `12345 seconds`. | Supply managed runtime idea context without YAML time fields; retain ordinary-mode behavior. |
| Rule-maker plan/execution/review; experiment plan/review | Serialize the full idea into prompts. Local probes confirmed the explicit field in all three rule-maker phases; experiment plan/review have the same full-idea serialization in source. | Use the same filtered managed idea projection at the shared boundary, avoiding separate prompt-specific budget policies. |
| Resource finder | Does not explicitly render `time_limit`, but renders legacy `constraints.time`. | Suppress that recognized YAML time field in the managed projection too; preserve unrelated compute/money constraints and research prose. |
| Workspace `.neurico/idea.yaml` | Resource staging writes a copy with host paths removed, but retains time fields. Existing workspaces can already contain them. | New managed runtime copies must use filtered context. Do not rewrite reviewed inputs/history merely to erase old limits; current runtime instructions must make them non-authoritative. |
| Saved worker continuation and manager history | Replacement/resume can reuse saved prompts; manager rebuilds messages from persisted research/conversation. | Attach fresh launch-budget context at actual worker/manager dispatch, including replacements and resume, without changing saved request identity or reviewed evidence. |
| Proposer | No direct YAML time rendering; it reads public planning/report context, which may mention earlier limits. | Same current runtime authority; do not strip or rewrite research reports. |
| CLI/web launch controls | Existing forms/payload/request have iterations but no time input. | Add one optional launch field consistently through form, launcher, worker and runner. |

The root cause is that research specification and invocation policy currently share an idea object, while the new budget record has workspace rather than launch lifetime. Disabling only the runtime YAML read would leave conflicting agent instructions. Disabling only prompt defaults would leave both the explicit YAML value and the old persisted deadline.

The runner also writes idea metadata back to the submitted YAML. Do not mutate its time constraints in place: use a managed runtime projection at a boundary that keeps source metadata persistence intact. The managed GitHub path attaches a remote; the broad `add_research_metadata` call belongs to the other workspace setup branch. Do not change all metadata writers globally.

## Minimal implementation shape

### Small deadline value and existing control

Add a small deadline value/helper, owned by `HitlRunStopControl`, with remaining-time calculation, operation allowance and a serializable snapshot. It owns no thread, worker, scheduler, rollback or result selection. Place it with existing run-control code unless size/dependencies justify a small core module.

Reuse `.neurico/hitl/control/budget.json` and the existing atomic writer under the workspace lease. Bind its optional timing to the existing `request_id`; no new session ID, registry or per-agent budget. Preserve timing on reattachment to the same request. A new explicit Start establishes new timing or an explicit unlimited state, never inheriting the previous request’s deadline. Validate same-request records and configuration; do not silently reset active timing. The earlier uncommitted record format lacks request identity and must not be treated as a current launch’s budget. No periodically persisted countdown.

Use UTC timestamps for restart persistence and monotonic elapsed time within an invocation. Live operation allowance must not increase when the wall clock moves backward. Restart calculations rely on a trustworthy system clock. Runtime-created timing fields are not agent-editable research configuration.

Extend existing stop control to recognize expiration and request a reasoned stop. Preserve existing callers and first-established stop causes when user cancellation/provider failure races expiration. Make checks/record publication consistent across the already-shared threads; do not add a second stop channel. An expired record must be noticed before research dispatch even when no prior stop file exists.

### Bound existing execution paths

- Check admission before launching workers and manager/provider calls, including replacements and retries. Refresh allowance at each actual call, not once per iteration.
- Reuse supervisor stop polling and group termination. Bring prompt input delivery under its bounded lifecycle; otherwise a process that does not read stdin can prevent deadline checks. Account for logging/output draining too rather than assuming only quiet workers can overrun.
- Pass the allowance into existing manager/backend timeouts and cap MCP startup and retry waits. Replace the unbounded manager result wait with stop/deadline-aware waiting. Reuse cancellation and generation invalidation so late responses cannot mutate the recovered workspace.
- Existing managed manager providers select the Claude/Codex CLI backends. Do not redesign unrelated API manager modes. The reachable verifier API already provides an async end-to-end timeout and should receive the remaining cap.
- Preserve `budget_exhausted` through broad exception handlers. A budget stop must not become provider unavailability, objective scorer failure or an invalid attempt that triggers a retry. This is stop propagation, not a new scoring-outcome policy.
- Continue existing human-wait polling under the same control; Full HITL waiting does not pause the recommended wall-time clock.

Review existing cancellation grace when implementing execution enforcement. Do not add an arbitrary configurable reserve in this foundation or invent per-stage budgets. Cleanup does not allow additional research and may still fail or overrun; report this honestly. A coordinator timer alone cannot guarantee remote compute has stopped by an external benchmark deadline.

### Useful budget awareness

Render one runtime snapshot into existing manager context on each provider turn/retry and into each worker launch/replacement prompt: original total, remaining time at render, absolute deadline and effective operation allowance. Tell agents to choose work that fits and leave time for scoring and saving a valid result. For a long-running worker, the absolute deadline remains useful after its initial remaining-time snapshot becomes stale.

Basic exhaustion reporting uses existing CLI/web status. A live countdown and extra budget displays are deferred. No separate countdown writer, timer service, MCP tool, predictive scheduler or automatic allocation of experiment counts. Keep the backend deadline authoritative even if an agent ignores the prompt. Avoid presenting the original duration as a fresh allowance in downstream prompts.

## Remote limits are a separate integration gate

DSI Slurm's core wrapper creates/removes remote workspaces; this alone does not prove cancellation of submitted jobs. Modal has resource records, app-stop and artifact-aware teardown, but actual job ownership/cancellation coverage still needs verification. Cancelling a local CLI is not sufficient for either.

The first implementation can validate local processes. Do not advertise strict Slurm/Modal enforcement until their existing submitted-job records and backend wall-time/cancellation paths are connected to the same deadline and tested. Do not introduce a general job registry. A local compute setting alone must not be treated as proof that a worker never submits remote jobs through a skill.

Benchmark-specific start alignment, output packaging and adapters are outside this core mechanism. The ten-hour objective must eventually be checked against the benchmark's actual harness deadline and remote enforcement; a local ten-hour timer is not sufficient evidence of leaderboard comparability.

## Approved implementation step: launch-owned optional budget

Implemented below after user approval. Reuse the existing control, ownership, request transport and prompt dispatch; add no recovery or timing service.

1. **Launch input and lifetime.** Add one optional duration field to Full HITL/Auto CLI/web launch configuration, passed through the existing request into the runner. Show explicit units and default to no limit. Validate before starting work; do not read YAML to populate it. Bind the existing saved budget to request identity. Same request retains timing; a new Start may choose any valid duration or no limit. Update exhaustion advice to allow starting again with the user’s chosen budget.
2. **One managed idea projection.** Exclude `constraints.time_limit` and the recognized legacy `constraints.time` from managed runtime context before downstream prompt generation and new workspace contract writes. Keep the submitted YAML, other constraints, ordinary research and outdated standalone modes unchanged. Trace source metadata persistence so filtering cannot delete the original YAML settings.
3. **Current authority on every dispatch.** Use the same runtime budget snapshot in existing manager and worker dispatch context, including pending-request resume and replacements. State the current deadline/remaining allowance, or that this Start has no total time limit. Earlier YAML, workspace metadata and historical plans do not set this launch’s budget. Preserve saved request/continuation evidence and reviewed workspace fingerprints. This small context addition belongs in this step because otherwise removing the YAML runtime read alone leaves the contradiction unresolved; live countdown UI and broader awareness features remain deferred.
4. **Local regression verification.** Cover Full/Auto and CLI/web: YAML-only limits produce unbudgeted launches; conflicting YAML cannot override input; omitted input cannot inherit a prior Start’s budget; new Start accepts a changed budget; retries/reattachment within one request keep the deadline. Check explicit and default YAML limits are absent from new managed contexts, legacy `time` is suppressed, unrelated/source fields survive, ordinary behavior remains, and current launch context reaches resumed/replacement workers without changing pending-request identity. Include the case of an expired prior Start followed by a new unlimited Start. Keep all tests local and uncommitted.
5. **Review and stop.** Report the source-of-truth changes and actual test coverage. No commit/push without instruction. Discuss full execution enforcement as the following step rather than claiming this makes all blocking calls or remote jobs obey a hard deadline.

Historical foundation tests that asserted a workspace-wide deadline or YAML budget authority have been updated to this contract. Their earlier passing result is not evidence for the new per-Start behavior.

## First delivery result — 2026-09-29 (historical contract)

This result predates the per-Start requirement. Its YAML input and immutable workspace-wide budget semantics are superseded. The existing stop-control, clock calculation, recovery preservation and reason reporting remain reusable. No production code was changed during the subsequent source-of-truth review.

Implemented in existing owners only:

- `hitl_paths.py`: canonical `control/budget.json` path, reusing existing public/private rollback exclusions.
- `hitl_run_control.py`: lease-checked, immutable budget creation/loading; positive explicit duration validation; UTC epoch start/deadline persistence; remaining time bounded by both live monotonic and wall clocks; repeated attachment does not reset the clock. Existing control recognizes exhaustion, preserves existing stored stop causes and uses the existing exception/stop record. No separate budget class, service, background timer or active context was added.
- `runner.py`: initialize/reload once after managed request validation and before preparation; reject invalid iteration requests before starting the budget. Expired direct and detached runs stop before resource preparation or manager dispatch. Ordinary/legacy modes remain unbudgeted.
- `hitl_run_worker.py` and `hitl_workspace_view.py`: preserve `budget_exhausted`, including recovery failure, in existing status. The stopped view identifies exhaustion instead of suggesting ordinary continuation. No countdown UI was added.

Validation: **380 tests passed in 12.36 seconds on macOS** across `tests/`; `git diff --check` passed. New local tests cover absent/invalid/conflicting budgets, ownership, elapsed downtime, repeated attachment, backward clock changes, stop-cause compatibility, public/private Git rollback, expired Full/Auto CLI/web/direct/detached admission, basic status and cleanup failure. Optional PyGithub was disabled only in the test process as in prior validation; actual temporary Git recovery remained enabled. Linux and real provider/GPU/remote execution were not run. All test changes remain local/uncommitted. No new commit or push was made.

Limit: expiration is currently detected at existing cooperative checks and runner admission. Blocking manager waits, worker input delivery, operation timeout caps, verifier calls and remote job enforcement still require the separately agreed enforcement step. This is not yet a strict total-runtime guarantee or benchmark-ready enforcement.

## Per-Start delivery result — 2026-09-29

- CLI and web Start offer an optional time limit in whole seconds, initially blank for no limit. Ten hours is `36000`. The existing launcher/worker request carries `time_limit_seconds`; the direct runner CLI also accepts `--time-limit-seconds`. The shared runner accepts this option only for managed AutoResearch.
- The existing budget record now includes the launch request ID and records unlimited launches explicitly. A new Start replaces the previous launch’s timing under the workspace lease. Reattaching to the same request requires the same duration and retains the original deadline. A valid old record without launch identity does not impose its deadline on a new Start. Malformed records fail without being silently reset.
- The runner makes a managed runtime copy after source metadata persistence, removing `constraints.time_limit` and legacy `constraints.time`. Other research constraints and the submitted YAML remain intact. Newly written workspace contracts use the filtered copy. Reconnecting reviewed inputs skips rewriting the workspace contract, preserving pending-request evidence.
- Existing CLI worker dispatch, manager provider dispatch (including retries/compaction), and verifier API dispatch receive a fresh runtime budget note. It includes total/remaining time and the deadline, or explicitly no total limit. Historical YAML, saved prompts and research plans are not invocation policy. The note is added to outgoing context without rewriting saved continuation records or conversation history. Existing ordinary/legacy flows remain unchanged when no managed budget is configured.
- Exhaustion continues through the existing stop/recovery path and status reason. The stopped view now explains that another Start can use a new limit or no limit. No new recovery mechanism, timer thread, registry or countdown UI was added.

Validation: **427 tests passed in 12.07 seconds on macOS** across `tests/`; `git diff --check` and JavaScript syntax validation passed. Coverage includes CLI input, web-form interaction logic in a Node DOM harness, actual launch-request serialization/loading, detached worker forwarding, Full/Auto and CLI/web runtime entry, new unlimited Starts after expiry, same-request deadline preservation, backward clock handling, YAML filtering across managed worker phases, source preservation, actual subprocess worker/replacement context, manager dispatch, reviewed contract preservation and existing recovery regressions. Tests remain local and uncommitted. Optional PyGithub was disabled only in the test process; CLI input tests stubbed the unused native terminal renderer because this environment lacks `wcwidth`. Browser layout, real provider/GPU/remote execution and Linux were not tested. No commit or push was made.

## Active-stop verification — 2026-09-30

Focused local verification: **118 passed, 4 expected failures in 19.80 seconds**. Ten new active-path cases pass; four strict expected-failure cases reproduce two shared cancellation gaps under both user Stop and budget expiry. Tests are local/uncommitted. No production code changed during this verification.

Verified with real local subprocesses and simulated provider responses:

- An active worker stops through the existing supervisor without launching a replacement.
- The mechanical scorer and an unanswered human wait respond to the same stop control.
- Full/Auto runner expiry unwinds a manager wait, calls existing provider cancellation, and stops the manager before recovery. This also ends repeated provider-startup retries. The unbounded queue read inside manager dispatch alone is not evidence that another manager cancellation mechanism is required.
- A worker that never reads its input blocks `stdin.write` before the supervisor enters stop polling. Both budget expiry and user Stop are delayed. Tests kill/reap their own child during teardown.
- An in-flight verifier API call observes its own API timeout but not the shared run stop. Both stop causes leave that request waiting; tests release the simulated response during teardown.

Existing deadline persistence, recovery, provider-failure propagation and scheduler-signal regression tests also passed in the focused suite. Real providers and remote Slurm/Modal workloads were not exercised.

## Implemented plan: verifier cancellation only

Status: implemented locally on 2026-09-30 after user approval; uncommitted. The user deferred the blocked-worker-input fix. This step does not change worker prompt delivery, manager shutdown, remote backends or recovery policy.

### Root cause and existing mechanisms

The verifier's asynchronous API request already has an end-to-end `asyncio.wait_for` timeout, and `_call_verifier_api_async` closes its client in `finally`. However, the wait never observes the active run's shared stop control. In addition, three broad exception handlers convert `HitlRunStopRequested` into an API-failure result or an advisory unavailable report. Fault injection reproduced all three conversions; the existing timeout/client-cleanup test and conformance suite passed (29 tests).

Reuse the existing run stop control, `HitlRunStopRequested`, async task cancellation, API timeout, client cleanup and outer managed recovery. No new thread, timer service, cancellation registry, configuration or persisted state.

### 1. Make the existing API wait interruptible

File: `src/agents/eval_verifier.py`, `_call_verifier_api`.

- When running under the shared run control, check stop before submitting the request.
- Await the existing API coroutine as one task, checking shared cancellation at the existing 0.1-second polling cadence while it is pending. Keep the current end-to-end API timeout around the entire wait; do not restart that timeout at each poll or create a second budget calculation.
- On user Stop or budget exhaustion, cancel and await the pending task before propagating `HitlRunStopRequested`. Reuse the existing `finally: await client.close()`; do not add another client-close path.
- Check stop before accepting a completed response so a response arriving with an observed cancellation does not become a new verdict. Cleanup must not replace the established stop cause with an ordinary API error.
- Calls without an active run control keep their existing timeout behavior. An unlimited managed run must still respond to user Stop.

### 2. Preserve cancellation through the three existing handlers

Add a specific `except HitlRunStopRequested: raise` before broad failure handling at:

| File | Boundary |
|---|---|
| `src/agents/eval_verifier.py` | API-call handling in `run_eval_verifier` |
| `src/core/pipeline_orchestrator.py` | `_scoring_conformance_report` |
| `src/core/hitl.py` | `_scoring_conformance_report_for_review` |

This lets the existing stop path take over before a cancelled verification becomes a manager report. Ordinary API errors, invalid responses and advisory reports retain their existing behavior. The verifier remains advisory in managed review; the manager still owns research decisions. No scoring handoff or checkpoint-selection changes.

### 3. Verify the complete narrow change locally

Use simulated async API clients and existing run controls; no provider credentials or remote compute are needed.

- Normal response still returns normally; the existing API timeout still cancels the request and closes the client.
- User Stop in an unlimited run and budget expiry in a limited run interrupt an in-flight request, await cleanup and preserve the original stop reason.
- A stop already present prevents API submission. A stop observed as the response completes prevents publishing a new verdict/report.
- Inject the stop exception at each of the three boundaries and through the connected conformance-report chain: it escapes unchanged, without an unavailable advisory or further manager-review call. Existing cached conformance-report replay remains unchanged.
- Convert the two verifier expected-failure reproducers in `tests/test_budget_active_stop.py` into passing regression tests. Keep the two worker-input expected failures as the explicitly deferred issue.
- Run the verifier, conformance-report and active-stop tests first; then run the full local suite once the change is stable. Keep every test change local and uncommitted.

### Completion boundary

Done means the pending verifier request responds to both stop causes, its existing client cleanup completes, and cancellation reaches the existing managed stop/recovery flow without being reclassified as a verifier fault. No new mechanism is introduced. Report the results and stop for discussion before another implementation step; do not commit or push unless requested.

## Verifier cancellation delivery — 2026-09-30

Production changes are confined to three files:

- `src/agents/eval_verifier.py`: retain the active run control for the lifetime of the API call; check it before submission and during the existing async wait; cancel and await the request on stop while retaining the original end-to-end API timeout. Reuse the existing client-close `finally`. Preserve an observed stop if request cleanup raises. Pass `HitlRunStopRequested` through the verifier API error handler.
- `src/core/pipeline_orchestrator.py`: pass the stop exception through `_scoring_conformance_report`.
- `src/core/hitl.py`: pass the stop exception through `_scoring_conformance_report_for_review`.

No worker-input, manager-shutdown, recovery, remote-backend or scoring-policy changes. Existing advisory error reports and cached report replay remain unchanged.

Validation: focused verifier/conformance/active-stop tests: **126 passed, 2 expected failures**. Full local suite: **451 passed, 2 expected failures in 26.92 seconds on macOS**. The two expected failures are the explicitly deferred blocked-worker-input cases (user Stop and budget expiry); both verifier reproducers now pass. Added local regressions cover cancellation and client cleanup for both stop causes, cleanup errors preserving the stop reason, stop before submission, stop arriving with a response, normal managed completion, unchanged API timeout, all three exception handlers, and the connected report handoff. `git diff --check` passed. Tests use local processes and simulated API clients; no live provider or remote compute was invoked. Optional PyGithub was disabled only in the test process, consistent with prior validation. All test changes remain local/uncommitted. No commit or push was made for this fix.

## Deferred work

- **Worker input delivery:** deferred at the user's request. The local reproducer remains; production prompt delivery stays unchanged.
- **Remote verification:** separately exercise existing Slurm/Modal owned-job cancellation before claiming strict remote or benchmark deadline enforcement. This verifier-only fix makes no new remote guarantees.
