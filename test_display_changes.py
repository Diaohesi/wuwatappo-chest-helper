"""Display-change recovery through the real capture function and mocked OS I/O."""
from contextlib import ExitStack
import queue
import unittest
from unittest.mock import patch

from PIL import Image

import auto_chest
from chest_assets import load_builtin_templates
import chest_windows as desktop
from chest_windows import WindowInfo


class DisplayChangeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.now = 100.
        self.virtual = (0, 0, 160, 100)
        self.frame = self.make_frame(chest=True)
        self.capture_hook = None
        self.window = WindowInfo(52, 72, "DesktopCatInput", (0, 0, 160, 100))
        self.sent = []
        self.watcher = auto_chest.Watcher(queue.Queue())
        self.watcher.templates = load_builtin_templates()
        self.watcher.active.set()
        self.stack.enter_context(patch.object(auto_chest.time, "monotonic", side_effect=lambda: self.now))
        self.stack.enter_context(patch.object(desktop, "_require_windows"))
        self.stack.enter_context(patch.object(desktop, "is_desktop_available", return_value=True))
        self.stack.enter_context(patch.object(desktop, "virtual_rect", side_effect=lambda: self.virtual))
        self.stack.enter_context(patch.object(desktop.displays, "monitors", side_effect=lambda: (
            desktop.displays.Monitor('mock', self.virtual, True, 96),)))
        self.stack.enter_context(patch.object(desktop, "find_pet_windows", side_effect=lambda: [self.window]))
        self.stack.enter_context(patch.object(desktop, "input_privilege_problem", return_value=None))
        self.stack.enter_context(patch.object(desktop, "window_is_current", return_value=True))
        self.stack.enter_context(patch.object(desktop, "point_hits_window", return_value=True))
        self.stack.enter_context(patch.object(desktop, "can_observe_disappearance", return_value=True))
        self.stack.enter_context(patch.object(desktop, "user_idle_seconds", return_value=10.))
        self.grab = self.stack.enter_context(patch.object(desktop.ImageGrab, "grab", side_effect=self.grab_image))
        self.stack.enter_context(patch.object(desktop, "safe_click", side_effect=self.record_click))

    def make_frame(self, chest=False, size=(160, 100)):
        image = Image.new("RGB", size, (30, 45, 60))
        # Include one nonuniform pixel even without the icon, so the capture
        # passes its separate pure-colour protection after a display change.
        image.putpixel((0, 0), (31, 45, 60))
        if chest:
            image.paste(load_builtin_templates()[0][1], (20, 20))
        return image

    def grab_image(self, **kwargs):
        self.assertEqual(kwargs, {"all_screens": True, "include_layered_windows": True})
        if self.capture_hook:
            self.capture_hook(self.grab.call_count)
        return self.frame.copy()

    def record_click(self, x, y, window, should_cancel=None):
        self.assertEqual(window, self.window)
        self.assertFalse(should_cancel())
        self.sent.append((x, y))
        return True

    def tick(self, count=1):
        for _ in range(count):
            self.now += 1.
            self.watcher.tick()

    def test_inconsistent_display_size_raises_retryable_capture_error(self):
        for size in ((159, 100), (160, 101)):
            with self.subTest(actual_size=size):
                self.frame = self.make_frame(size=size)
                with self.assertRaises(desktop.CaptureUnavailable):
                    desktop.capture_desktop()

    def test_stable_changed_display_returns_new_size_and_negative_origin(self):
        self.virtual = (-320, -100, 0, 100)
        self.frame = self.make_frame(size=(320, 200))
        image, origin = desktop.capture_desktop()
        self.assertEqual(image.size, (320, 200))
        self.assertEqual(origin, (-320, -100))
        self.assertEqual(self.sent, [])

    def test_display_change_does_not_confirm_pending_claim_and_recovery_resumes(self):
        self.tick(count=2)
        self.assertEqual(self.sent, [(27, 26)])
        claim = self.watcher.pending[0]
        self.frame = self.make_frame(size=(120, 80))
        self.tick(count=3)
        self.assertTrue(self.watcher.active.is_set())
        self.assertTrue(self.watcher.waiting_for_screen)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(claim.attempts, 1)
        self.assertEqual(claim.lock.missing_frames, 0)
        self.assertIsNone(claim.visible_since)
        self.assertEqual(self.sent, [(27, 26)])

        self.virtual = (0, 0, 320, 200)
        self.frame = self.make_frame(size=(320, 200))
        self.tick()
        self.assertFalse(self.watcher.waiting_for_screen)
        self.assertEqual(self.watcher.confirmed, 0)
        self.tick()
        self.assertEqual(self.watcher.confirmed, 1)
        self.assertEqual(self.watcher.pending, [])
        self.assertEqual(self.sent, [(27, 26)])

    def test_display_change_during_fresh_verification_requires_new_stable_frames(self):
        def change_at_verification(number):
            if number == 3:
                self.frame = self.make_frame(size=(120, 80))
        self.capture_hook = change_at_verification
        self.tick(count=2)
        self.assertEqual(self.grab.call_count, 3)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.watcher.pending, [])
        self.assertTrue(self.watcher.active.is_set())

        self.capture_hook = None
        self.frame = self.make_frame(chest=True)
        self.tick()
        self.assertEqual(self.sent, [])
        self.tick()
        self.assertEqual(self.sent, [(27, 26)])
        self.assertEqual(self.watcher.pending[0].attempts, 1)


if __name__ == "__main__":
    unittest.main()
