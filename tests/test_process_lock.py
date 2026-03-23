from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sultan_engine_v2.runtime.process_lock import (
    FileProcessLock,
    ProcessAlreadyRunningError,
    _pid_command_contains,
    _pid_is_running_windows,
)


class ProcessLockTests(unittest.TestCase):
    def test_acquire_and_release_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock = FileProcessLock(
                Path(temp_dir) / "service.lock",
                service_name="TEST_SERVICE",
                pid_provider=lambda: 100,
                pid_checker=lambda pid: pid == 100,
            )

            metadata = lock.acquire()

            self.assertEqual(100, metadata.pid)
            self.assertTrue(lock.path.exists())

            lock.release()

            self.assertFalse(lock.path.exists())

    def test_acquire_raises_when_active_pid_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "service.lock"
            first_lock = FileProcessLock(
                lock_path,
                service_name="TEST_SERVICE",
                pid_provider=lambda: 111,
                pid_checker=lambda pid: pid == 111,
            )
            first_lock.acquire()

            second_lock = FileProcessLock(
                lock_path,
                service_name="TEST_SERVICE",
                pid_provider=lambda: 222,
                pid_checker=lambda pid: pid == 111,
            )

            with self.assertRaises(ProcessAlreadyRunningError):
                second_lock.acquire()

    def test_stale_lock_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "service.lock"
            stale_lock = FileProcessLock(
                lock_path,
                service_name="TEST_SERVICE",
                pid_provider=lambda: 111,
                pid_checker=lambda pid: False,
            )
            stale_lock.acquire()

            fresh_lock = FileProcessLock(
                lock_path,
                service_name="TEST_SERVICE",
                pid_provider=lambda: 222,
                pid_checker=lambda pid: pid == 222,
            )
            metadata = fresh_lock.acquire()

            self.assertEqual(222, metadata.pid)

    def test_pid_is_running_windows_returns_true_for_tasklist_match(self) -> None:
        tasklist_result = SimpleNamespace(stdout='"python.exe","123","Console","1","12,000 K"\n')

        with patch("sultan_engine_v2.runtime.process_lock.subprocess.run", return_value=tasklist_result):
            self.assertTrue(_pid_is_running_windows(123))

    def test_pid_is_running_windows_returns_false_when_tasklist_has_no_match(self) -> None:
        tasklist_result = SimpleNamespace(stdout="INFO: No tasks are running which match the specified criteria.\n")

        with patch("sultan_engine_v2.runtime.process_lock.subprocess.run", return_value=tasklist_result):
            self.assertFalse(_pid_is_running_windows(123))

    def test_acquire_replaces_lock_when_pid_reused_by_different_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "service.lock"
            first_lock = FileProcessLock(
                lock_path,
                service_name="TEST_SERVICE",
                pid_provider=lambda: 111,
                pid_checker=lambda pid: pid == 111,
                command="run_dashboard.py",
            )
            first_lock.acquire()

            second_lock = FileProcessLock(
                lock_path,
                service_name="TEST_SERVICE",
                pid_provider=lambda: 222,
                pid_checker=lambda pid: pid == 111,
                command="run_dashboard.py",
            )

            with patch("sultan_engine_v2.runtime.process_lock._pid_command_contains", return_value=False):
                metadata = second_lock.acquire()

            self.assertEqual(222, metadata.pid)

    def test_acquire_raises_when_pid_matches_same_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "service.lock"
            first_lock = FileProcessLock(
                lock_path,
                service_name="TEST_SERVICE",
                pid_provider=lambda: 111,
                pid_checker=lambda pid: pid == 111,
                command="run_dashboard.py",
            )
            first_lock.acquire()

            second_lock = FileProcessLock(
                lock_path,
                service_name="TEST_SERVICE",
                pid_provider=lambda: 222,
                pid_checker=lambda pid: pid == 111,
                command="run_dashboard.py",
            )

            with patch("sultan_engine_v2.runtime.process_lock._pid_command_contains", return_value=True):
                with self.assertRaises(ProcessAlreadyRunningError):
                    second_lock.acquire()

    def test_pid_command_contains_windows_uses_command_line_check(self) -> None:
        process_result = SimpleNamespace(stdout='"F:\\\\AI STORY CHANNEL\\\\.venv\\\\Scripts\\\\python.exe" sultan_engine_v2\\\\run_dashboard.py\n')

        with patch("sultan_engine_v2.runtime.process_lock.os.name", "nt"):
            with patch("sultan_engine_v2.runtime.process_lock.subprocess.run", return_value=process_result):
                self.assertTrue(_pid_command_contains(123, "run_dashboard.py"))


if __name__ == "__main__":
    unittest.main()
