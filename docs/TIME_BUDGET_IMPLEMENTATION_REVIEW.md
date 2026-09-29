# NeuriCo time-budget implementation review

Date: 2026-09-29

Reviewed revision: `d71bace2cc77cab404ac205ae5c68ad2fe46f25f`, verified upstream `main`, checked out on `time-budget` in the isolated worktree.

Scope: the current research execution architecture as it affects time constraints, agent awareness, cancellation, retries, scoring, checkpointing, recovery, and remote compute. This is a design review, not a security audit of every NeuriCo feature. No production code was changed and no provider or GPU jobs were launched.

## Decision

NeuriCo has several useful time controls but no shared elapsed-time budget for an entire research run. The right change is to **consolidate existing execution paths and introduce one authoritative run policy**, not add a second AutoResearch runner or separate ordinary/HITL timers.

The earlier review examined the active older checkout (`489e1de`). This review uses current main. Current main already has a reusable wall-clock subprocess supervisor, process-group cleanup, durable HITL cancellation/recovery, and a bounded async verifier API. Those should be retained. The remaining integration is broader than adding a timeout to the AutoResearch loop.

## 1. Existing controls and ownership

| Surface | Current behavior | Interpretation |
|---|---|---|
| `ideas/schema.yaml:225`, `src/core/idea_manager.py:222` | Defines and validates `constraints.time_limit` in seconds. The schema advertises a 3,600-second default. | Declared task constraint; not a shared runtime counter. |
| `src/templates/prompt_generator.py:471` | Research prompts render the time constraint; ordinary prompts can insert an implicit one-hour limit when constraints exist. | Advisory text can differ from actual execution settings. |
| `src/templates/prompt_generator.py:1093` | Resource-finder context reads the legacy `constraints.time` field. | A separate prompt interpretation of task time. |
| `src/templates/prompt_generator.py:1139` | Comment prompts render comments and selected metadata, with no canonical time-budget input. | Later iteration workers do not consistently receive the declared time limit. |
| `src/agents/autoresearch_proposer.py:98` | Proposer context contains artifacts/history; no authoritative remaining-time snapshot. | No framework-supplied countdown. |
| `src/core/runner.py:280`, CLI options around 1757–1916 | Independent execution, resource-finder, rule-maker, scorer, proposer, trimmer, and paper timeouts. | Per-invocation caps, not total budget. |
| `src/core/autoresearch.py:1635`, `:1983` | Ordinary controller loops by iteration count; closures reuse the configured stage timeouts. | Repeating an iteration does not consume a common remaining-time allowance. |
| `src/core/hitl_autoresearch.py:1246`, `:1338` | Separate HITL controller manages frontiers and can repeat failed attempts until scored. | Iteration count is not even an upper bound on worker launches in HITL. |
| `src/core/runner.py:807`, `:1025` | HITL supplies `None` for major worker/scorer timeout arguments. | Deliberately unbounded calls under normal HITL behavior. |
| `src/core/agent_runner.py:223` | `run_prebuilt_cli_agent` supervises a subprocess, streams logs, polls timeout/stop, and manages its process group. | Primary subprocess enforcement mechanism to reuse. |
| `src/core/agent_runner.py:133` | Older `_run_cli_agent` still exists for standalone interactive agents and blocks on output without a timeout. | An additional execution path, not covered by improving only the newer helper. |
| `src/core/hitl_run_control.py:86` | Durable launch-scoped stop request, shared with threads through context/process control. | Reusable cancellation plumbing; presently represents user/provider stop rather than a time budget. |
| `src/interactive/llm_backend.py:93` | Provider adapters accept timeout parameters; MCP startup has its own deadline. | Transport/provider caps; must remain subordinate to a shared budget when enabled. |
| `src/core/hitl_manager_react.py:2375` | Manager calls explicitly pass `timeout_seconds=None`. | Worker-only enforcement would miss manager work. |
| `src/agents/eval_verifier.py:390` | `asyncio.wait_for` bounds the verifier API call and closes the client. | Working API-specific enforcement; reuse, do not replace with a shell timer. |
| `src/core/scorer.py:129` | Mechanical scorer delegates to the shared subprocess helper. | Already near the desired consolidation point. |
| `src/core/dsi_slurm_remote.py:72` | SSH creates/removes a remote workspace, with a connection timeout. | Workspace lifecycle, not remote job budget enforcement. |
| Modal templates and lifecycle helpers | Per-function durations, command timeouts, app stop and teardown helpers. | Remote job/transport controls, not one total research allowance. |

The schema's `constraints.budget` is a monetary amount. Manager context-token budgets, provider retry counts, UI polling intervals, lock timeouts, and network connection deadlines are also distinct quantities. They should not be converted into or mistaken for research wall time.

## 2. Important findings

### A. Some nominal timeouts are applied after the work has already finished

**Confirmed by a local subprocess probe.** The ordinary proposer reads stdout to EOF and only then calls `process.wait(timeout=timeout)` (`src/agents/autoresearch_proposer.py:345`). A silent or continuously running child can keep that read blocked beyond the configured limit. If it eventually exits normally and writes a proposal, the result can still be reported as successful.

The same source pattern appears in the ordinary resource-finder path, manifest trimmer, bootstrap rule-maker, and legacy monolithic runner. Their behavior was inspected, not individually reproduced. The HITL proposer path already uses the newer supervisor.

**Implication:** simply passing `min(stage_timeout, remaining)` into every existing function does not yet guarantee a deadline. Migrate reachable blocking-output paths to the existing supervisor while preserving their command, working-directory, logging, and result contracts.

### B. Shared subprocess supervision is reusable, but its cleanup is not a hard tree-wide guarantee

**Confirmed by a local subprocess probe.** The shared runner's main loop checks time while stdout is drained on a separate thread. However, on timeout it sends SIGTERM to the group, then escalates only if waiting for the parent times out. A parent can exit while a child ignores SIGTERM; that child is not necessarily killed in this branch (`src/core/agent_runner.py:327–350`). Clean-parent-exit cleanup uses a separate lingering-group helper, but the timeout branch does not.

The helper also writes the prompt to stdin synchronously before entering its monitoring loop, uses `time.time()` for elapsed timing, and permits cleanup grace after the requested limit. These are inspected edge cases, not all reproduced failures. A worker that changes session/process-group membership is another reason an external container/allocation limit remains useful for official hard compute limits.

**Implication:** strengthen the existing supervisor, use a monotonic clock for live elapsed-time accounting, bound blocking I/O, and budget cleanup grace. Do not build an independent watchdog in each agent.

### C. HITL has intentionally different stop/retry semantics

The top-level runner disables ordinary worker caps in HITL. Worker replacement loops and the retry-until-scored loop can launch additional work. Manager MCP readiness timeouts increase their startup window and do not consume the normal provider retry count (`src/core/hitl_manager_react.py:2316`; covered by an existing test). Manager reasoning calls themselves have no wall-clock deadline.

The configuration key `hitl_manager_max_request_provider_turns: 48` appears in `config/manager.yaml`, but a repository search found no runtime consumer of that key at this revision. The actual manager reads `hitl_manager_max_turns` and backend retry settings. The advertised request-turn setting must not be relied upon as a time cap.

**Implication:** with an explicit run budget, manager calls, replacement workers, retries, waits, and scorers must all consult the same policy. Without a budget, retain existing unbounded HITL behavior. Do not silently restore arbitrary per-stage caps to every HITL session.

### D. Prompt awareness is fragmented

The research prompt, resource-finder prompt, comment prompt, and proposer do not derive time information from one source. The initial prompt can advertise a default hour while the actual experiment timeout differs; later workers can lack the corresponding constraint entirely. HITL initial execution already suppresses the implicit time-limit text with `include_implicit_time_limit=False` (`src/core/pipeline_orchestrator.py:1799`).

**Implication:** one renderer should format a runtime-produced snapshot of total budget, remaining time, stage allowance, deadline, and finishing reserve. Reuse it in all relevant prompts and status surfaces. A read-only status tool is a view of this record, not another timer.

### E. No durable budget survives a research restart or rollback

Ordinary saved state records lineage, current best, iteration progress, and an update timestamp (`src/core/autoresearch.py:210`). HITL has more durable state, but its private snapshots deliberately roll back most research/runtime state while excluding launch/control records (`src/core/hitl_git_state.py:35`, `:274`). Neither constitutes a persisted research deadline.

Scheduler SIGTERM is intentionally distinguished from user SIGINT: SIGTERM preserves interrupted HITL work for restart, while user stop triggers cooperative recovery (`src/cli/hitl_run_worker.py:205`, `tests/test_hitl_timeout_recovery.py`).

**Implication:** budget ownership must be tied to the research run and survive process/launch replacement, rather than being recreated for every launch ID or checkpoint. Budget state must be excluded from public and private rollback. Exhaustion needs its own reason; mapping it to an ordinary failed attempt could trigger automatic retries, and mapping it to user stop would misrepresent the event.

### F. Remote compute and setup are outside the current per-call cap

The DSI wrapper provisions remote workspaces before worker launch and removes them afterward. Its SSH connection timeout is not a full operation timeout. The inspected runtime does not own a live Slurm job registry or cancel submitted jobs on a shared deadline. Slurm instructions and artifacts provide useful job information, but killing a local worker/SSH connection does not establish that an already submitted batch job has stopped.

Modal's training template includes a ten-hour function timeout; other templates use other durations. Those values apply per remote invocation. Its lifecycle has app-stop/teardown primitives, but they are not tied to a shared research deadline.

**Implication:** backend controls must derive their limits from the same policy and cancel only jobs owned by this run. Check remaining time after provisioning/transfers, before dispatch, before scoring, and before new retries. For strict PostTrainBench accounting, placing the run in a fixed H100 allocation/container provides an outer ceiling; internal awareness is still needed for graceful completion.

### G. Final scoring and checkpoint operations need explicit treatment

Scoring, verifier calls, artifact hashing/copying, checkpoint creation/restoration, and model export can take nontrivial time outside a training subprocess. Some verifier callers use its own default timeout instead of a run-derived allowance. A preflight check alone cannot interrupt a long synchronous copy or external command.

**Implication:** reserve time within the total allowance, propagate caps into the actual blocking operations, and retain the last valid accepted result. Ordinary Git checkpointing does not independently guarantee restoration of ignored or overwritten model weights; the PostTrainBench artifact adapter remains responsible for its model-output contract. Do not advertise a valid final model merely because the orchestration clock stopped.

## 3. One coherent design

### One configuration resolution

Reuse explicitly supplied `constraints.time_limit` as the task's declared time value. If a command-line `--time-budget` override is added, resolve it once into the same canonical value; do not retain competing YAML and CLI countdowns. Existing `--timeout`, proposer/scorer limits, provider timeouts, and backend job limits remain subordinate per-operation caps.

This requires a documented migration because `time_limit` is currently advisory, and the schema/default prompt mentions an implicit hour. Do not turn that implicit default into an unexpected hard limit. Omitted total budget should mean no total deadline. New budgeted runs should record their resolved value and source; resuming an existing run must reuse its value, and a conflicting requested value should require an explicit new-run/extension operation rather than silently reset it. Legacy resumed runs need an explicit budget start, not a guess based on old timestamps.

### One runtime owner

At the `ResearchRunner` boundary, create/load a small provider-neutral run-budget object. Both ordinary and HITL orchestration, manager execution, scorer/verifier, and compute adapters consume this same policy. Do not put independent budget calculations inside `AutoResearchController` and `HitlAutoResearchController`.

Suggested operations: inspect remaining time, obtain a capped allowance, check whether another stage may start, and produce an immutable prompt/status snapshot. Persist the budget identity, start time, deadline, total seconds, finishing reserve, and stop reason once using existing atomic-write conventions. Keep it outside checkpoint rollback and bind continuation launches to the original budget identity. Avoid a second mutable “remaining seconds” field that can diverge from the deadline.

Use a persisted UTC deadline for resume and a monotonic elapsed clock during a process lifetime. A restart derives remaining time from the original deadline. For the initial feature, wall-clock time continues through retries, queueing, human waits, and downtime. Active-time accounting with pause credits would be a separate policy decision, not something to add implicitly for HITL.

### Existing execution mechanisms consume the allowance

For a stage, effective allowance is the minimum of its existing cap (if any) and the shared available time. No stage gets a new copy of the total budget. Nonpositive allowance prevents launch; do not round it into a fresh minimum timeout.

- CLI processes: reuse and repair `run_prebuilt_cli_agent`; migrate the older blocking launchers.
- Manager/provider calls: retain `LLMBackend` transport adapters, passing the same policy's allowance; stop retries and interrupt waiting calls when exhausted.
- Verifier API: retain its `asyncio.wait_for` wrapper with a derived allowance.
- HITL: reuse the established cancellation/recovery machinery with a distinct exhaustion reason and no automatic replacement after exhaustion.
- Remote backends: use backend-native limits and job cancellation derived from the same allowance and owned-job identity.

Transport, startup, and lock timeouts may still exist. Multiple enforcement points are necessary; multiple independent definitions of the run budget are not.

### One prompt/status contract

Render one consistent block at dispatch for proposer, worker, and manager, and expose the same information in status output. Include the stage cap so “three hours remain overall” is not mistaken for “this worker may run three hours.” A live read-only status command can query the shared record during a long call. Logs capture budget snapshots at stage boundaries for later runtime analysis.

### One terminal outcome

Budget exhaustion stops new proposals, retries, repairs, and background work. It is distinct from user cancellation, provider failure, and a scientifically unsuccessful candidate. Complete bounded cleanup within the reserve, preserve the last valid accepted artifact, and record whether a usable result exists. An exhausted run with no valid baseline is not a successful research result.

For a fresh benchmark run, accounting starts before the first agent-controlled research/setup stage and includes baseline construction and development evaluation. For continuation, use the existing run deadline; starting a new budget is an explicit action. Paper writing/publication can be a separate post-research workflow; they must not extend the declared research window or change the selected artifact after the deadline.

## 4. Suggested implementation boundaries

1. Consolidate ordinary proposer/resource finder, trimmer/bootstrap, and relevant legacy/standalone subprocess execution onto the existing helper. Strengthen timeout cleanup there. Preserve existing behavior where no cap is supplied.
2. Add one generic budget resolver/state object at the top-level run boundary, with rollback-safe persistence and explicit exhaustion semantics.
3. Thread the object through both research controllers, pipeline stages, manager waits/retries, scorer/verifier, and one shared prompt/status renderer.
4. Integrate remote ownership/cancellation before claiming strict remote-budget support. A local-only first delivery should reject or clearly exclude unsupported strict remote enforcement rather than claim it is complete.
5. Validate a full run and continuation under a short local budget before the H100 pilot. Add benchmark model export through the benchmark adapter, not hardcoded behavior in the generic budget policy.

No predictive scheduler, automatic budget extension, separate HITL clock, per-agent persisted countdown, benchmark-specific timer service, or attempt-count-to-time conversion is needed.

## 5. Validation performed

Source inspection covered the schema, top-level CLI and launchers, prompt generation, ordinary and HITL controllers, shared and older subprocess paths, manager/backend retries, scorer/verifier, saved state, checkpoint exclusions, signal recovery, DSI/Modal controls, and relevant tests.

Local subprocess probes used temporary files and short Python workers; no AI provider calls or GPU work:

| Probe | Requested limit | Observed wall time | Result |
|---|---:|---:|---|
| Ordinary proposer sleeps 0.6s and writes a proposal | 0.1s | 0.632s | Reported success, `timed_out=false` |
| Shared runner sleeps 0.6s | 0.1s | 0.123s | Reported timeout |
| Shared runner; child ignores SIGTERM and writes after deadline | 0.2s | 0.775s | Parent timed out; child still wrote its marker |

These timings demonstrate behavior, not performance benchmarks or guaranteed timing precision. All probe children were short-lived and completed.

Existing focused tests: **79 passed** across `tests/test_hitl_mcp_retry_classification.py` and `tests/test_eval_verifier.py`, with plugin autoload and pytest cache disabled. These validate existing retry/API mechanisms, not a shared budget that does not yet exist.

A broader initial test selection stalled during local dependency imports, before running tests. A diagnostic traceback located blocking file reads in the PyGithub/JWT/cryptography import chain. Those checks were interrupted; they are not reported as failures or passes. A separately bounded run of `test_hitl_provider_failure_propagation.py` and `test_hitl_autoresearch_review_fixes.py` also did not finish within 30 seconds and was stopped. The full recovery/integration suite is therefore unverified in this environment. No dependency changes were made.

Required acceptance tests for the future change: a shared allowance decreases across initial setup and all iterations; every retry/worker replacement inherits it; prompts and subprocess caps agree; ordinary/HITL no-budget behavior stays compatible; timeout kills remaining local children; API and manager waits stop; restart and checkpoint rollback cannot increase remaining time; expiry is terminal rather than retryable; the accepted result survives candidate interruption; and backend-owned jobs are cancelled without affecting unrelated work.

## Conclusion

The missing piece is a shared run-level policy, but several pre-existing inconsistencies must be consolidated for that policy to be trustworthy. Reuse the existing process supervisor, provider adapters, verifier cancellation, atomic state, and HITL recovery. Introduce one source of truth for time and route every awareness/enforcement surface through it.
