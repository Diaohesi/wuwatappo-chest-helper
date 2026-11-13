"""Synthetic native-artwork scale scenes, with no live pet or desktop input."""
from contextlib import ExitStack, contextmanager
import hashlib
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

import auto_chest
from chest_artwork import render_ready_interior
from chest_assets import load_builtin_templates
from chest_windows import WindowInfo


class ScaledWatcherTests(unittest.TestCase):
    def scene(self, scale, positions=((30, 25), (150, 105))):
        image = Image.new("RGB", (260, 200), (30, 45, 60))
        if scale is not None:
            artwork = render_ready_interior(round(32*scale), round(25*scale))
            for position in positions:
                image.paste(artwork, position)
        return image

    @contextmanager
    def session(self, mode, scale=.5, positions=((30, 25), (150, 105))):
        self.now, self.serial = 100., 0
        self.frame = self.scene(scale, positions)
        self.sent = []
        self.input = WindowInfo(52, 72, "DesktopCatInput", (0, 0, 260, 200))
        self.render = WindowInfo(51, 72, "Wuwa Tappo", self.input.rect)
        self.watcher = auto_chest.Watcher(queue.Queue())
        self.watcher.mode = mode
        self.watcher.threshold = .96
        self.watcher.templates = load_builtin_templates()
        self.watcher.active.set()
        self.manager = MagicMock()
        self.manager.grab.side_effect = self.background_frame
        with ExitStack() as stack:
            stack.enter_context(patch.object(auto_chest.time, "monotonic", side_effect=lambda: self.now))
            stack.enter_context(patch.object(auto_chest.desktop, "is_desktop_available", return_value=True))
            stack.enter_context(patch.object(auto_chest.desktop, "find_pet_windows", return_value=[self.input, self.render]))
            stack.enter_context(patch.object(auto_chest.desktop, "input_privilege_problem", return_value=None))
            stack.enter_context(patch.object(auto_chest.desktop, "window_is_current", return_value=True))
            stack.enter_context(patch.object(auto_chest.bg, "find_background_pairs", return_value=[(self.render, self.input)]))
            stack.enter_context(patch.object(auto_chest.bg, "client_origin", return_value=(0, 0)))
            if mode == "background":
                stack.enter_context(patch.object(auto_chest.bg, "BackgroundCapture", return_value=self.manager))
                stack.enter_context(patch.object(auto_chest.desktop, "capture_desktop", side_effect=AssertionError("Visible capture in background mode")))
                for name in ("point_hits_window", "can_observe_disappearance", "user_idle_seconds", "safe_click"):
                    stack.enter_context(patch.object(auto_chest.desktop, name, side_effect=AssertionError(name)))
                stack.enter_context(patch.object(auto_chest.bg, "background_click", side_effect=self.record_click))
            else:
                stack.enter_context(patch.object(auto_chest.bg, "BackgroundCapture", side_effect=AssertionError("Unexpected native capture")))
                stack.enter_context(patch.object(auto_chest.desktop, "capture_desktop", side_effect=lambda: (self.next_image(), (0, 0))))
                stack.enter_context(patch.object(auto_chest.desktop, "point_hits_window", side_effect=lambda x, y, window: window == self.input))
                stack.enter_context(patch.object(auto_chest.desktop, "can_observe_disappearance", return_value=True))
                if mode == "visible":
                    stack.enter_context(patch.object(auto_chest.desktop, "user_idle_seconds", return_value=10.))
                    stack.enter_context(patch.object(auto_chest.desktop, "safe_click", side_effect=self.record_click))
                    stack.enter_context(patch.object(auto_chest.bg, "background_click", side_effect=AssertionError("Directed input in visible mode")))
                else:
                    stack.enter_context(patch.object(auto_chest.desktop, "user_idle_seconds", side_effect=AssertionError("Idle check in borderless mode")))
                    stack.enter_context(patch.object(auto_chest.desktop, "safe_click", side_effect=AssertionError("Physical input in borderless mode")))
                    stack.enter_context(patch.object(auto_chest.bg, "background_click", side_effect=self.record_click))
            try:
                yield
            finally:
                self.watcher.close_background_capture()

    def next_image(self):
        self.serial += 1
        image = self.frame.copy()
        image.putpixel((0, 0), (self.serial % 255, 45, 60))
        return image

    def background_frame(self, render, input_window, should_cancel=None):
        self.assertFalse(should_cancel())
        image = self.next_image()
        return SimpleNamespace(image=image, render=render, input=input_window, origin=(0, 0),
                               digest=hashlib.sha256(image.tobytes()).hexdigest(), changed=self.serial > 1)

    def record_click(self, x, y, window, should_cancel=None, before_press=None):
        self.assertEqual(window, self.input)
        self.assertFalse(should_cancel())
        if before_press is not None:
            self.assertTrue(before_press())
        self.sent.append((x, y))
        return True

    def tick(self, count=1, delta=.5):
        for _ in range(count):
            self.now += delta
            self.watcher.tick()

    def test_three_modes_claim_and_confirm_two_synthetic_chests_at_multiple_scales(self):
        for mode in ("visible", "borderless", "background"):
            for scale in (.5, 1., 2.):
                with self.subTest(mode=mode, scale=scale), self.session(mode, scale):
                    width, height = round(32*scale), round(25*scale)
                    centres = [(30+width//2, 25+height//2), (150+width//2, 105+height//2)]
                    self.tick()
                    self.assertEqual(self.sent, [])
                    self.assertEqual(self.serial, 1)
                    self.tick()
                    self.assertEqual(self.sent, centres[:1])
                    self.assertEqual(self.serial, 3)  # The second frame is freshly verified.
                    self.tick()
                    self.assertEqual(self.sent, centres)
                    self.frame = self.scene(scale, ((150, 105),))
                    self.tick()
                    self.assertEqual(self.watcher.confirmed, 0)
                    self.tick()
                    self.assertEqual(self.watcher.confirmed, 1)
                    self.frame = self.scene(None)
                    self.tick(count=2)
                    self.assertEqual(self.watcher.confirmed, 2)
                    self.assertEqual(self.watcher.pending, [])
                    self.assertEqual(len(self.sent), 2)

    def test_growing_chest_at_same_centre_updates_existing_box_without_false_confirmation(self):
        for mode in ("visible", "borderless", "background"):
            with self.subTest(mode=mode), self.session(mode, .5, ((60, 50),)):
                self.tick(count=2)
                claim = self.watcher.pending[0]
                self.assertEqual(self.sent, [(68, 56)])
                self.frame = self.scene(2., ((36, 31),))  # Same centre, much larger than old patch.
                self.tick(count=2)
                self.assertEqual(self.watcher.pending, [claim])
                self.assertEqual(claim.lock.box, (36, 31, 64, 50))
                self.assertEqual(claim.attempts, 1)
                self.assertEqual(self.watcher.confirmed, 0)
                self.assertTrue(self.watcher.active.is_set())
                self.frame = self.scene(None)
                self.tick(count=2)
                self.assertEqual(self.watcher.confirmed, 1)
                self.assertEqual(len(self.sent), 1)

    def test_unmapped_resized_chest_pauses_and_keeps_exhausted_claim_budget(self):
        for mode in ("visible", "borderless", "background"):
            with self.subTest(mode=mode), self.session(mode, .5, ((30, 25),)):
                self.tick(count=2)
                self.tick(count=4, delta=2.1)
                claim = self.watcher.pending[0]
                self.assertEqual(claim.attempts, 3)
                self.assertEqual(len(self.sent), 3)
                self.frame = self.scene(2., ((150, 105),))
                self.tick()
                self.assertFalse(self.watcher.active.is_set())
                self.assertEqual(self.watcher.layout_changed_windows, {(52, 72)})
                self.assertEqual(self.watcher.pending, [claim])
                self.assertEqual(claim.attempts, 3)
                self.assertIsNone(claim.visible_since)
                self.assertEqual(claim.lock.missing_frames, 0)
                self.assertEqual(self.watcher.confirmed, 0)
                self.tick(count=5, delta=10.)
                self.assertEqual(len(self.sent), 3)
                self.assertTrue(any(kind == "error" and "布局" in text for kind, text in list(self.watcher.events.queue)))

    def test_explicit_continue_acknowledges_only_changed_window_without_claiming_success(self):
        for mode in ("visible", "borderless", "background"):
            with self.subTest(mode=mode), self.session(mode, .5, ((30, 25),)):
                self.tick(count=2)
                self.frame = self.scene(2., ((150, 105),))
                self.tick()
                self.assertFalse(self.watcher.active.is_set())
                self.watcher.ledger.clicked((99, 100), (10, 10, 16, 12), self.watcher.templates[0][1])
                unrelated_lock = self.watcher.ledger.locks[-1]
                unrelated = auto_chest.PendingClaim(unrelated_lock, 2, self.now, None, (0, 0, 260, 200))
                self.watcher.pending.append(unrelated)
                with patch.object(auto_chest.threading, "Thread"):
                    self.watcher.start(self.watcher.templates, .96, 2., mode=mode)
                self.assertEqual(self.watcher.layout_changed_windows, set())
                self.assertEqual(self.watcher.pending, [unrelated])
                self.assertEqual(self.watcher.ledger.locks, [unrelated_lock])
                self.assertEqual(self.watcher.confirmed, 0)
                self.assertEqual(self.watcher.clicks, 1)
                self.tick(count=2)
                self.assertTrue(self.watcher.active.is_set())
                self.assertEqual(self.sent, [(38, 31), (182, 130)])
                self.assertEqual(self.watcher.confirmed, 0)
                self.assertTrue(any(kind == "log" and "未确认的领取不计成功" in text
                                    for kind, text in list(self.watcher.events.queue)))

    def test_small_displaced_chest_inside_old_large_box_does_not_steal_its_claim(self):
        for mode in ("visible", "borderless", "background"):
            with self.subTest(mode=mode), self.session(mode, 2., ((30, 25),)):
                self.tick(count=2)
                claim = self.watcher.pending[0]
                self.frame = self.scene(.5, ((70, 58),))
                self.tick()
                self.assertFalse(self.watcher.active.is_set())
                self.assertEqual(self.watcher.pending, [claim])
                self.assertEqual(claim.attempts, 1)
                self.assertEqual(self.watcher.confirmed, 0)
                self.assertEqual(len(self.sent), 1)

    def test_pending_confirmation_reuses_one_whole_window_detection(self):
        for mode in ("visible", "borderless", "background"):
            with self.subTest(mode=mode), self.session(mode, .5, ((30, 25),)):
                self.tick(count=2)
                with patch.object(auto_chest, "detect_templates", wraps=auto_chest.detect_templates) as detect:
                    self.tick()
                detect.assert_called_once()
                self.assertEqual(detect.call_args.args[0].size, (260, 200))
                self.assertEqual(self.watcher.confirmed, 0)


if __name__ == "__main__":
    unittest.main()
