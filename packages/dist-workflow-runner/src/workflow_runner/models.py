"""Frozen data models shared across the workflow-runner.

``AppContext`` is the lifespan session state (config + live server pool);
everything else is a frozen value type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from workflow_runner.client import ServerPool


@dataclass(frozen=True)
class ServerSpec:
    """A single domain MCP server configured in ``servers.yaml``."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout_s: int = 300


@dataclass(frozen=True)
class RunnerConfig:
    """Resolved runner configuration."""

    runstore_db: str | None = None
    workflow_dir: str | None = None
    servers: list[ServerSpec] = field(default_factory=list)


@dataclass(frozen=True)
class WorkflowStep:
    """One step of a workflow template."""

    id: str
    server: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    capture: str | None = None
    on_failure: str = "fail"


@dataclass(frozen=True)
class WorkflowSpec:
    """A validated workflow template (``schema_version`` 1)."""

    schema_version: int
    workflow_id: str
    version: str
    name: str
    description: str = ""
    source_prompt: str | None = None
    inputs: list[dict[str, Any]] = field(default_factory=list)
    steps: list[WorkflowStep] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def step_count(self) -> int:
        return len(self.steps)


@dataclass(frozen=True)
class StepResult:
    """Result of a single executed step."""

    step_id: str
    server: str
    tool: str
    args_resolved: dict[str, Any]
    status: str  # 'succeeded' | 'failed' | 'skipped'
    error: str | None = None
    result: dict[str, Any] | None = None
    started_at_utc: str | None = None
    finished_at_utc: str | None = None
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "server": self.server,
            "tool": self.tool,
            "args_resolved": self.args_resolved,
            "status": self.status,
            "error": self.error,
            "result": self.result,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class WorkflowExecution:
    """The execution-graph record for one workflow run (§2.5)."""

    workflow_id: str
    workflow_version: str
    source_prompt: str | None
    inputs_resolved: dict[str, Any]
    run_id: str
    status: str
    started_at_utc: str
    finished_at_utc: str | None = None
    steps: list[StepResult] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """The exact execution-graph artifact JSON shape (§2.5)."""
        return {
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "source_prompt": self.source_prompt,
            "inputs_resolved": self.inputs_resolved,
            "run_id": self.run_id,
            "status": self.status,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "steps": [s.to_dict() for s in self.steps],
            "outputs": self.outputs,
        }


@dataclass
class AppContext:
    """Lifespan session state for the runner server.

    Populated during the MCPServer lifespan and accessed by tools via
    ``ctx.request_context.lifespan_context``. ``pool`` is the live
    :class:`~workflow_runner.client.ServerPool` (a ``FakePool`` in tests).
    """

    config: RunnerConfig
    pool: Any = None

    @property
    def runstore_db(self) -> str | None:
        return self.config.runstore_db

    @property
    def workflow_dir(self) -> str | None:
        return self.config.workflow_dir
