"""Result confirmation and bounded retries; all desktop operations are mocked."""
from contextlib import ExitStack
import queue
import unittest
from unittest.mock import patch

from PIL import Image

import auto_chest
from chest_assets import load_builtin_templates
from chest_windows import WindowInfo


class ClaimConfirmationTests(unittest.TestCase):
    def test_higher_integrity_pet_pauses_before_capture_or_click(self):
        with patch.object(auto_chest.desktop, "input_privilege_problem", return_value="桌宠以管理员权限运行"), \
                patch.object(auto_chest.desktop, "capture_desktop") as capture:
            self.tick()
        capture.assert_not_called()
        self.assertEqual(self.sent, [])
        self.assertFalse(self.watcher.active.is_set())
        self.assertTrue(any(kind == "privilege" for kind, _ in self.drain()))

    def test_unavailable_capture_neither_confirms_nor_retries(self):
        self.initial_click()
        with patch.object(auto_chest.desktop, "capture_desktop", side_effect=auto_chest.desktop.CaptureUnavailable("blank")):
            self.tick(10., count=3)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.watcher.active.is_set())
        self.assertEqual(self.watcher.pending[0].attempts, 1)
        self.assertIsNone(self.watcher.pending[0].visible_since)

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.now = 100.
        self.events = queue.Queue()
        self.watcher = auto_chest.Watcher(self.events)
        self.watcher.templates = load_builtin_templates()
        self.watcher.active.set()
        self.window = WindowInfo(51, 72, "DesktopCatInput", (0, 0, 160, 100))
        self.windows = [self.window]
        self.desktop_available = True
        self.occluded = False
        self.clickthrough = False
        self.idle = 10.
        self.frame = self.make_frame((20, 20, 1))
        self.sent = []
        self.stack.enter_context(patch.object(auto_chest.time, "monotonic", side_effect=lambda: self.now))
        self.stack.enter_context(patch.object(auto_chest.desktop, "is_desktop_available", side_effect=lambda: self.desktop_available))
        self.stack.enter_context(patch.object(auto_chest.desktop, "find_pet_windows", side_effect=lambda: self.windows))
        self.stack.enter_context(patch.object(auto_chest.desktop, "capture_desktop", side_effect=lambda: (self.frame.copy(), (0, 0))))
        self.stack.enter_context(patch.object(auto_chest.desktop, "point_hits_window", side_effect=lambda *_: not (self.occluded or self.clickthrough)))
        self.stack.enter_context(patch.object(auto_chest.desktop, "can_observe_disappearance", side_effect=lambda *_: not self.occluded))
        self.stack.enter_context(patch.object(auto_chest.desktop, "window_is_current", return_value=True))
        self.stack.enter_context(patch.object(auto_chest.desktop, "user_idle_seconds", side_effect=lambda: self.idle))

        def record_click(x, y, window, should_cancel=None):
            self.assertEqual(window, self.window)
            self.assertIsNotNone(should_cancel)
            self.assertFalse(should_cancel())
            self.sent.append((self.now, x, y))
            return True

        self.click = self.stack.enter_context(patch.object(auto_chest.desktop, "safe_click", side_effect=record_click))

    def make_frame(self, *icons):
        image = Image.new("RGB", (160, 100), (30, 45, 60))
        templates = dict(load_builtin_templates())
        for x, y, variant in icons:
            image.paste(templates[variant], (x, y))
        return image

    def tick(self, delta=1., count=1):
        for _ in range(count):
            self.now += delta
            self.watcher.tick()

    def initial_click(self):
        self.tick(count=2)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.watcher.confirmed, 0)

    def drain(self):
        result = []
        while not self.events.empty():
            result.append(self.events.get_nowait())
        return result

    def test_sent_input_is_pending_until_two_visible_missing_frames(self):
        self.initial_click()
        self.assertEqual(len(self.watcher.pending), 1)
        messages = self.drain()
        self.assertTrue(any(kind == "log" and "已发出" in text for kind, text in messages))
        self.assertFalse(any(kind == "confirmed" for kind, _ in messages))
        self.frame = self.make_frame()
        self.tick()
        self.assertEqual(self.watcher.confirmed, 0)
        self.tick()
        self.assertEqual(self.watcher.confirmed, 1)
        self.assertEqual(self.watcher.pending, [])
        self.assertEqual(self.watcher.ledger.locks, [])
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(any(kind == "log" and "已确认" in text for kind, text in self.drain()))

    def test_disappeared_icon_can_make_its_old_location_clickthrough(self):
        self.initial_click()
        self.frame = self.make_frame()  # No countdown text is drawn.
        self.clickthrough = True
        self.tick()
        self.assertEqual(self.watcher.confirmed, 0)
        self.tick()
        self.assertEqual(self.watcher.confirmed, 1)
        self.assertEqual(self.watcher.pending, [])
        self.assertEqual(len(self.sent), 1)

        # The same slot becomes clickable again after its independent refresh.
        self.tick(1800.)
        self.clickthrough = False
        self.frame = self.make_frame((20, 20, 1))
        self.tick(count=2)
        self.assertEqual(len(self.sent), 2)

    def test_visible_icon_at_clickthrough_point_neither_confirms_nor_retries(self):
        self.initial_click()
        self.clickthrough = True
        self.tick(10., count=3)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(self.watcher.pending[0].visible_since)

    def test_occlusion_breaks_a_clickthrough_disappearance_streak(self):
        self.initial_click()
        self.frame = self.make_frame()
        self.clickthrough = True
        self.tick()
        self.occluded = True
        self.tick()
        self.occluded = False
        self.tick()
        self.assertEqual(self.watcher.confirmed, 0)
        self.tick()
        self.assertEqual(self.watcher.confirmed, 1)

    def test_moving_window_cannot_confirm_absence_at_a_new_location(self):
        self.initial_click()
        self.frame = self.make_frame()
        self.window = WindowInfo(51, 72, "DesktopCatInput", (5, 0, 160, 100))
        self.windows = [self.window]
        self.tick(count=3)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.watcher.pending[0].lock.missing_frames, 0)

    def test_two_icons_can_disappear_without_countdown_text_independently(self):
        self.frame = self.make_frame((20, 20, 1), (90, 60, 1))
        self.initial_click()
        self.tick(.5)
        self.frame = self.make_frame((90, 60, 1))
        with patch.object(auto_chest.desktop, "point_hits_window", side_effect=lambda x, *_: x > 70):
            self.tick(.5, count=2)
        self.assertEqual(self.watcher.confirmed, 1)
        self.assertEqual(len(self.watcher.pending), 1)
        self.frame = self.make_frame()
        self.clickthrough = True
        self.tick(.5, count=2)
        self.assertEqual(self.watcher.confirmed, 2)
        self.assertEqual(self.watcher.pending, [])
        self.assertEqual(len(self.sent), 2)

    def test_unchanged_target_retries_after_four_seconds_then_pauses_at_three(self):
        self.initial_click()
        self.tick(3.9)
        self.assertEqual(len(self.sent), 1)
        self.tick(.11)
        self.assertEqual(len(self.sent), 2)
        self.tick(2.)
        self.tick(2.01)
        self.assertEqual(len(self.sent), 3)
        self.assertTrue(self.watcher.active.is_set())
        self.tick(4.01)
        self.assertFalse(self.watcher.active.is_set())
        self.tick(100., count=3)
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertTrue(any(kind == "error" and "3 次" in text for kind, text in self.drain()))

    def test_manual_continue_resets_failed_budget_without_immediate_failure(self):
        self.initial_click()
        self.tick(1., count=12)
        self.assertFalse(self.watcher.active.is_set())
        self.assertEqual(len(self.sent), 3)
        self.watcher.reset_pending_attempts()
        self.watcher.gate.reset_observations()
        self.watcher.active.set()
        self.tick()
        self.assertTrue(self.watcher.active.is_set())
        self.assertEqual(len(self.sent), 3)
        self.tick(4.01)
        self.assertEqual(len(self.sent), 4)
        self.assertEqual(self.watcher.pending[0].attempts, 1)

    def test_occlusion_is_neither_confirmation_nor_failed_attempt(self):
        self.initial_click()
        self.occluded = True
        self.frame = self.make_frame()
        self.tick(20., count=3)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(self.watcher.pending[0].attempts, 1)
        self.assertEqual(len(self.sent), 1)
        self.occluded = False
        self.frame = self.make_frame((20, 20, 1))
        self.tick()
        self.tick(3.)
        self.assertEqual(len(self.sent), 1)
        self.tick(1.01)
        self.assertEqual(len(self.sent), 2)

    def test_lock_and_hidden_window_do_not_confirm_disappearance(self):
        for unknown in ("lock", "hidden"):
            with self.subTest(unknown=unknown):
                self.setUp_case_unknown(unknown)

    def setUp_case_unknown(self, unknown):
        # This helper deliberately uses the same pending target across both
        # unknown states; neither may reset its attempt count or claim success.
        if not self.sent:
            self.initial_click()
        self.frame = self.make_frame()
        if unknown == "lock":
            self.desktop_available = False
        else:
            self.desktop_available = True
            self.windows = []
        self.tick(20., count=3)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(self.watcher.pending[0].attempts, 1)
        self.assertEqual(len(self.sent), 1)

    def test_variant_change_and_new_gate_key_do_not_reset_retry_budget(self):
        self.frame = self.make_frame((50, 30, 1))
        self.initial_click()
        self.frame = self.make_frame((49, 30, 2))  # Same center (57, 36).
        self.watcher.gate.states.clear()
        self.tick(2.)
        self.assertEqual(len(self.sent), 1)
        self.tick(2.01)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(len(self.watcher.pending), 1)
        self.assertEqual(self.watcher.pending[0].attempts, 2)

    def test_second_ready_chest_is_not_blocked_by_first_pending_result(self):
        self.frame = self.make_frame((20, 20, 1), (90, 60, 1))
        self.initial_click()
        self.tick(.5)
        self.assertEqual([(x, y) for _, x, y in self.sent], [(27, 26), (97, 66)])
        self.assertEqual(len(self.watcher.pending), 2)
        self.frame = self.make_frame((90, 60, 1))
        self.tick(.5, count=2)
        self.assertEqual(self.watcher.confirmed, 1)
        self.assertEqual(len(self.watcher.pending), 1)
        self.assertEqual(self.watcher.pending[0].attempts, 1)

    def test_user_activity_cannot_be_bypassed_by_retry(self):
        self.initial_click()
        self.idle = 0.
        self.tick(5., count=2)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.watcher.pending[0].attempts, 1)
        self.idle = 10.
        self.tick()
        self.assertEqual(len(self.sent), 2)

    def test_resume_start_resets_pending_attempts_and_passes_cancel_callback(self):
        self.initial_click()
        self.watcher.pending[0].attempts = 3
        self.watcher.pause()
        with patch.object(auto_chest.threading, "Thread") as thread:
            self.watcher.start(self.watcher.templates, .94, 2.)
        thread.return_value.start.assert_called_once()
        self.assertTrue(self.watcher.active.is_set())
        self.assertEqual(self.watcher.pending[0].attempts, 0)
        self.assertIsNone(self.watcher.pending[0].visible_since)
        cancel = self.click.call_args.kwargs["should_cancel"]
        self.watcher.pause()
        self.assertTrue(cancel())


if __name__ == "__main__":
    unittest.main(verbosity=2)
