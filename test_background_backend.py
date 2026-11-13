"""Native-boundary regression tests; never capture or click a real window."""
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

import chest_background as bg
from chest_windows import WindowInfo, CaptureUnavailable, PrivilegeMismatch


class NativeFixture(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.render = WindowInfo(101, 301, "Wuwa Tappo", (100, 50, 260, 150))
        self.input = WindowInfo(102, 301, "DesktopCatInput", self.render.rect)
        self.pids = {101: 301, 102: 301, 999: 900}
        self.rects = {101: self.render.rect, 102: self.input.rect}
        self.classes = {101: "Tauri Window", 102: "DesktopCatInput"}
        self.available, self.foreground, self.now = True, 999, 100.
        self.cancelled = False
        self.messages = []
        self.send_hook = None
        self.sleep_hook = None
        self.user32 = MagicMock()
        self.user32.IsWindow.return_value = True
        self.user32.IsWindowVisible.return_value = True
        self.user32.IsIconic.return_value = False
        self.user32.GetForegroundWindow.side_effect = lambda: self.foreground
        self.user32.ScreenToClient.side_effect = self.screen_to_client
        self.user32.SendMessageTimeoutW.side_effect = self.native_send
        for name in ("SetCursorPos", "SendInput", "SetForegroundWindow", "SetWindowPos", "BlockInput"):
            getattr(self.user32, name).side_effect = AssertionError(f"Unexpected physical operation: {name}")
        self.stack.enter_context(patch.object(bg, "_user32", self.user32))
        self.stack.enter_context(patch.object(bg.desktop, "_user32", self.user32))
        self.stack.enter_context(patch.object(bg.desktop, "_require_windows"))
        self.stack.enter_context(patch.object(bg.desktop, "_pid", side_effect=lambda hwnd: self.pids.get(hwnd, 0)))
        self.stack.enter_context(patch.object(bg.desktop, "_is_pet_pid", side_effect=lambda pid: pid == 301))
        self.stack.enter_context(patch.object(bg.desktop, "_window_rect", side_effect=lambda hwnd: self.rects.get(hwnd)))
        self.stack.enter_context(patch.object(bg.desktop, "_window_class", side_effect=lambda hwnd: self.classes.get(hwnd)))
        self.stack.enter_context(patch.object(bg.desktop, "is_desktop_available", side_effect=lambda: self.available))
        self.privilege = self.stack.enter_context(patch.object(bg.desktop, "input_privilege_problem", return_value=None))
        self.stack.enter_context(patch.object(bg.desktop, "user_idle_seconds", side_effect=AssertionError("Background mode must not require idle")))
        self.size = self.stack.enter_context(patch.object(bg, "_client_size", return_value=(160, 100)))
        self.origin = self.stack.enter_context(patch.object(bg, "client_origin", return_value=(100, 50)))
        self.stack.enter_context(patch.object(bg.time, "monotonic", side_effect=lambda: self.now))
        self.stack.enter_context(patch.object(bg.time, "sleep", side_effect=self.sleep))

    def sleep(self, seconds):
        self.now += seconds
        if self.sleep_hook:
            self.sleep_hook()

    def screen_to_client(self, hwnd, pointer):
        point = ctypes.cast(pointer, ctypes.POINTER(wintypes.POINT)).contents
        point.x -= self.rects[hwnd][0]
        point.y -= self.rects[hwnd][1]
        return True

    def native_send(self, hwnd, message, wparam, lparam, flags, timeout, result):
        self.messages.append((message, wparam, lparam))
        return self.send_hook(message) if self.send_hook else True

    def click(self):
        return bg.background_click(120, 80, self.input, lambda: self.cancelled)

    def codes(self):
        return [message[0] for message in self.messages]


class BackgroundPairTests(NativeFixture):
    def test_pair_requires_same_verified_process_and_distinct_native_layer(self):
        self.assertEqual(bg.find_background_pairs([self.render, self.input]), [(self.render, self.input)])
        other = WindowInfo(103, 302, "DesktopCatInput", self.input.rect)
        self.classes[103] = "DesktopCatInput"
        self.assertEqual(bg.find_background_pairs([self.render, other]), [])

    def test_ambiguous_render_or_input_and_changed_native_class_are_rejected(self):
        duplicate = WindowInfo(103, 301, "Wuwa Tappo", self.render.rect)
        self.assertEqual(bg.find_background_pairs([self.render, duplicate, self.input]), [])
        duplicate = WindowInfo(103, 301, "DesktopCatInput", self.input.rect)
        self.classes[103] = "DesktopCatInput"
        self.assertEqual(bg.find_background_pairs([self.render, self.input, duplicate]), [])
        self.classes[102] = "UnrelatedPopup"
        self.assertEqual(bg.find_background_pairs([self.render, self.input]), [])


class BackgroundInputTests(NativeFixture):
    def test_screen_coordinates_translate_to_client_without_focus_or_pointer_change(self):
        self.assertTrue(self.click())
        expected = 20 | (30 << 16)
        self.assertIn((0x0201, 1, expected), self.messages)
        self.assertIn((0x0202, 0, expected), self.messages)
        self.assertEqual(self.foreground, 999)
        for name in ("SetCursorPos", "SendInput", "SetForegroundWindow", "SetWindowPos", "BlockInput"):
            getattr(self.user32, name).assert_not_called()

    def test_lock_cancel_and_out_of_bounds_send_no_button_messages(self):
        self.available = False
        self.assertFalse(self.click())
        self.available = True
        self.cancelled = True
        self.assertFalse(self.click())
        self.cancelled = False
        self.assertFalse(bg.background_click(99, 80, self.input))
        self.assertEqual(self.messages, [])

    def test_changed_pid_class_or_geometry_blocks_input(self):
        for mutate, restore in (
            (lambda: self.pids.update({102: 302}), lambda: self.pids.update({102: 301})),
            (lambda: self.classes.update({102: "AnotherClass"}), lambda: self.classes.update({102: "DesktopCatInput"})),
            (lambda: self.rects.update({102: (0, 0, 160, 100)}), lambda: self.rects.update({102: self.input.rect})),
        ):
            mutate()
            self.assertFalse(self.click())
            restore()
        self.assertEqual(self.messages, [])

    def test_privilege_mismatch_is_reported_before_any_input(self):
        self.privilege.return_value = "elevation mismatch"
        with self.assertRaises(PrivilegeMismatch):
            self.click()
        self.assertEqual(self.messages, [])

    def test_cancellation_or_move_during_hover_never_sends_down(self):
        self.sleep_hook = lambda: setattr(self, "cancelled", True)
        self.assertFalse(self.click())
        self.assertNotIn(0x0201, self.codes())
        self.cancelled = False
        self.sleep_hook = lambda: self.rects.update({102: (0, 0, 160, 100)})
        self.assertFalse(self.click())
        self.assertNotIn(0x0201, self.codes())

    def test_final_visibility_check_after_hover_can_reject_without_button_input(self):
        def reject_covered_target():
            self.assertIn(0x0200, self.codes())
            self.assertGreaterEqual(self.now, 100.10)
            self.assertNotIn(0x0201, self.codes())
            return False
        before_press = MagicMock(side_effect=reject_covered_target)
        self.assertFalse(bg.background_click(120, 80, self.input,
            lambda: self.cancelled, before_press=before_press))
        before_press.assert_called_once_with()
        self.assertNotIn(0x0201, self.codes())
        self.assertNotIn(0x0202, self.codes())

    def test_target_disappearing_after_down_does_not_repeat_final_visibility_check(self):
        self.chest_visible = True
        before_press = MagicMock(side_effect=lambda: self.chest_visible)
        self.send_hook = lambda code: (setattr(self, "chest_visible", False) or True) if code == 0x0201 else True
        self.assertTrue(bg.background_click(120, 80, self.input,
            lambda: self.cancelled, before_press=before_press))
        self.assertFalse(self.chest_visible)
        before_press.assert_called_once_with()
        self.assertEqual(self.codes().count(0x0201), 1)
        self.assertEqual(self.codes().count(0x0202), 1)

    def test_pause_during_final_visibility_check_still_prevents_down(self):
        def pause_during_observation():
            self.cancelled = True
            return True
        self.assertFalse(bg.background_click(120, 80, self.input,
            lambda: self.cancelled, before_press=pause_during_observation))
        self.assertNotIn(0x0201, self.codes())
        self.assertNotIn(0x0202, self.codes())

    def test_cancel_and_lock_after_down_still_send_release(self):
        for field in ("cancelled", "available"):
            self.messages.clear()
            self.cancelled, self.available = False, True
            self.send_hook = lambda code: (setattr(self, field, field == "cancelled") or True) if code == 0x0201 else True
            self.assertFalse(self.click())
            self.assertEqual(self.codes().count(0x0201), 1)
            self.assertEqual(self.codes().count(0x0202), 1)

    def test_down_timeout_releases_and_preserves_original_error(self):
        self.send_hook = lambda code: code != 0x0201
        with self.assertRaisesRegex(OSError, "0x201"):
            self.click()
        self.assertEqual(self.codes().count(0x0202), 1)

    def test_release_failure_is_retried_with_a_bound(self):
        self.send_hook = lambda code: code not in (0x0201, 0x0202)
        with self.assertRaisesRegex(OSError, "0x201") as caught:
            self.click()
        self.assertEqual(self.codes().count(0x0202), 2)
        self.assertTrue(caught.exception.__notes__)

    def test_reused_handle_after_down_does_not_receive_release(self):
        self.send_hook = lambda code: (self.pids.update({102: 302}) or True) if code == 0x0201 else True
        self.assertFalse(self.click())
        self.assertNotIn(0x0202, self.codes())

    def test_pet_foregrounded_by_user_is_not_fake_deactivated(self):
        self.send_hook = lambda code: (setattr(self, "foreground", 101) or True) if code == 0x0201 else True
        self.assertTrue(self.click())
        self.assertNotIn((0x0006, 0, 0), self.messages)
        self.assertNotIn(0x02a3, self.codes())


class BackgroundCaptureTests(NativeFixture):
    def setUp(self):
        super().setUp()
        self.size.return_value = (4, 3)
        self.context = MagicMock()
        self.connection, self.child = MagicMock(), MagicMock()
        self.context.Pipe.return_value = (self.connection, self.child)
        self.process = self.context.Process.return_value
        self.alive = True
        self.process.is_alive.side_effect = lambda: self.alive
        self.process.terminate.side_effect = lambda: setattr(self, "alive", False)
        self.process.kill.side_effect = lambda: setattr(self, "alive", False)
        self.connection.poll.return_value = True
        self.connection.recv.return_value = self.payload()
        self.stack.enter_context(patch.object(bg.multiprocessing, "get_context", return_value=self.context))
        self.capture = bg.BackgroundCapture(timeout=.2, poll_interval=.05)
        self.addCleanup(self.capture.close)

    def payload(self, color=20):
        image = Image.new("RGB", (4, 3), (10, 11, 12))
        image.putpixel((0, 0), (color, 21, 22))
        return {"ok": True, "size": image.size, "rgb": image.tobytes()}

    def grab(self):
        return self.capture.grab(self.render, self.input, lambda: self.cancelled)

    def test_first_and_repeated_cached_frames_are_not_fresh_changes(self):
        self.connection.recv.side_effect = [self.payload(), self.payload(), self.payload(99)]
        first, repeated, changed = self.grab(), self.grab(), self.grab()
        self.assertEqual(first.image.size, (4, 3))
        self.assertEqual(first.origin, (100, 50))
        self.assertFalse(first.changed)
        self.assertFalse(repeated.changed)
        self.assertTrue(changed.changed)
        self.assertEqual(first.digest, repeated.digest)
        self.context.Process.assert_called_once()
        self.assertEqual(self.connection.send.call_args_list[0].args, ("grab",))
        self.user32.SendMessageTimeoutW.assert_not_called()

    def test_timeout_ends_capture_process_and_releases_resources(self):
        def no_result(seconds):
            self.now += seconds
            return False
        self.connection.poll.side_effect = no_result
        with self.assertRaises(CaptureUnavailable):
            self.grab()
        self.process.terminate.assert_called_once()
        self.connection.close.assert_called_once()
        self.assertIsNone(self.capture._connection)
        self.assertLess(self.now, 100.3)

    def test_lock_or_cancel_while_waiting_ends_capture_process(self):
        for field in ("cancelled", "available"):
            self.cancelled, self.available, self.alive = False, True, True
            def interrupted(_timeout):
                setattr(self, field, field == "cancelled")
                return False
            self.connection.poll.side_effect = interrupted
            with self.assertRaises(CaptureUnavailable):
                self.grab()
            self.assertFalse(self.alive)

    def test_locked_desktop_does_not_even_start_the_worker(self):
        self.available = False
        with self.assertRaises(CaptureUnavailable):
            self.grab()
        self.context.Process.assert_not_called()

    def test_invalid_frame_and_native_errors_are_unknown_not_missing_chests(self):
        invalid = [
            {"ok": False, "error": "native capture failed"},
            {"ok": True, "size": (4, 3), "rgb": bytes(3)},
            {"ok": True, "size": (3, 4), "rgb": bytes(36)},
            {"ok": True, "size": (4, 3), "rgb": bytes(36)},
        ]
        for payload in invalid:
            self.alive = True
            self.connection.recv.return_value = payload
            with self.subTest(payload=payload), self.assertRaises(CaptureUnavailable):
                self.grab()
            self.assertIsNone(self.capture._digest)

    def test_geometry_changes_during_capture_discard_the_frame(self):
        def moved():
            self.rects[101] = (0, 0, 160, 100)
            return self.payload()
        self.connection.recv.side_effect = moved
        with self.assertRaises(CaptureUnavailable):
            self.grab()
        self.process.terminate.assert_called_once()

    def test_forced_worker_cleanup_is_bounded_even_if_terminate_fails(self):
        self.grab()
        self.process.terminate.side_effect = None
        self.capture.close()
        self.process.kill.assert_called_once()
        self.assertEqual([call.kwargs for call in self.process.join.call_args_list], [{"timeout": .5}, {"timeout": .5}])


class RuntimeSelfTestTests(unittest.TestCase):
    def test_worker_loads_only_pinned_runtime_libraries(self):
        library = MagicMock()
        library.framework_libpath = Path("packaged/maa/bin/MaaFramework.dll")
        library.version.return_value = "v5.16.0"
        package = ModuleType("maa")
        connection = MagicMock()
        with patch.dict(sys.modules, {"maa": package, "maa.library": SimpleNamespace(Library=library)}), \
                patch("importlib.metadata.version", return_value="5.16.0"), \
                patch.object(bg.ctypes, "WinDLL") as load:
            bg._runtime_self_test_worker(connection)
        report = connection.send.call_args.args[0]
        self.assertTrue(report["ok"])
        self.assertEqual(report["sdk_version"], "5.16.0")
        self.assertEqual(report["framework_version"], "5.16.0")
        library.framework.assert_called_once()
        library.toolkit.assert_called_once()
        self.assertTrue(load.call_args.args[0].endswith("MaaWin32ControlUnit.dll"))

    def test_wrong_sdk_version_is_reported(self):
        connection = MagicMock()
        with patch("importlib.metadata.version", return_value="5.15.0"):
            bg._runtime_self_test_worker(connection)
        self.assertFalse(connection.send.call_args.args[0]["ok"])
        self.assertIn("5.16.0", connection.send.call_args.args[0]["error"])

    def test_missing_native_dll_is_reported_without_a_controller(self):
        library = MagicMock()
        library.framework_libpath = Path("packaged/maa/bin/MaaFramework.dll")
        connection = MagicMock()
        with patch.dict(sys.modules, {"maa": ModuleType("maa"), "maa.library": SimpleNamespace(Library=library)}), \
                patch("importlib.metadata.version", return_value="5.16.0"), \
                patch.object(bg.ctypes, "WinDLL", side_effect=OSError("missing native dependency")):
            bg._runtime_self_test_worker(connection)
        report = connection.send.call_args.args[0]
        self.assertFalse(report["ok"])
        self.assertIn("missing native dependency", report["error"])

    def test_self_test_timeout_and_malformed_reply_always_close_child(self):
        for ready, reply in ((False, None), (True, {"unexpected": 1}), (True, {"ok": True})):
            context = MagicMock()
            parent, child = MagicMock(), MagicMock()
            context.Pipe.return_value = (parent, child)
            process = context.Process.return_value
            process.is_alive.side_effect = [True, False, False]
            parent.poll.return_value, parent.recv.return_value = ready, reply
            with patch.object(bg.multiprocessing, "get_context", return_value=context):
                report = bg.runtime_self_test(timeout=.1)
            self.assertEqual(report["ok"], ready and reply == {"ok": True})
            parent.poll.assert_called_once_with(.1)
            parent.close.assert_called_once()
            process.terminate.assert_called_once()
            self.assertIs(context.Process.call_args.kwargs["target"], bg._runtime_self_test_worker)


if __name__ == "__main__":
    unittest.main()
