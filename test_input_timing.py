"""Mock-only checks for frame-visible input and guaranteed button release."""
from contextlib import ExitStack
import unittest
from unittest.mock import patch

import chest_windows as windows


class InputTimingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.window = windows.WindowInfo(123, 456, "DesktopCatInput", (0, 0, 1920, 1080))
        self.cursor = (900, 600)
        self.time = 0.
        self.actions = []
        self.stack.enter_context(patch.object(windows, "cursor_pos", side_effect=lambda: self.cursor))
        self.stack.enter_context(patch.object(windows, "hotkey_down", return_value=False))
        self.stack.enter_context(patch.object(windows, "is_desktop_available", return_value=True))
        self.stack.enter_context(patch.object(windows, "window_is_current", return_value=True))
        self.owns = self.stack.enter_context(patch.object(windows, "_point_hits_window", return_value=True))
        self.window_class = self.stack.enter_context(patch.object(windows, "_window_class", return_value="StandardWindow"))
        self.stack.enter_context(patch.object(windows.time, "monotonic", side_effect=lambda: self.time))
        self.stack.enter_context(patch.object(windows.time, "sleep", side_effect=self.sleep))
        self.stack.enter_context(patch.object(windows._user32, "SetCursorPos", side_effect=self.move))
        self.send = self.stack.enter_context(patch.object(windows, "_send_button", side_effect=self.button))

    def sleep(self, seconds):
        self.time += seconds

    def move(self, x, y):
        self.cursor = (x, y)
        self.actions.append(("move", self.time, self.cursor))
        return True

    def button(self, flag):
        self.actions.append((flag, self.time, self.cursor))

    def test_hover_hold_and_release_are_visible_across_frames(self):
        self.assertTrue(windows.safe_click(158, 809, self.window))
        down, up = [a for a in self.actions if isinstance(a[0], int)]
        self.assertGreaterEqual(down[1], .099)
        self.assertGreaterEqual(up[1]-down[1], .099)
        self.assertGreaterEqual(self.actions[-1][1]-up[1], .149)
        self.assertEqual(down[2], (158, 809))
        self.assertEqual(up[2], (158, 809))
        self.assertEqual(self.cursor, (900, 600))

    def test_cancel_before_press_emits_no_button_events(self):
        self.assertFalse(windows.safe_click(158, 809, self.window, should_cancel=lambda: self.time > .04))
        self.send.assert_not_called()

    def test_cancel_while_pressed_always_releases(self):
        self.assertFalse(windows.safe_click(158, 809, self.window, should_cancel=lambda: self.time > .14))
        self.assertEqual([a[0] for a in self.actions if isinstance(a[0], int)], [2, 4])

    def test_window_occluded_while_pressed_always_releases(self):
        self.owns.side_effect = lambda *args: self.time < .14
        self.assertFalse(windows.safe_click(158, 809, self.window))
        self.assertEqual([a[0] for a in self.actions if isinstance(a[0], int)], [2, 4])

    def test_user_movement_is_not_undone_to_release_or_restore(self):
        def user_move_sleep(seconds):
            self.time += seconds
            if self.time > .14:
                self.cursor = (500, 400)
        with patch.object(windows.time, "sleep", side_effect=user_move_sleep):
            self.assertFalse(windows.safe_click(158, 809, self.window))
        self.assertEqual(self.cursor, (500, 400))
        self.assertEqual(self.actions[-1][0], 4)
        self.assertEqual(self.actions[-1][2], (500, 400))

    def test_failed_release_is_retried_and_error_is_preserved(self):
        def fail_first_up(flag):
            self.actions.append((flag, self.time, self.cursor))
            if self.send.call_count == 2:
                raise OSError("transient release failure")
        self.send.side_effect = fail_first_up
        with self.assertRaises(OSError):
            windows.safe_click(158, 809, self.window)
        self.assertEqual([a[0] for a in self.actions if isinstance(a[0], int)], [2, 4, 4])

    def test_pet_overlay_keeps_native_mouse_routing_after_privilege_check(self):
        self.window_class.return_value = "DesktopCatInput"
        with patch.object(windows, "_post_mouse_button") as post:
            self.assertTrue(windows.safe_click(158, 809, self.window))
        post.assert_not_called()
        self.assertEqual([call.args for call in self.send.call_args_list], [(2,), (4,)])
        events = [a for a in self.actions if isinstance(a[0], int)]
        self.assertGreaterEqual(events[1][1]-events[0][1], .099)

    def test_overlay_cancel_also_releases_the_original_window(self):
        self.window_class.return_value = "DesktopCatInput"
        with patch.object(windows, "_post_mouse_button") as post:
            self.assertFalse(windows.safe_click(158, 809, self.window, should_cancel=lambda: self.time > .14))
        post.assert_not_called()
        self.assertEqual([call.args[0] for call in self.send.call_args_list], [2, 4])


class CaptureAvailabilityTests(unittest.TestCase):
    def test_uniform_capture_cannot_be_used_as_disappearance_evidence(self):
        from PIL import Image
        with patch.object(windows, "is_desktop_available", return_value=True), \
                patch.object(windows, "virtual_rect", return_value=(0, 0, 32, 24)), \
                patch.object(windows.ImageGrab, "grab", return_value=Image.new("RGB", (32, 24), (0, 72, 126))):
            with self.assertRaises(windows.CaptureUnavailable):
                windows.capture_desktop()


if __name__ == "__main__":
    unittest.main()
