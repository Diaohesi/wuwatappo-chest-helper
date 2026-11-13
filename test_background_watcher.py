"""Background claim integration checks with real tiny images and no native input."""
from contextlib import ExitStack
import hashlib
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

import auto_chest
from chest_assets import load_builtin_templates
from chest_windows import WindowInfo


class BackgroundWatcherTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.now = 100.
        self.events = queue.Queue()
        self.watcher = auto_chest.Watcher(self.events)
        self.watcher.mode = "background"
        self.watcher.templates = load_builtin_templates()
        self.watcher.active.set()
        self.render = WindowInfo(101, 301, "Wuwa Tappo", (0, 0, 160, 100))
        self.input = WindowInfo(102, 301, "DesktopCatInput", (0, 0, 160, 100))
        self.windows = [self.render, self.input]
        self.desktop_available = True
        self.current = True
        self.frame = self.make_frame((20, 20, 1))
        self.fresh = True
        self.capture_error = False
        self.capture_hook = None
        self.serial = 0
        self.sent = []
        self.manager = MagicMock()
        self.manager.grab.side_effect = self.grab
        self.watcher.background_capture = self.manager

        self.stack.enter_context(patch.object(auto_chest.time, "monotonic", side_effect=lambda: self.now))
        self.stack.enter_context(patch.object(auto_chest.desktop, "is_desktop_available", side_effect=lambda: self.desktop_available))
        self.stack.enter_context(patch.object(auto_chest.desktop, "find_pet_windows", side_effect=lambda: list(self.windows)))
        self.stack.enter_context(patch.object(auto_chest.desktop, "input_privilege_problem", return_value=None))
        self.stack.enter_context(patch.object(auto_chest.desktop, "window_is_current", side_effect=lambda w: self.current and w in self.windows))
        self.stack.enter_context(patch.object(auto_chest.bg, "find_background_pairs", side_effect=lambda _windows: [(self.render, self.input)] if self.windows else []))
        self.stack.enter_context(patch.object(auto_chest.bg, "BackgroundCapture", return_value=self.manager))
        self.stack.enter_context(patch.object(auto_chest.bg, "client_origin", side_effect=lambda render: render.rect[:2]))
        self.click = self.stack.enter_context(patch.object(auto_chest.bg, "background_click", side_effect=self.record_click))

        # A covered window and an actively typing user must use this independent
        # backend; entering any visible-screen path would invalidate the test.
        self.forbidden = []
        for name in ("capture_desktop", "safe_click", "point_hits_window", "can_observe_disappearance", "user_idle_seconds"):
            self.forbidden.append(self.stack.enter_context(patch.object(auto_chest.desktop, name,
                side_effect=AssertionError(f"Visible path used: {name}"))))

    def make_frame(self, *icons):
        image = Image.new("RGB", (160, 100), (30, 45, 60))
        templates = dict(load_builtin_templates())
        for x, y, variant in icons:
            image.paste(templates[variant], (x, y))
        return image

    def grab(self, render, input_window, should_cancel=None):
        if self.capture_hook:
            self.capture_hook(self.manager.grab.call_count)
        if self.capture_error:
            raise auto_chest.desktop.CaptureUnavailable("lost window frame")
        self.assertIsNotNone(should_cancel)
        self.serial += 1
        if self.fresh:
            # A small unrelated pixel changes so each fresh frame really has
            # a different digest even when the chest artwork stays static.
            self.frame.putpixel((0, 0), (self.serial % 255, 45, 60))
        image = self.frame.copy()
        return SimpleNamespace(image=image, render=self.render, input=self.input,
                               digest=hashlib.sha256(image.tobytes()).hexdigest(), changed=self.fresh)

    def record_click(self, x, y, input_window, should_cancel=None):
        self.assertEqual(input_window, self.input)
        self.assertIsNotNone(should_cancel)
        self.assertFalse(should_cancel())
        self.sent.append((self.now, x, y))
        return True

    def tick(self, delta=1., count=1):
        for _ in range(count):
            self.now += delta
            self.watcher.tick()

    def initial_click(self):
        self.tick(count=2)
        self.assertEqual([(x, y) for _, x, y in self.sent], [(27, 26)])
        self.assertEqual(self.watcher.confirmed, 0)

    def test_covered_chest_clicks_without_idle_or_physical_input_path(self):
        self.initial_click()
        for operation in self.forbidden:
            operation.assert_not_called()
        self.assertEqual(self.watcher.pending[0].attempts, 1)

    def test_two_chests_are_clicked_and_confirmed_independently(self):
        self.frame = self.make_frame((20, 20, 1), (90, 60, 1))
        self.initial_click()
        self.tick(.5)
        self.assertEqual([(x, y) for _, x, y in self.sent], [(27, 26), (97, 66)])
        self.assertEqual(len(self.watcher.pending), 2)
        self.frame = self.make_frame((90, 60, 1))
        self.tick(.5, count=2)
        self.assertEqual(self.watcher.confirmed, 1)
        self.assertEqual(len(self.watcher.pending), 1)
        self.frame = self.make_frame()
        self.tick(.5, count=2)
        self.assertEqual(self.watcher.confirmed, 2)
        self.assertEqual(self.watcher.pending, [])
        self.assertEqual(len(self.sent), 2)

    def test_repeated_cached_missing_frame_never_finishes_confirmation(self):
        self.initial_click()
        self.frame = self.make_frame()
        self.tick()  # First actual changed picture without the target.
        self.assertEqual(self.watcher.confirmed, 0)
        self.fresh = False
        self.tick(count=4)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertTrue(self.watcher.pending)
        self.fresh = True
        self.tick()
        self.assertEqual(self.watcher.confirmed, 0)
        self.tick()
        self.assertEqual(self.watcher.confirmed, 1)

    def test_capture_loss_is_unknown_and_cannot_claim_or_retry(self):
        self.initial_click()
        self.capture_error = True
        self.tick(10., count=3)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.watcher.pending[0].attempts, 1)
        self.assertIsNone(self.watcher.pending[0].visible_since)
        self.assertTrue(self.watcher.active.is_set())
        self.manager.close.assert_called()

    def test_capture_reconnection_requires_two_new_missing_observations(self):
        self.initial_click()
        self.frame = self.make_frame()
        self.tick()
        self.capture_error = True
        self.tick()
        self.capture_error = False
        self.tick()
        self.assertEqual(self.watcher.confirmed, 0)
        self.tick()
        self.assertEqual(self.watcher.confirmed, 1)

    def test_moved_render_window_cannot_prove_old_claim_disappeared(self):
        self.initial_click()
        self.frame = self.make_frame()
        self.render = WindowInfo(101, 301, "Wuwa Tappo", (5, 0, 165, 100))
        self.windows = [self.render, self.input]
        self.tick(count=4)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(len(self.sent), 1)

    def test_geometry_change_during_fresh_verification_prevents_click(self):
        self.tick()
        def move_at_verification(number):
            if number == 3:
                self.render = WindowInfo(101, 301, "Wuwa Tappo", (5, 0, 165, 100))
                self.windows = [self.render, self.input]
        self.capture_hook = move_at_verification
        self.tick()
        self.assertEqual(self.sent, [])

    def test_client_origin_change_with_unchanged_outer_rect_prevents_click(self):
        def grab_with_captured_origin(render, input_window, should_cancel=None):
            frame = self.grab(render, input_window, should_cancel)
            # The first stable observation and selected frame share the old
            # client origin. Only the verification frame uses the new origin;
            # both outer window rectangles remain exactly the same.
            frame.origin = (5, 0) if self.manager.grab.call_count >= 3 else (0, 0)
            return frame

        self.manager.grab.side_effect = grab_with_captured_origin
        # Every live query now sees the new origin. Re-querying it for both
        # frames would hide the geometry change captured in frame.origin.
        with patch.object(auto_chest.bg, "client_origin", return_value=(5, 0)):
            self.tick(count=2)
        self.assertEqual(self.manager.grab.call_count, 3)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.watcher.pending, [])
        self.assertEqual(self.watcher.clicks, 0)

    def test_pause_during_fresh_verification_prevents_click(self):
        self.tick()
        self.capture_hook = lambda number: self.watcher.pause() if number == 3 else None
        self.tick()
        self.assertEqual(self.sent, [])

    def test_lock_neither_captures_nor_confirms_and_releases_capture_resources(self):
        self.initial_click()
        count = self.manager.grab.call_count
        self.desktop_available = False
        self.tick(20., count=3)
        self.assertEqual(self.manager.grab.call_count, count)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(len(self.sent), 1)
        self.manager.close.assert_called()

    def test_unlock_resumes_with_new_observations_and_retains_pending_claim(self):
        self.initial_click()
        self.frame = self.make_frame()
        self.tick()
        self.desktop_available = False
        self.tick(30.)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(len(self.watcher.pending), 1)
        self.desktop_available = True
        self.tick()
        self.assertEqual(self.watcher.confirmed, 0)
        self.tick()
        self.assertEqual(self.watcher.confirmed, 1)
        self.assertTrue(self.watcher.active.is_set())
        self.assertEqual(len(self.sent), 1)

    def test_explicit_mode_change_drops_old_evidence_but_retains_totals(self):
        self.initial_click()
        self.watcher.confirmed = 7
        old_gate, old_ledger = self.watcher.gate, self.watcher.ledger
        with patch.object(auto_chest.threading, "Thread") as thread:
            self.watcher.start(self.watcher.templates, .94, 2., mode="visible")
        thread.return_value.start.assert_called_once_with()
        self.assertEqual(self.watcher.mode, "visible")
        self.assertIsNot(self.watcher.gate, old_gate)
        self.assertIsNot(self.watcher.ledger, old_ledger)
        self.assertEqual(self.watcher.pending, [])
        self.assertEqual(self.watcher.ledger.locks, [])
        self.assertEqual(self.watcher.clicks, 1)
        self.assertEqual(self.watcher.confirmed, 7)

    def test_three_failed_attempts_pause_without_real_input_fallback(self):
        self.initial_click()
        # Two stable frames per retry and at least four seconds between sends.
        self.tick(2.1, count=6)
        self.assertEqual(len(self.sent), 3)
        self.assertFalse(self.watcher.active.is_set())
        self.assertEqual(self.watcher.confirmed, 0)
        for operation in self.forbidden:
            operation.assert_not_called()

    def test_negative_monitor_origin_is_applied_to_background_coordinates(self):
        self.render = WindowInfo(101, 301, "Wuwa Tappo", (-160, -100, 0, 0))
        self.input = WindowInfo(102, 301, "DesktopCatInput", (-160, -100, 0, 0))
        self.windows = [self.render, self.input]
        self.tick(count=2)
        self.assertEqual([(x, y) for _, x, y in self.sent], [(-133, -74)])

    def test_privilege_mismatch_pauses_before_background_capture(self):
        with patch.object(auto_chest.desktop, "input_privilege_problem", return_value="权限不足"):
            self.tick()
        self.manager.grab.assert_not_called()
        self.assertEqual(self.sent, [])
        self.assertFalse(self.watcher.active.is_set())
        self.assertTrue(any(kind == "privilege" for kind, _ in list(self.events.queue)))


if __name__ == "__main__":
    unittest.main()
