"""Agents the HITL manager may schedule between iterations."""

MANAGER_CALLABLE_AGENTS = frozenset({"resource_finder"})
MANAGER_AGENT_PROVENANCE_KIND = "manager_agent"
MAX_MANAGER_AGENT_ATTEMPTS = 2


def manager_callable_agent(name: str) -> str:
    normalized = str(name or "").strip()
    if normalized in MANAGER_CALLABLE_AGENTS:
        return normalized
    allowed = ", ".join(sorted(MANAGER_CALLABLE_AGENTS))
    raise ValueError(f"Agent {normalized!r} is not manager-callable. Choose from: {allowed}.")


def manager_agent_provenance(invocation_id: str) -> dict[str, str]:
    """Return the durable ownership marker for one manager-scheduled run."""
    normalized = str(invocation_id or "").strip()
    if not normalized:
        raise ValueError("Manager-agent provenance requires an invocation_id.")
    return {
        "kind": MANAGER_AGENT_PROVENANCE_KIND,
        "invocation_id": normalized,
    }
