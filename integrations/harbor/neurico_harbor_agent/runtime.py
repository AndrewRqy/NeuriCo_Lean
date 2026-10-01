"""Harbor-to-NeuriCo input and inference configuration helpers."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import claude_agent_sdk

from src.cli.submit_local import _convert_without_llm


@dataclass(frozen=True)
class HarborAutoResearchTask:
    """One Harbor instruction executed by NeuriCo AutoResearch."""

    instruction: str
    workspace: Path

    def __post_init__(self) -> None:
        if not self.instruction.strip():
            raise ValueError("Harbor instruction must not be empty")
        if not self.workspace.is_absolute():
            raise ValueError("Harbor workspace must be an absolute path")
        if not self.workspace.is_dir():
            raise ValueError(f"Harbor workspace does not exist: {self.workspace}")


@dataclass(frozen=True)
class HostedInferenceConnection:
    """Harbor inference settings mapped to NeuriCo's Claude CLI provider."""

    requested_model: str
    token: str
    base_url: str | None = None

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str],
        *,
        requested_model: str,
    ) -> HostedInferenceConnection:
        token = environment.get("HOSTED_INFERENCE_TOKEN") or environment.get("ANTHROPIC_API_KEY")
        if not token:
            raise ValueError(
                "Missing inference credential: set HOSTED_INFERENCE_TOKEN "
                "(hosted Harbor) or ANTHROPIC_API_KEY (local Harbor)"
            )

        base_url = environment.get("HOSTED_INFERENCE_URL") or environment.get("ANTHROPIC_BASE_URL")
        provider, separator, _ = requested_model.partition("/")
        if not base_url and separator and provider != "anthropic":
            raise ValueError(
                "Direct credential mode currently requires an Anthropic model; "
                "use Harbor gateway mode for non-Anthropic model routing"
            )

        return cls(
            requested_model=requested_model,
            token=token,
            base_url=base_url or None,
        )

    @property
    def backend_model(self) -> str:
        """Return the model spelling expected by Claude Code."""
        if self.base_url:
            return self.requested_model
        provider, separator, model = self.requested_model.partition("/")
        if separator and provider == "anthropic":
            return model
        return self.requested_model

    def claude_environment(self) -> dict[str, str]:
        """Map Harbor's neutral inference settings to the Claude CLI."""
        result = {
            "ANTHROPIC_API_KEY": self.token,
            "ANTHROPIC_MODEL": self.backend_model,
            "IS_SANDBOX": "1",
        }
        if self.base_url:
            result["ANTHROPIC_BASE_URL"] = self.base_url

        # NeuriCo launches several specialized Claude agents. Pin every model
        # tier so each stage uses the model selected by Harbor.
        result["ANTHROPIC_DEFAULT_SONNET_MODEL"] = self.backend_model
        result["ANTHROPIC_DEFAULT_OPUS_MODEL"] = self.backend_model
        result["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = self.backend_model
        result["CLAUDE_CODE_SUBAGENT_MODEL"] = self.backend_model
        return result


def _title_from_instruction(instruction: str) -> str:
    """Derive only the schema-required title; preserve the full task elsewhere."""
    first_line = next((line.strip() for line in instruction.splitlines() if line.strip()), "")
    first_line = re.sub(r"^#{1,6}\s+", "", first_line)
    first_line = re.sub(r"\s+", " ", first_line).strip()
    title = first_line[:197].rstrip()
    if len(title) < 10:
        title = f"Harbor AutoResearch: {title or 'Research task'}"
    return title


def build_harbor_idea(task: HarborAutoResearchTask) -> dict[str, Any]:
    """Use NeuriCo's local converter to wrap a Harbor instruction as an idea.

    The complete instruction is retained in ``background.description``, which
    NeuriCo already promotes as high-priority user instructions when generating
    research and AutoResearch prompts.
    """
    converted = _convert_without_llm(
        {
            "path": "harbor://instruction",
            "title": _title_from_instruction(task.instruction),
            "description": task.instruction,
            "tags": ["harbor", "autoresearch"],
            "author": None,
            "raw_text": task.instruction,
        }
    )["parsed"]
    idea = converted["idea"]
    metadata = idea.setdefault("metadata", {})
    metadata.update(
        {
            "source": "harbor",
            "local_workspace": str(task.workspace),
            "instruction_sha256": sha256(task.instruction.encode("utf-8")).hexdigest(),
        }
    )
    return converted


def bundled_claude_directory() -> Path:
    """Return the locked Claude executable directory shipped by the SDK wheel."""
    package_root = Path(claude_agent_sdk.__file__).resolve().parent
    executable = package_root / "_bundled" / "claude"
    if not executable.is_file():
        raise RuntimeError(f"Bundled Claude executable is missing: {executable}")
    return executable.parent


def build_autoresearch_environment(
    base_environment: Mapping[str, str],
    connection: HostedInferenceConnection,
    *,
    ideas_dir: Path,
) -> dict[str, str]:
    """Build the environment inherited by NeuriCo and all of its agents."""
    environment = dict(base_environment)
    environment.update(connection.claude_environment())
    environment["NEURICO_IDEAS"] = str(ideas_dir)
    environment["PYTHONUNBUFFERED"] = "1"
    current_path = environment.get("PATH", os.defpath)
    environment["PATH"] = f"{bundled_claude_directory()}{os.pathsep}{current_path}"
    return environment
