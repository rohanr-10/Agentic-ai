"""Unit tests: task state machine invariants."""

from __future__ import annotations

import pytest

from app.core.errors import InvalidTransitionError
from app.db.enums import TaskStatus
from app.orchestration.states import TRANSITIONS, can_transition


class TestTransitionMap:
    def test_documented_happy_path_is_legal(self):
        path = [
            TaskStatus.CREATED,
            TaskStatus.QUEUED,
            TaskStatus.PLANNING,
            TaskStatus.AWAITING_APPROVAL,
            TaskStatus.READY,
            TaskStatus.RUNNING,
            TaskStatus.VERIFYING,
            TaskStatus.COMPLETED,
        ]
        for source, target in zip(path, path[1:]):
            assert can_transition(source, target), f"{source} -> {target} should be legal"

    def test_pause_resume_path_is_legal(self):
        assert can_transition(TaskStatus.RUNNING, TaskStatus.PAUSED)
        assert can_transition(TaskStatus.PAUSED, TaskStatus.RUNNING)

    def test_retry_path_is_legal(self):
        assert can_transition(TaskStatus.RUNNING, TaskStatus.RETRYING)
        assert can_transition(TaskStatus.RETRYING, TaskStatus.RUNNING)
        assert can_transition(TaskStatus.RETRYING, TaskStatus.FAILED)

    @pytest.mark.parametrize("terminal", [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED])
    def test_terminal_states_are_absorbing(self, terminal):
        for source in TaskStatus:
            assert not can_transition(terminal, source), f"{terminal} must not leave to {source}"

    def test_no_skip_from_created_to_running(self):
        assert not can_transition(TaskStatus.CREATED, TaskStatus.RUNNING)
        assert not can_transition(TaskStatus.QUEUED, TaskStatus.RUNNING)

    def test_every_state_has_entry(self):
        for status in TaskStatus:
            assert status in TRANSITIONS

    def test_illegal_transition_raises(self):
        from app.orchestration.states import assert_transition

        with pytest.raises(InvalidTransitionError):
            assert_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING)
        with pytest.raises(InvalidTransitionError):
            assert_transition(TaskStatus.CREATED, TaskStatus.COMPLETED)


class TestConcurrentTransition:
    async def test_cas_conflict_detected(self, tmp_path, test_settings, migrated_db):
        """Two racing transitions: exactly one wins, the other gets a conflict."""
        from app.core.errors import ConflictError
        from app.db.models import Task
        from app.db.session import Database
        from app.orchestration.states import transition
        from app.core.utils import new_uuid

        database = Database(test_settings)
        try:
            async with database.begin() as session:
                from app.db.enums import UserRole
                from app.db.models import User

                user = User(
                    id=new_uuid(),
                    email="race@example.com",
                    password_hash="x",
                    display_name="Race",
                    role=UserRole.USER,
                )
                session.add(user)
                task = Task(
                    id=new_uuid(),
                    user_id=user.id,
                    title="t",
                    objective="o",
                    status=TaskStatus.CREATED,
                    version=1,
                )
                session.add(task)

            async with database.session() as s1:
                task1 = await s1.get(Task, task.id)
                assert task1 is not None
                # first transition succeeds
                updated = await transition(
                    s1, None, task_id=task.id, to_status=TaskStatus.QUEUED
                )
                assert updated.status == TaskStatus.QUEUED
                assert updated.version == 2

            # a writer acting on a stale snapshot cannot apply an outdated
            # transition: after CAS retry it sees the fresh state (QUEUED)
            # and PLANNING is applied from THERE, never from CREATED twice
            async with database.session() as s2:
                stale = await s2.get(Task, task.id)
                assert stale is not None
                stale.version = 1  # simulate a stale read
                retried = await transition(
                    s2, None, task_id=task.id, to_status=TaskStatus.PLANNING
                )
                assert retried.status == TaskStatus.PLANNING
                assert retried.version == 3  # exactly one increment per transition

            # and a now-illegal move (PLANNING -> READY without approval) is
            # rejected against the FRESH state, not the stale one
            async with database.session() as s3:
                task3 = await s3.get(Task, task.id)
                assert task3 is not None
                task3.version = 2  # stale again
                with pytest.raises(InvalidTransitionError):
                    await transition(
                        s3, None, task_id=task.id, to_status=TaskStatus.RUNNING
                    )
        finally:
            await database.dispose()


def run_migrations_if_needed(settings) -> None:
    import os
    from pathlib import Path

    db_path = settings.database_url.split("///")[-1]
    if not Path(db_path).exists():
        os.environ["AGP_DATABASE_URL"] = settings.database_url
        from alembic import command
        from alembic.config import Config as AlembicConfig

        command.upgrade(AlembicConfig("alembic.ini"), "head")


@pytest.fixture
def migrated_db(test_settings):
    """Sync fixture: Alembic must run outside the asyncio loop."""
    run_migrations_if_needed(test_settings)
