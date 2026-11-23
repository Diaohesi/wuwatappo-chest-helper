"""Windows 11 screenshot regressions with real pixels and mocked OS actions."""
from contextlib import ExitStack, contextmanager
import hashlib
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

import auto_chest
from chest_assets import load_builtin_templates
from chest_detection import detect_chests
from chest_windows import WindowInfo
from fixture_assets import load_fixture


CAPSULES = ((140, 42, 171, 62), (131, 76, 162, 96))
GLYPHS = ((145, 44, 164, 59), (136, 78, 156, 93))
CENTERS = ((154, 51), (146, 85))


def erase_glyphs(image, upper=False, lower=False):
    """Remove ready artwork while retaining each button's outer capsule."""
    image = image.copy()
    draw = ImageDraw.Draw(image)
    for erase, (left, top, right, bottom) in zip((upper, lower), GLYPHS):
        if erase:
            draw.rectangle((left, top, right-1, bottom-1), fill=(246, 215, 170))
    return image


class Win11ChestDetectionTests(unittest.TestCase):
    def test_original_win11_pixels_detect_both_chests_once_at_user_threshold(self):
        templates = dict(load_builtin_templates())
        self.assertEqual(set(templates), {1, 2, 3, 4, 5})
        self.assertEqual(templates[3].size, (19, 15))
        self.assertEqual(templates[4].size, (20, 15))
        found = detect_chests(load_fixture("win11_chests.png"), .96)
        self.assertEqual([(variant, match.center) for variant, match in found],
                         [(3, CENTERS[0]), (4, CENTERS[1])])
        for _, match in found:
            self.assertGreater(match.score, .999999)

    def test_pet_and_desktop_background_do_not_change_ready_chest_detection(self):
        original = load_fixture("win11_chests.png")
        for colour in ("black", "white", "magenta", (30, 120, 180)):
            with self.subTest(background=colour):
                image = Image.new("RGB", original.size, colour)
                for box in CAPSULES:
                    image.paste(original.crop(box), box[:2])
                self.assertEqual([match.center for _, match in detect_chests(image, .96)], list(CENTERS))

    def test_either_win11_chest_can_be_ready_independently(self):
        original = load_fixture("win11_chests.png")
        for upper, lower, expected in ((True, False, [CENTERS[1]]), (False, True, [CENTERS[0]])):
            with self.subTest(upper_removed=upper):
                image = erase_glyphs(original, upper=upper, lower=lower)
                self.assertEqual([match.center for _, match in detect_chests(image, .96)], expected)

    def test_capsules_without_chest_glyphs_are_not_ready_buttons(self):
        image = erase_glyphs(load_fixture("win11_chests.png"), upper=True, lower=True)
        self.assertEqual(detect_chests(image, .96), [])

    def test_countdown_text_and_similar_coloured_blocks_do_not_match(self):
        original = load_fixture("win11_chests.png")
        countdown = erase_glyphs(original, upper=True, lower=True)
        draw = ImageDraw.Draw(countdown)
        draw.text((141, 46), "29:59", fill=(140, 100, 62))
        draw.text((132, 80), "17:42", fill=(140, 100, 62))
        blocks = Image.new("RGB", original.size, (30, 45, 60))
        draw = ImageDraw.Draw(blocks)
        for left, top, right, bottom in CAPSULES:
            draw.rounded_rectangle((left, top, right-1, bottom-1), radius=8, fill=(246, 215, 170))
            draw.rectangle((left+5, top+5, right-7, bottom-6), fill=(140, 100, 62))
        for name, image in (("countdown", countdown), ("similar colours", blocks)):
            with self.subTest(image=name):
                self.assertEqual(detect_chests(image, .96), [])


class Win11WatcherTests(unittest.TestCase):
    @contextmanager
    def session(self, mode):
        self.now = 100.
        self.frame = load_fixture("win11_chests.png")
        self.before_capture = None
        self.serial = 0
        self.sent = []
        self.input = WindowInfo(52, 72, "DesktopCatInput", (0, 0, *self.frame.size))
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
                stack.enter_context(patch.object(auto_chest.desktop, "capture_desktop", side_effect=AssertionError("Physical screen used for background mode")))
                for name in ("point_hits_window", "can_observe_disappearance", "user_idle_seconds", "safe_click"):
                    stack.enter_context(patch.object(auto_chest.desktop, name, side_effect=AssertionError(name)))
                stack.enter_context(patch.object(auto_chest.bg, "background_click", side_effect=self.record_click))
            else:
                stack.enter_context(patch.object(auto_chest.bg, "BackgroundCapture", side_effect=AssertionError("Unexpected MaaFw capture")))
                stack.enter_context(patch.object(auto_chest.desktop, "capture_desktop", side_effect=self.desktop_frame))
                stack.enter_context(patch.object(auto_chest.desktop, "point_hits_window", side_effect=lambda x, y, window: window == self.input))
                stack.enter_context(patch.object(auto_chest.desktop, "can_observe_disappearance", return_value=True))
                if mode == "visible":
                    stack.enter_context(patch.object(auto_chest.desktop, "user_idle_seconds", return_value=10.))
                    stack.enter_context(patch.object(auto_chest.desktop, "safe_click", side_effect=self.record_click))
                    stack.enter_context(patch.object(auto_chest.bg, "background_click", side_effect=AssertionError("Directed input used for visible mode")))
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
        if self.before_capture:
            self.before_capture(self.serial)
        image = self.frame.copy()
        # A harmless pixel change supplies fresh background-frame evidence.
        image.putpixel((0, 0), (self.serial % 255, 45, 60))
        return image

    def desktop_frame(self):
        return self.next_image(), (0, 0)

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

    def test_all_modes_claim_two_real_win11_chests_and_confirm_independently(self):
        for mode in ("visible", "borderless", "background"):
            with self.subTest(mode=mode), self.session(mode):
                self.tick()
                self.assertEqual(self.sent, [])
                self.assertEqual(self.serial, 1)
                self.tick()
                self.assertEqual(self.sent, [CENTERS[0]])
                self.assertEqual(self.serial, 3)  # Stable frame plus fresh verification.
                self.assertEqual(self.watcher.confirmed, 0)
                self.tick()
                self.assertEqual(self.sent, list(CENTERS))
                self.assertEqual(len(self.watcher.pending), 2)
                self.frame = erase_glyphs(self.frame, upper=True)
                self.tick()
                self.assertEqual(self.watcher.confirmed, 0)
                self.tick()
                self.assertEqual(self.watcher.confirmed, 1)
                self.assertEqual(len(self.watcher.pending), 1)
                self.frame = erase_glyphs(self.frame, lower=True)
                self.tick(count=2)
                self.assertEqual(self.watcher.confirmed, 2)
                self.assertEqual(self.watcher.pending, [])
                self.assertEqual(self.watcher.clicks, 2)

    def test_fresh_verification_does_not_substitute_the_other_ready_win11_chest(self):
        for mode in ("visible", "borderless", "background"):
            with self.subTest(mode=mode), self.session(mode):
                def remove_selected_at_verification(number):
                    if number == 3:
                        self.frame = erase_glyphs(self.frame, upper=True)
                self.before_capture = remove_selected_at_verification
                self.tick(count=2)
                self.assertEqual(self.serial, 3)
                self.assertEqual(self.sent, [])
                self.assertEqual(self.watcher.pending, [])

    def test_new_chest_claim_and_confirmation_continue_during_other_chest_backoff(self):
        for mode in ("visible", "borderless", "background"):
            with self.subTest(mode=mode), self.session(mode):
                self.frame = erase_glyphs(self.frame, lower=True)
                self.tick(count=2)
                claim = self.watcher.pending[0]
                self.tick(count=6, delta=2.1)
                self.assertEqual(self.sent, [CENTERS[0]]*3)
                self.assertEqual(self.watcher.pending, [claim])
                self.assertEqual(claim.attempts, 3)
                self.assertTrue(self.watcher.active.is_set())
                self.assertEqual(self.watcher.confirmed, 0)
                self.frame = load_fixture("win11_chests.png")
                self.tick(count=2)
                self.assertEqual(self.sent, [CENTERS[0]]*3 + [CENTERS[1]])
                self.frame = erase_glyphs(self.frame, lower=True)
                self.tick(count=2)
                self.assertEqual(self.watcher.confirmed, 1)
                self.tick(delta=5.)
                self.assertEqual(self.sent[-1], CENTERS[0])
                self.assertEqual(claim.attempts, 4)
                self.frame = erase_glyphs(self.frame, upper=True)
                self.tick(count=2)
                self.assertEqual(self.watcher.confirmed, 2)
                self.assertEqual(self.watcher.pending, [])


if __name__ == "__main__":
    unittest.main()
