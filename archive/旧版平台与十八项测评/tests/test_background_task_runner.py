import asyncio
import tempfile
import unittest
from pathlib import Path

from src.audit.event_bus import EventBus
from src.core.data_models import Task, TaskResult, TaskStatus, TaskType
from src.persistence import SQLiteArtifactStore, SQLiteDatabase, SQLiteEventStore, SQLiteTaskStore
from src.tasks import BackgroundTaskRunner, CancellationRegistry, RuntimeTaskStatus


class FakeEnv:
    def __init__(self, delay: float = 0.0, fail: bool = False):
        self.delay = delay
        self.fail = fail

    async def submit_task(self, task):
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("boom")
        return TaskResult(task_id=task.task_id, status=TaskStatus.COMPLETED, output={"ok": True})


def make_task(task_id="t1"):
    return Task(task_id=task_id, task_type=TaskType.SINGLE_DOMAIN, description="demo")


class BackgroundTaskRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_runner_completes_queued_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = SQLiteDatabase(Path(tmp) / "runner.sqlite3")
            store = SQLiteTaskStore(db)
            bus = EventBus(SQLiteEventStore(db))
            runner = BackgroundTaskRunner(FakeEnv(), store, bus, CancellationRegistry(), SQLiteArtifactStore(db))
            task = make_task()

            await runner.submit(task)
            self.assertEqual(store.get_task("t1").status, RuntimeTaskStatus.QUEUED.value)
            await runner.run_once("t1")

            self.assertEqual(store.get_task("t1").status, RuntimeTaskStatus.COMPLETED.value)
            self.assertGreaterEqual(len(bus.query(task_id="t1")), 2)

    async def test_queued_task_cancelled_before_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = SQLiteDatabase(Path(tmp) / "runner.sqlite3")
            store = SQLiteTaskStore(db)
            bus = EventBus(SQLiteEventStore(db))
            cancellation = CancellationRegistry()
            runner = BackgroundTaskRunner(FakeEnv(), store, bus, cancellation)
            task = make_task()

            await runner.submit(task)
            cancellation.request_cancel("t1")
            await runner.run_once("t1")

            self.assertEqual(store.get_task("t1").status, RuntimeTaskStatus.CANCELLED.value)

    async def test_running_cancel_flag_marks_cancelled_after_execution_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = SQLiteDatabase(Path(tmp) / "runner.sqlite3")
            store = SQLiteTaskStore(db)
            bus = EventBus(SQLiteEventStore(db))
            cancellation = CancellationRegistry()
            runner = BackgroundTaskRunner(FakeEnv(delay=0.05), store, bus, cancellation)
            task = make_task()

            await runner.submit(task)
            running = asyncio.create_task(runner.run_once("t1"))
            await asyncio.sleep(0.01)
            cancellation.request_cancel("t1")
            await running

            self.assertEqual(store.get_task("t1").status, RuntimeTaskStatus.CANCELLED.value)

    async def test_failure_marks_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = SQLiteDatabase(Path(tmp) / "runner.sqlite3")
            store = SQLiteTaskStore(db)
            bus = EventBus(SQLiteEventStore(db))
            runner = BackgroundTaskRunner(FakeEnv(fail=True), store, bus, CancellationRegistry())
            task = make_task()

            await runner.submit(task)
            await runner.run_once("t1")

            self.assertEqual(store.get_task("t1").status, RuntimeTaskStatus.FAILED.value)


if __name__ == "__main__":
    unittest.main()
