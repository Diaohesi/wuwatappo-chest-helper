"""Mock-only permission checks; these tests never launch a UAC prompt."""
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import chest_windows as windows


class IntegrityLevelTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.process = 101
        self.token = 202
        self.rid = wintypes.DWORD(12288)
        self.count = ctypes.c_ubyte(1)
        self.open_process = self.stack.enter_context(patch.object(windows._kernel32, "OpenProcess", return_value=self.process))
        self.close_handle = self.stack.enter_context(patch.object(windows._kernel32, "CloseHandle", return_value=True))

        def open_token(process, access, token_pointer):
            self.assertEqual(process, self.process)
            self.assertEqual(access, 8)
            ctypes.cast(token_pointer, ctypes.POINTER(wintypes.HANDLE)).contents.value = self.token
            return True

        def token_information(token, info_class, buffer, size, needed_pointer):
            self.assertEqual(token.value, self.token)
            self.assertEqual(info_class, 25)
            ctypes.cast(needed_pointer, ctypes.POINTER(wintypes.DWORD)).contents.value = ctypes.sizeof(windows._TOKEN_MANDATORY_LABEL)
            if buffer is None:
                return False
            windows._TOKEN_MANDATORY_LABEL.from_buffer(buffer).Label.Sid = 999
            return True

        self.open_token = self.stack.enter_context(patch.object(windows._advapi32, "OpenProcessToken", side_effect=open_token))
        self.token_information = self.stack.enter_context(patch.object(windows._advapi32, "GetTokenInformation", side_effect=token_information))
        self.stack.enter_context(patch.object(windows._advapi32, "IsValidSid", return_value=True))
        self.stack.enter_context(patch.object(windows._advapi32, "GetSidSubAuthorityCount", side_effect=lambda _sid: ctypes.pointer(self.count)))
        self.subauthority = self.stack.enter_context(patch.object(windows._advapi32, "GetSidSubAuthority", side_effect=lambda _sid, _index: ctypes.pointer(self.rid)))

    def closed(self):
        return [call.args[0].value if isinstance(call.args[0], wintypes.HANDLE) else call.args[0]
                for call in self.close_handle.call_args_list]

    def test_reads_last_sid_rid_and_closes_both_handles(self):
        self.count.value = 2
        self.assertEqual(windows.integrity_level(789), 12288)
        self.subauthority.assert_called_once_with(999, 1)
        self.assertEqual(self.closed(), [self.token, self.process])

    def test_process_query_unavailable_returns_unknown(self):
        self.open_process.return_value = None
        self.assertIsNone(windows.integrity_level(789))
        self.open_token.assert_not_called()
        self.close_handle.assert_not_called()

    def test_token_query_failure_still_closes_process(self):
        self.open_token.side_effect = None
        self.open_token.return_value = False
        self.assertIsNone(windows.integrity_level(789))
        self.assertEqual(self.closed(), [self.process])

    def test_token_information_failure_closes_token_and_process(self):
        self.token_information.side_effect = None
        self.token_information.return_value = False
        self.assertIsNone(windows.integrity_level(789))
        self.assertEqual(self.closed(), [self.token, self.process])

    def test_empty_sid_is_unknown_and_cannot_underflow_subauthority(self):
        self.count.value = 0
        self.assertIsNone(windows.integrity_level(789))
        self.subauthority.assert_not_called()
        self.assertEqual(self.closed(), [self.token, self.process])


class InputPrivilegeProblemTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.window = windows.WindowInfo(22, 999, "DesktopCatInput", (0, 0, 100, 100))
        self.stack.enter_context(patch.object(windows, "_is_pet_pid", return_value=True))
        self.own_pid = 555
        self.stack.enter_context(patch.object(windows.os, "getpid", return_value=self.own_pid))
        self.levels = {555: 8192, 999: 12288}
        self.integrity = self.stack.enter_context(patch.object(windows, "integrity_level", side_effect=lambda pid: self.levels.get(pid)))

    def test_medium_helper_high_pet_reports_uipi_and_admin_action(self):
        problem = windows.input_privilege_problem([self.window])
        self.assertIn("管理员", problem)
        self.assertIn("权限不足", problem)
        self.assertIn("Windows", problem)

    def test_equal_or_higher_helper_integrity_has_no_problem(self):
        for own in (12288, 16384):
            with self.subTest(own=own):
                self.levels[555] = own
                self.assertIsNone(windows.input_privilege_problem([self.window]))

    def test_no_windows_means_no_problem_or_token_query(self):
        with patch.object(windows, "find_pet_windows", return_value=[]):
            self.assertIsNone(windows.input_privilege_problem())
        self.integrity.assert_not_called()

    def test_unknown_levels_do_not_invent_a_mismatch(self):
        for unknown in (555, 999):
            with self.subTest(unknown=unknown):
                original = self.levels[unknown]
                self.levels[unknown] = None
                self.assertIsNone(windows.input_privilege_problem([self.window]))
                self.levels[unknown] = original

    def test_repeated_windows_query_same_pet_pid_only_once(self):
        self.levels[555] = 12288
        self.assertIsNone(windows.input_privilege_problem([self.window, self.window]))
        self.assertEqual([call.args[0] for call in self.integrity.call_args_list], [555, 999])

    def test_safe_click_rejects_mismatch_before_any_input(self):
        with patch.object(windows._user32, "SetCursorPos") as move, \
                patch.object(windows, "_send_button") as send, \
                patch.object(windows, "_post_mouse_button") as post:
            with self.assertRaisesRegex(windows.PrivilegeMismatch, "权限不足"):
                windows.safe_click(50, 50, self.window)
        move.assert_not_called()
        send.assert_not_called()
        post.assert_not_called()


class ElevationRequestTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        root = Path(self.directory) / "含 空格的目录"
        root.mkdir()
        self.script = root / "宝箱助手.py"
        self.script.write_text("# test fixture only\n", encoding="utf-8")
        self.python = root / "python.exe"
        self.python.touch()
        self.pythonw = root / "pythonw.exe"
        self.pythonw.touch()
        self.stack.enter_context(patch.object(windows.sys, "executable", str(self.python)))
        self.shell = self.stack.enter_context(patch.object(windows._shell32, "ShellExecuteW", return_value=42))

    def test_standard_uac_request_uses_pythonw_and_exact_script_quoting(self):
        self.assertTrue(windows.relaunch_elevated(self.script))
        self.shell.assert_called_once_with(None, "runas", str(self.pythonw),
                                           subprocess.list2cmdline([str(self.script)]), str(self.script.parent), 1)

    def test_missing_pythonw_falls_back_to_current_executable(self):
        self.pythonw.unlink()
        self.assertTrue(windows.relaunch_elevated(self.script))
        self.assertEqual(self.shell.call_args.args[2], str(self.python))

    def test_failure_or_user_cancellation_does_not_report_success(self):
        for code in (None, 0, 5, 31, 32):
            with self.subTest(code=code):
                self.shell.return_value = code
                self.assertFalse(windows.relaunch_elevated(self.script))

    def test_shell_error_is_a_failed_request(self):
        self.shell.side_effect = OSError("UAC cancelled")
        self.assertFalse(windows.relaunch_elevated(self.script))

    def test_missing_script_cannot_start_an_elevated_process(self):
        self.assertFalse(windows.relaunch_elevated(self.script.with_name("missing.py")))
        self.shell.assert_not_called()

    def test_frozen_exe_relaunches_itself_with_quoted_arguments(self):
        executable = self.script.parent / "宝箱 助手.exe"
        executable.touch()
        arguments = ["--output", str(self.script.parent / '含 "引号" 和 空格.json'), ""]
        with patch.object(windows.sys, "frozen", True, create=True), \
                patch.object(windows.sys, "executable", str(executable)), \
                patch.object(windows.sys, "argv", [str(executable), *arguments]):
            # A one-file bundle's extracted source path need not survive.
            self.assertTrue(windows.relaunch_elevated(self.script.with_name("missing.py")))
        self.shell.assert_called_once_with(None, "runas", str(executable),
                                           subprocess.list2cmdline(arguments), str(executable.parent), 1)

    def test_frozen_exe_without_arguments_does_not_pass_extracted_script(self):
        with patch.object(windows.sys, "frozen", True, create=True), \
                patch.object(windows.sys, "argv", [str(self.python)]):
            self.assertTrue(windows.relaunch_elevated(self.script))
        self.shell.assert_called_once_with(None, "runas", str(self.python), "", str(self.python.parent), 1)

    def test_frozen_uac_cancellation_is_not_reported_as_a_restart(self):
        self.shell.return_value = 5
        with patch.object(windows.sys, "frozen", True, create=True), \
                patch.object(windows.sys, "argv", [str(self.python)]):
            self.assertFalse(windows.relaunch_elevated(self.script))

    def test_missing_frozen_exe_does_not_request_uac(self):
        with patch.object(windows.sys, "frozen", True, create=True), \
                patch.object(windows.sys, "executable", str(self.script.with_name("missing.exe"))):
            self.assertFalse(windows.relaunch_elevated(self.script))
        self.shell.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
