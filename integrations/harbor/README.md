# NeuriCo Harbor agent

This directory is the locked ACP runtime for using NeuriCo AutoResearch as a
Harbor agent. The adapter is deliberately thin: it converts Harbor's prompt
through NeuriCo's existing local-idea converter and launches the existing
fresh AutoResearch workflow in Harbor's supplied repository.

## Contract

- The ACP `cwd` becomes the idea's `metadata.local_workspace`. The adapter
  never assumes `/app`, and NeuriCo uses Harbor's repository as its research
  workspace.
- Harbor's complete text prompt is preserved in
  `idea.background.description`. NeuriCo's prompt generator already promotes
  that field as high-priority user instructions.
- NeuriCo runs its normal fresh AutoResearch lifecycle: resource discovery,
  scored baseline construction, proposal, candidate experiment, scoring, and
  accept-or-restore checkpointing.
- The adapter is benchmark-focused. Internal scoring is enabled, while paper
  generation and scribe/notebook output are always disabled. Harbor's verifier
  remains the authoritative benchmark result after the agent exits.
- One AutoResearch improvement iteration is used by default, matching
  NeuriCo's CLI default. `NEURICO_HARBOR_AUTORESEARCH_ITERATIONS` may select a
  larger positive count. This changes the search depth within one Harbor trial;
  it does not change Harbor's number of independent attempts.
- This first adapter does not add a new whole-run time-budget policy. NeuriCo's
  existing stage limits still apply, Harbor may cancel the ACP run, and a
  later NeuriCo feature can map one global budget across AutoResearch stages.
- The requested Harbor model is advertised as an ACP session configuration
  option and is pinned for every Claude-backed NeuriCo stage.
- The adapter reads `HOSTED_INFERENCE_TOKEN` and only applies
  `HOSTED_INFERENCE_URL` when Harbor supplies it. For local runs,
  `ANTHROPIC_API_KEY` and optional `ANTHROPIC_BASE_URL` are accepted.
- NeuriCo's idea registry is kept in a temporary control directory outside the
  task repository. Research state and the retained best implementation remain
  in Harbor's workspace.
- Harbor runs its own verifier after NeuriCo exits; the adapter does not inspect
  or translate Harbor's verifier.

The locked runtime includes the Claude Agent SDK because its wheel contains the
Claude executable used by NeuriCo's existing provider integration. This avoids
installing an unpinned CLI during a task and works in task images without
Node.js.

## Hosted Harbor

Use the repository root as `source.path` and point `source.manifest` at this
directory:

```json
{
  "agents": [
    {
      "name": "acp",
      "source": {
        "type": "github",
        "repo": "ChicagoHAI/neurico",
        "path": ".",
        "manifest": "integrations/harbor/harbor-agent.json"
      },
      "model_name": "anthropic/claude-sonnet-5",
      "env": {
        "NEURICO_HARBOR_AUTORESEARCH_ITERATIONS": "1"
      },
      "secrets": ["ANTHROPIC_API_KEY"]
    }
  ]
}
```

Pin `source.ref` to a commit SHA for a reproducible production run. Gateway
credential mode is supported. Direct credential mode currently requires an
Anthropic model because this adapter deliberately ships one locked backend.

## Local protocol smoke test

```bash
cd integrations/harbor
uv sync --frozen
NEURICO_MODEL=anthropic/claude-sonnet-5 \
ANTHROPIC_API_KEY=... \
uv run python -m neurico_harbor_agent
```

That command starts the stdio ACP server. A full local Harbor run uses the
same Git source manifest through `agent.name: acp`:

```yaml
agents:
  - name: acp
    model_name: anthropic/claude-sonnet-5
    env:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      NEURICO_HARBOR_AUTORESEARCH_ITERATIONS: "1"
    kwargs:
      source:
        repo_url: https://github.com/ChicagoHAI/neurico
        ref: <branch-tag-or-commit>
        source_dir: .
        manifest_path: integrations/harbor/harbor-agent.json
```

Use a branch while developing and a commit SHA once the integration is
stable. This executes on the local machine's Harbor/Docker stack; it does not
submit a hosted job.

The adapter intentionally does not accept raw NeuriCo command-line arguments.
Benchmark controls are added as individually validated `NEURICO_HARBOR_*`
environment variables so a Harbor config cannot silently enable unrelated
research-publication behavior. At present, iteration count is the only such
control; model choice belongs to Harbor, and paper generation remains off.

## Contract tests

```bash
uv sync --frozen --extra dev
uv run --frozen --extra dev pytest -q tests/contract_checks.py
```

The suite includes a real stdio ACP handshake and model-selection round trip.
AutoResearch execution is replaced at the process boundary during contract
tests, so the tests do not spend model credits.
