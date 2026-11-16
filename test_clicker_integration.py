"""Bundled clicker ownership/UI checks; no EXE, job or real input is started."""
from contextlib import ExitStack
from pathlib import Path
import queue
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

import auto_chest
import chest_clicker as clicker


REQUEST_WINDOW_CLOSE = clicker._request_window_close
RELEASE_MOUSE_BUTTONS = clicker._release_mouse_buttons


class ClickerMocks:
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.data = Path(directory) / "用户 data"
        self.now = 100.
        self.pids = []
        self.process = MagicMock()
        self.process.pid = 24119
        self.process.returncode = None
        self.process.poll.side_effect = lambda: self.process.returncode
        self.process.terminate.side_effect = lambda: setattr(self.process, "returncode", 1)
        self.process.wait.side_effect = lambda **_: self.process.returncode
        self.group = MagicMock()
        self.group.pids.side_effect = lambda: list(self.pids)
        self.group.assign.side_effect = lambda process: self.pids.append(process.pid)
        def terminate_group():
            self.pids = []
            self.process.returncode = 1
        self.group.terminate.side_effect = terminate_group
        self.factory = self.stack.enter_context(patch.object(clicker, "ProcessGroup", return_value=self.group))
        self.popen = self.stack.enter_context(patch.object(clicker.subprocess, "Popen", return_value=self.process))
        self.close_windows = self.stack.enter_context(patch.object(clicker, "_request_window_close"))
        self.release_buttons = self.stack.enter_context(patch.object(clicker, "_release_mouse_buttons"))
        self.stack.enter_context(patch.object(clicker.time, "monotonic", side_effect=lambda: self.now))
        # Replace only this module's platform binding, retaining native Path
        # semantics when these mocked tests are run outside Windows.
        self.stack.enter_context(patch.object(clicker, "os", SimpleNamespace(name="nt")))


class ClickerProcessTests(ClickerMocks, unittest.TestCase):
    def test_construction_never_launches_or_creates_job(self):
        manager = clicker.ClickerProcess()
        self.assertFalse(manager.running)
        self.popen.assert_not_called()
        self.factory.assert_not_called()
        self.close_windows.assert_not_called()
        self.release_buttons.assert_not_called()

    def test_real_bundled_binary_has_pinned_hash_and_size_without_launching(self):
        report = clicker.verify_clicker()
        self.assertEqual(report["sha256"], "e4b07ea0a1cebc18e825c0ad3bc9bfdadbcb1b5b30e26cf05e00f5409da6bf60")
        self.assertEqual(report["bytes"], 20480)
        self.assertEqual(report["version"], "3.3")
        self.assertTrue(report["verified"])
        self.assertFalse(report["launched"])
        self.popen.assert_not_called()

    def test_missing_or_tampered_binary_cannot_launch(self):
        root = self.data / "untrusted bundle"
        with self.assertRaisesRegex(RuntimeError, "缺失|不可读"):
            clicker.verify_clicker(root)
        path = clicker.bundled_clicker_path(root)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"altered executable")
        manager = clicker.ClickerProcess(root)
        with self.assertRaisesRegex(RuntimeError, "校验失败"):
            manager.launch(self.data)
        self.popen.assert_not_called()
        self.factory.assert_not_called()
        self.assertFalse((self.data / "clicker").exists())

    def test_launch_uses_argument_list_private_working_directory_and_job_ownership(self):
        manager = clicker.ClickerProcess()
        self.assertTrue(manager.launch(self.data))
        self.popen.assert_called_once_with([str(clicker.bundled_clicker_path())],
                                          cwd=self.data / "clicker", close_fds=True)
        self.assertTrue((self.data / "clicker").is_dir())
        self.group.assign.assert_called_once_with(self.process)
        self.assertTrue(manager.running)
        self.assertFalse(manager.launch(self.data))
        self.assertEqual(self.popen.call_count, 1)

    def test_assignment_failure_terminates_owned_process_and_closes_job(self):
        self.group.assign.side_effect = OSError("job assignment failed")
        manager = clicker.ClickerProcess()
        with self.assertRaisesRegex(OSError, "assignment failed"):
            manager.launch(self.data)
        self.process.terminate.assert_called_once_with()
        self.process.wait.assert_called_once_with(timeout=5)
        self.group.close.assert_called_once_with()
        self.assertIsNone(manager.group)
        self.assertFalse(manager.running)

    def test_exited_original_with_active_restart_child_still_blocks_duplicate_launch(self):
        manager = clicker.ClickerProcess()
        manager.launch(self.data)
        self.process.returncode = 0
        self.pids = [24120]
        self.assertTrue(manager.running)
        self.assertTrue(manager.poll())
        self.assertFalse(manager.launch(self.data))
        self.assertEqual(self.popen.call_count, 1)
        self.group.close.assert_not_called()
        self.release_buttons.assert_not_called()
        manager.request_close()
        self.close_windows.assert_called_once_with(24120)

    def test_close_requests_only_owned_pids_then_terminates_group_after_two_seconds(self):
        manager = clicker.ClickerProcess()
        manager.launch(self.data)
        self.pids = [24119, 24120]
        manager.request_close()
        manager.request_close()
        self.assertEqual(self.close_windows.call_args_list, [call(24119), call(24120)])
        self.now = 101.999
        self.assertTrue(manager.poll())
        self.group.terminate.assert_not_called()
        self.release_buttons.assert_not_called()
        self.now = 102.
        self.assertFalse(manager.poll())
        self.group.terminate.assert_called_once_with()
        self.group.close.assert_called_once_with()
        self.release_buttons.assert_called_once_with()
        self.assertFalse(manager.poll())
        self.release_buttons.assert_called_once_with()
        self.process.terminate.assert_not_called()

    def test_already_exited_group_is_reaped_without_killing_or_closing_unrelated_windows(self):
        manager = clicker.ClickerProcess()
        manager.launch(self.data)
        self.process.returncode = 0
        self.pids = []
        manager.close_requested_at = 90.
        manager.request_close()
        self.assertFalse(manager.poll())
        self.close_windows.assert_not_called()
        self.group.terminate.assert_not_called()
        self.process.terminate.assert_not_called()
        self.group.close.assert_called_once_with()
        self.release_buttons.assert_called_once_with()

    def test_native_close_filter_addresses_only_windows_of_requested_pid(self):
        api = MagicMock()
        owners = {11: 24119, 22: 99999, 33: 24119}
        api.GetWindowThreadProcessId.side_effect = lambda hwnd, output: setattr(output._obj, "value", owners[hwnd])
        def enumerate_windows(visit, _):
            for hwnd in owners:
                visit(hwnd, 0)
            return True
        api.EnumWindows.side_effect = enumerate_windows
        with patch.object(clicker.ctypes, "WinDLL", return_value=api, create=True), \
                patch.object(clicker.ctypes, "WINFUNCTYPE", side_effect=lambda *_: lambda function: function, create=True):
            REQUEST_WINDOW_CLOSE(24119)
        self.assertEqual(api.PostMessageW.call_args_list, [call(11, 0x0010, 0, 0), call(33, 0x0010, 0, 0)])

    def test_final_mouse_cleanup_sends_only_release_flags(self):
        with patch.object(auto_chest.desktop, "_send_button") as send:
            RELEASE_MOUSE_BUTTONS()
        self.assertEqual(send.call_args_list, [call(0x0004), call(0x0010), call(0x0040)])


class ClickerAppTests(ClickerMocks, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.object(auto_chest, "BASE", self.data))
        self.show_error = self.stack.enter_context(patch("tkinter.messagebox.showerror"))
        self.stack.enter_context(patch("tkinter.messagebox.showinfo"))
        self.hotkey = self.stack.enter_context(patch.object(auto_chest.desktop, "hotkey_down", return_value=False))
        self.app = auto_chest.App.__new__(auto_chest.App)
        self.app.root = MagicMock()
        self.app.watcher = auto_chest.Watcher(queue.Queue())
        self.app.watcher.thread = MagicMock()
        self.app.watcher.thread.is_alive.return_value = False
        self.app.watcher.active.set()
        self.app.watcher.start = MagicMock()
        self.app.watcher.pause = MagicMock(wraps=self.app.watcher.pause)
        self.app.watcher.close = MagicMock(wraps=self.app.watcher.close)
        self.app.clicker = clicker.ClickerProcess()
        self.app.closing = self.app.previewing = self.app.starting = False
        self.app.clicker_opening = self.app.clicker_was_running = False
        self.app.key_states = {}
        self.app.events = queue.Queue()
        for name in ("start_button", "clicker_button", "mode_choice", "clicker_help", "status", "count", "append_log"):
            setattr(self.app, name, MagicMock())

    def open_clicker(self):
        self.app.toggle_clicker()
        self.app.finish_open_clicker()
        self.assertTrue(self.app.clicker.running)

    def test_open_pauses_and_waits_for_watcher_input_release_before_launch(self):
        self.app.watcher.thread.is_alive.return_value = True
        self.app.toggle_clicker()
        self.assertFalse(self.app.watcher.active.is_set())
        self.assertTrue(self.app.watcher.stop_event.is_set())
        self.assertTrue(self.app.clicker_opening)
        self.popen.assert_not_called()
        self.app.finish_open_clicker()
        self.popen.assert_not_called()
        self.app.root.after.assert_called_with(30, self.app.finish_open_clicker)
        self.app.watcher.thread.is_alive.return_value = False
        self.app.finish_open_clicker()
        self.popen.assert_called_once()
        self.assertFalse(self.app.clicker_opening)
        self.assertTrue(self.app.clicker_was_running)
        self.assertFalse(self.app.watcher.active.is_set())
        self.app.watcher.start.assert_not_called()

    def test_opening_suppresses_hotkeys_but_updates_their_latched_states(self):
        self.app.watcher.thread.is_alive.return_value = True
        self.app.toggle_clicker()
        self.app.toggle = MagicMock()
        self.app.close = MagicMock()
        self.hotkey.return_value = True
        self.app.poll()
        self.app.toggle.assert_not_called()
        self.app.close.assert_not_called()
        self.assertEqual(self.app.key_states, {0x77: True, 0x7B: True})
        self.popen.assert_not_called()

    def test_restart_child_keeps_hotkeys_disabled_and_natural_exit_does_not_resume_chests(self):
        self.open_clicker()
        self.app.toggle = MagicMock()
        self.app.close = MagicMock()
        self.process.returncode = 0
        self.pids = [24120]
        self.hotkey.return_value = True
        self.app.poll()
        self.assertTrue(self.app.clicker_was_running)
        self.app.toggle.assert_not_called()
        self.app.close.assert_not_called()
        self.assertEqual(self.app.key_states, {0x77: True, 0x7B: True})

        self.pids = []
        self.app.poll()
        self.assertFalse(self.app.clicker_was_running)
        self.assertFalse(self.app.watcher.active.is_set())
        self.app.watcher.start.assert_not_called()
        self.app.start_button.configure.assert_called_with(state="normal")
        self.app.clicker_button.configure.assert_called_with(state="normal", text="打开内置连点器")
        self.assertIn("保持暂停", self.app.status.set.call_args.args[0])
        self.app.toggle.assert_not_called()  # Held F8 must not become a new key press.
        self.app.close.assert_not_called()
        self.hotkey.return_value = False
        self.app.poll()
        self.hotkey.side_effect = lambda vk: vk == 0x77
        self.app.poll()
        self.app.toggle.assert_called_once_with()

    def test_failed_bundle_verification_does_not_stop_an_active_chest_watcher(self):
        with patch.object(auto_chest, "verify_clicker", side_effect=RuntimeError("校验失败")):
            self.app.toggle_clicker()
        self.assertTrue(self.app.watcher.active.is_set())
        self.app.watcher.pause.assert_not_called()
        self.app.watcher.close.assert_not_called()
        self.popen.assert_not_called()
        self.show_error.assert_called_once()

    def test_app_close_owns_clicker_shutdown_and_waits_for_group_and_watcher(self):
        self.open_clicker()
        self.app.close()
        self.assertTrue(self.app.closing)
        self.close_windows.assert_called_once_with(24119)
        self.app.root.destroy.assert_not_called()
        self.app.root.after.assert_called_with(30, self.app.finish_close)
        self.now = 102.
        self.app.watcher.thread.is_alive.return_value = True
        self.app.finish_close()
        self.group.terminate.assert_called_once_with()
        self.app.root.destroy.assert_not_called()
        self.app.watcher.thread.is_alive.return_value = False
        self.app.finish_close()
        self.app.root.destroy.assert_called_once_with()
        self.app.close()
        self.close_windows.assert_called_once_with(24119)

    def test_closing_while_waiting_for_watcher_prevents_delayed_clicker_launch(self):
        self.app.watcher.thread.is_alive.return_value = True
        self.app.toggle_clicker()
        self.app.close()
        self.app.finish_open_clicker()
        self.popen.assert_not_called()
        self.assertFalse(self.app.clicker_opening)


if __name__ == "__main__":
    unittest.main()
