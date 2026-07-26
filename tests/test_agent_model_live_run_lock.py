import tempfile
import unittest
from pathlib import Path

from src.evaluation.agent_model.live_run_lock import (
    LiveRunAlreadyActive,
    LiveRunLock,
)


class AgentModelLiveRunLockTest(unittest.TestCase):
    def test_second_live_suite_cannot_acquire_same_process_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live.lock"
            first = LiveRunLock(path)
            second = LiveRunLock(path)
            first.acquire()
            try:
                with self.assertRaises(LiveRunAlreadyActive):
                    second.acquire()
            finally:
                first.release()

            second.acquire()
            second.release()
