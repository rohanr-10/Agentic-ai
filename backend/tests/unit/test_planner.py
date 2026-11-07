"""Unit tests: plan validation and scheduling."""

from __future__ import annotations

import pytest

from app.orchestration.planner import (
    Plan,
    ScriptedPlanner,
    StepSpec,
    topological_groups,
    validate_plan,
)
from app.tools.registry import ToolRegistry, register_builtin_tools


@pytest.fixture
def full_registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_builtin_tools(reg)
    return reg


def _plan(steps: list[dict]) -> Plan:
    return Plan.model_validate({"objective_restated": "test", "steps": steps})


class TestPlanValidation:
    def test_valid_linear_plan(self, full_registry):
        plan = _plan(
            [
                {"id": "a", "title": "A", "tool_key": "fs.write", "input": {}},
                {"id": "b", "title": "B", "tool_key": "fs.read", "depends_on": ["a"]},
            ]
        )
        assert validate_plan(plan, registry=full_registry, max_steps=50) == []

    def test_duplicate_step_ids_rejected(self, full_registry):
        plan = _plan([{"id": "a", "title": "A"}, {"id": "a", "title": "B"}])
        errors = validate_plan(plan, registry=full_registry, max_steps=50)
        assert any("duplicate" in e for e in errors)

    def test_unknown_dependency_rejected(self, full_registry):
        plan = _plan([{"id": "a", "title": "A", "depends_on": ["ghost"]}])
        errors = validate_plan(plan, registry=full_registry, max_steps=50)
        assert any("unknown step" in e for e in errors)

    def test_cycle_rejected(self, full_registry):
        plan = _plan(
            [
                {"id": "a", "title": "A", "depends_on": ["b"]},
                {"id": "b", "title": "B", "depends_on": ["a"]},
            ]
        )
        errors = validate_plan(plan, registry=full_registry, max_steps=50)
        assert any("cycle" in e for e in errors)

    def test_self_dependency_rejected(self, full_registry):
        plan = _plan([{"id": "a", "title": "A", "depends_on": ["a"]}])
        errors = validate_plan(plan, registry=full_registry, max_steps=50)
        assert any("itself" in e for e in errors)

    def test_unknown_tool_rejected(self, full_registry):
        plan = _plan([{"id": "a", "title": "A", "tool_key": "nonexistent.tool"}])
        errors = validate_plan(plan, registry=full_registry, max_steps=50)
        assert any("unknown tool" in e for e in errors)

    def test_step_budget_enforced(self, full_registry):
        steps = [{"id": f"s{i}", "title": f"S{i}"} for i in range(5)]
        errors = validate_plan(_plan(steps), registry=full_registry, max_steps=3)
        assert any("maximum" in e for e in errors)

    def test_model_steps_without_tools_allowed(self, full_registry):
        plan = _plan([{"id": "think", "title": "Think about it"}])
        assert validate_plan(plan, registry=full_registry, max_steps=50) == []


class TestScheduling:
    def test_topological_groups_respect_dependencies(self):
        plan = _plan(
            [
                {"id": "c", "title": "C", "depends_on": ["a", "b"]},
                {"id": "a", "title": "A"},
                {"id": "b", "title": "B"},
                {"id": "d", "title": "D", "depends_on": ["c"]},
            ]
        )
        groups = topological_groups(plan)
        flat = [s.id for group in groups for s in group]
        assert flat.index("a") < flat.index("c")
        assert flat.index("b") < flat.index("c")
        assert flat.index("c") < flat.index("d")
        # independent a and b share a wave
        assert {s.id for s in groups[0]} == {"a", "b"}


class TestScriptedPlanner:
    async def test_returns_plans_in_order_and_records_calls(self):
        from app.orchestration.planner import PlanContext

        planner = ScriptedPlanner([_plan([{"id": "x", "title": "X"}])])
        context = PlanContext(objective="do it")
        plan = await planner.plan(context)
        assert plan.steps[0].id == "x"
        assert planner.calls == [context]
        with pytest.raises(Exception):
            await planner.plan(context)  # exhausted


class TestStepSpec:
    def test_step_id_pattern_enforced(self):
        with pytest.raises(ValueError):
            StepSpec(id="../evil", title="Evil")
        with pytest.raises(ValueError):
            StepSpec(id="", title="Empty")
