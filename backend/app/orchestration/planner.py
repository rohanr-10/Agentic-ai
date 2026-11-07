"""Planning: plan representation, validation, and planner interfaces.

A plan is a DAG of steps.  Every plan — whether proposed by the model,
hand-written by the client, or revised after verification failure — passes
through the same strict validation before it can touch the executor:

* step ids unique, dependencies exist, no cycles (Kahn)
* tool keys exist in the registry and are enabled
* step count and timeouts bounded
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog
from pydantic import BaseModel, Field

from app.core.errors import OrchestrationError, RequestValidationError
from app.tools.registry import ToolRegistry

logger = structlog.stdlib.get_logger("app.planner")

_STEP_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$"


class StepSpec(BaseModel):
    id: str = Field(pattern=_STEP_ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    tool_key: str | None = Field(default=None, max_length=100)
    input: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    requires_approval: bool = False
    max_retries: int = Field(default=0, ge=0, le=5)
    timeout_s: int = Field(default=60, ge=1, le=600)
    success_criteria: list[str] = Field(default_factory=list, max_length=10)


class Plan(BaseModel):
    objective_restated: str = Field(default="", max_length=4000)
    steps: list[StepSpec] = Field(min_length=1)
    notes: str = Field(default="", max_length=4000)


PLAN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "objective_restated": {"type": "string"},
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "pattern": _STEP_ID_PATTERN},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "tool_key": {"type": ["string", "null"]},
                    "input": {"type": "object"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "requires_approval": {"type": "boolean"},
                    "max_retries": {"type": "integer", "minimum": 0, "maximum": 5},
                    "timeout_s": {"type": "integer", "minimum": 1, "maximum": 600},
                    "success_criteria": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "title"],
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["steps"],
}


def validate_plan(plan: Plan, *, registry: ToolRegistry, max_steps: int) -> list[str]:
    """Return a list of validation problems (empty == valid)."""
    errors: list[str] = []
    if len(plan.steps) > max_steps:
        errors.append(f"plan has {len(plan.steps)} steps; maximum is {max_steps}")

    ids = [step.id for step in plan.steps]
    if len(ids) != len(set(ids)):
        errors.append("duplicate step ids")
    id_set = set(ids)

    for step in plan.steps:
        if step.id in step.depends_on:
            errors.append(f"step '{step.id}' depends on itself")
        for dep in step.depends_on:
            if dep not in id_set:
                errors.append(f"step '{step.id}' depends on unknown step '{dep}'")
        if step.tool_key is not None:
            tool = registry.try_get(step.tool_key)
            if tool is None:
                errors.append(f"step '{step.id}' references unknown tool '{step.tool_key}'")

    # cycle detection (Kahn)
    indegree = {step_id: 0 for step_id in id_set}
    adjacency: dict[str, list[str]] = {step_id: [] for step_id in id_set}
    for step in plan.steps:
        for dep in step.depends_on:
            if dep in id_set:
                indegree[step.id] += 1
                adjacency[dep].append(step.id)
    queue = deque([sid for sid, degree in indegree.items() if degree == 0])
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for nxt in adjacency[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if visited != len(id_set):
        errors.append("plan contains a dependency cycle")

    return errors


def topological_groups(plan: Plan) -> list[list[StepSpec]]:
    """Group steps into parallel-executable waves."""
    by_id = {step.id: step for step in plan.steps}
    indegree = {step.id: len(step.depends_on) for step in plan.steps}
    adjacency: dict[str, list[str]] = {step.id: [] for step in plan.steps}
    for step in plan.steps:
        for dep in step.depends_on:
            adjacency[dep].append(step.id)

    groups: list[list[StepSpec]] = []
    remaining = set(by_id)
    while remaining:
        ready = sorted(
            [sid for sid in remaining if indegree[sid] == 0]
        )
        if not ready:
            raise OrchestrationError("plan dependency cycle detected during scheduling")
        groups.append([by_id[sid] for sid in ready])
        for sid in ready:
            remaining.discard(sid)
            for nxt in adjacency[sid]:
                indegree[nxt] -= 1
    return groups


@dataclass
class PlanContext:
    objective: str
    available_tools: list[dict[str, Any]] = field(default_factory=list)
    additional_notes: list[str] = field(default_factory=list)
    conversation_context: str = ""


class Planner(Protocol):
    async def plan(self, context: PlanContext) -> Plan: ...

    async def replan(
        self, context: PlanContext, previous: Plan, failures: list[str]
    ) -> Plan: ...


class ModelPlanner:
    """Planner driven by the model gateway with structured outputs.

    The model PROPOSES; ``validate_plan`` decides.  Invalid proposals get
    exactly one repair round with the validation errors quoted back; a
    second failure aborts the task (never a silent guess).
    """

    def __init__(self, manager, *, model_config_id=None, registry: ToolRegistry | None = None):
        self.manager = manager
        self.model_config_id = model_config_id
        self.registry = registry

    def _messages(self, context: PlanContext, *, repair_errors: list[str] | None = None) -> list:
        from app.gateway.base import InferenceMessage

        tool_lines = "\n".join(
            json.dumps(tool) for tool in context.available_tools
        ) or "(no tools available)"
        notes = "\n".join(f"- {n}" for n in context.additional_notes) or "(none)"
        system = (
            "You are the planning module of an autonomous execution engine. "
            "Decompose the objective into a minimal, valid plan. Use tools only when "
            "they are listed. Prefer fewer steps. Respond with a single JSON object "
            "matching the provided schema exactly."
        )
        user = (
            f"OBJECTIVE:\n{context.objective}\n\n"
            f"AVAILABLE TOOLS (JSON):\n{tool_lines}\n\n"
            f"USER NOTES:\n{notes}\n"
        )
        if context.conversation_context:
            user += f"\nCONVERSATION CONTEXT:\n{context.conversation_context[:4000]}\n"
        if repair_errors:
            user += (
                "\nYOUR PREVIOUS PROPOSAL WAS REJECTED for these reasons:\n- "
                + "\n- ".join(repair_errors)
                + "\nProduce a corrected plan."
            )
        return [
            InferenceMessage(role="system", content=system),
            InferenceMessage(role="user", content=user),
        ]

    async def plan(self, context: PlanContext) -> Plan:
        return await self._request_plan(context)

    async def replan(
        self, context: PlanContext, previous: Plan, failures: list[str]
    ) -> Plan:
        context.additional_notes = [
            *context.additional_notes,
            f"previous plan failed verification: {'; '.join(failures[:5])}",
        ]
        return await self._request_plan(context)

    async def _request_plan(self, context: PlanContext) -> Plan:
        from app.gateway.base import InferenceRequest

        repair_errors: list[str] | None = None
        for _round in range(2):
            request = InferenceRequest(
                messages=self._messages(context, repair_errors=repair_errors),
                response_schema=PLAN_JSON_SCHEMA,
                max_tokens=4096,
            )
            response = await self.manager.generate(
                # session is unused by the manager for planning (adapter cached)
                _NullSession(),
                request,
                model_config_id=self.model_config_id,
            )
            try:
                plan = Plan.model_validate(json.loads(response.content))
            except (json.JSONDecodeError, ValueError) as exc:
                repair_errors = [f"response was not valid JSON for the schema: {exc}"]
                continue
            return plan
        raise OrchestrationError("model failed to produce a valid plan after one repair round")


class _NullSession:
    """The manager only uses the session for adapter resolution; planning
    against the default model needs no database access."""

    async def scalar(self, *args, **kwargs):  # pragma: no cover - protocol stub
        return None


class ScriptedPlanner:
    """Deterministic planner for tests and demos.

    Returns pre-seeded plans in order; records every call for assertions.
    This is a test double, clearly labelled — never used in production paths
    unless explicitly configured.
    """

    def __init__(self, plans: list[Plan]):
        self._plans = list(plans)
        self.calls: list[PlanContext] = []
        self.replan_calls: list[tuple[Plan, list[str]]] = []

    async def plan(self, context: PlanContext) -> Plan:
        self.calls.append(context)
        if not self._plans:
            raise OrchestrationError("scripted planner has no plans left")
        return self._plans.pop(0)

    async def replan(
        self, context: PlanContext, previous: Plan, failures: list[str]
    ) -> Plan:
        self.replan_calls.append((previous, failures))
        return await self.plan(context)


def parse_client_plan(raw: dict[str, Any]) -> Plan:
    try:
        return Plan.model_validate(raw)
    except ValueError as exc:
        raise RequestValidationError(
            "invalid plan structure", details=json.loads(exc.json()) if hasattr(exc, "json") else str(exc)
        ) from exc
