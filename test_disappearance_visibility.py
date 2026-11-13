"""Mock-only checks for transparent disappearance versus visual occlusion."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import chest_windows as windows


class DisappearanceVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.window = windows.WindowInfo(51, 72, "DesktopCatInput", (0, 0, 160, 100))
        self.region = (20, 20, 36, 32)
        self.order = [10, 51, 90]
        self.owner = 51
        self.roots = {}
        self.error = 0
        self.pids = {10: 88, 51: 72, 90: 99}
        self.bounds = {10: (500, 500, 600, 600)}
        self.api = SimpleNamespace(
            WindowFromPoint=Mock(side_effect=lambda _point: self.owner),
            GetAncestor=Mock(side_effect=lambda hwnd, _flag: self.roots.get(hwnd, hwnd)),
            GetTopWindow=Mock(side_effect=lambda _parent: self.order[0] if self.order else 0),
            GetWindow=Mock(side_effect=self.next_window),
            IsWindow=Mock(side_effect=lambda hwnd: hwnd in self.order),
            IsWindowVisible=Mock(return_value=True),
            IsIconic=Mock(return_value=False),
        )
        self.stack.enter_context(patch.object(windows, "_require_windows"))
        self.stack.enter_context(patch.object(windows, "_user32", self.api, create=True))
        self.current = self.stack.enter_context(patch.object(windows, "window_is_current", return_value=True))
        self.unlocked = self.stack.enter_context(patch.object(windows, "is_desktop_available", return_value=True))
        self.stack.enter_context(patch.object(windows, "_pid", side_effect=lambda hwnd: self.pids.get(hwnd, 0)))
        self.geometry = self.stack.enter_context(patch.object(windows, "_window_rect", side_effect=lambda hwnd: self.bounds.get(hwnd)))
        self.stack.enter_context(patch.object(windows.ctypes, "set_last_error", side_effect=self.set_error, create=True))
        self.stack.enter_context(patch.object(windows.ctypes, "get_last_error", side_effect=lambda: self.error, create=True))

    def next_window(self, hwnd, flag):
        self.assertEqual(flag, 2)
        index = self.order.index(hwnd)+1
        return self.order[index] if index < len(self.order) else 0

    def set_error(self, error):
        self.error = error

    def observe(self):
        return windows.can_observe_disappearance(self.region, self.window)

    def test_same_window_or_child_can_show_disappearance(self):
        self.assertTrue(self.observe())
        self.owner = 900
        self.roots[900] = 51
        self.assertTrue(self.observe())
        self.api.GetAncestor.assert_called_with(900, 2)

    def test_transparent_region_can_expose_lower_window_or_child(self):
        self.owner = 90
        self.assertTrue(self.observe())
        self.owner = 901
        self.roots[901] = 90
        self.assertTrue(self.observe())

    def test_actual_owner_above_target_is_unknown_even_for_same_pet_pid(self):
        self.owner = 10
        for pid in (88, 72):
            with self.subTest(pid=pid):
                self.pids[10] = pid
                self.assertFalse(self.observe())

    def test_clickthrough_foreign_overlay_blocks_absence_evidence(self):
        self.owner = 90  # Its input passes through the visible overlay.
        self.bounds[10] = (0, 0, 160, 100)
        self.assertFalse(self.observe())

    def test_partial_occlusion_blocks_even_when_center_still_hits_pet(self):
        self.bounds[10] = (35, 20, 60, 50)
        self.assertFalse(self.observe())
        self.bounds[10] = (36, 20, 60, 50)  # Touching the edge is not overlap.
        self.assertTrue(self.observe())

    def test_same_pet_fullscreen_layer_is_not_a_rectangle_only_blocker(self):
        self.pids[10] = 72
        self.bounds[10] = (0, 0, 160, 100)
        self.owner = 90
        self.assertTrue(self.observe())

    def test_hidden_or_minimized_overlay_does_not_block(self):
        self.bounds[10] = (0, 0, 160, 100)
        self.api.IsWindowVisible.side_effect = lambda hwnd: hwnd != 10
        self.assertTrue(self.observe())
        self.api.IsWindowVisible.side_effect = None
        self.api.IsIconic.side_effect = lambda hwnd: hwnd == 10
        self.assertTrue(self.observe())

    def test_stale_moved_or_hidden_target_is_unknown(self):
        self.current.return_value = False
        self.assertFalse(self.observe())
        self.api.WindowFromPoint.assert_not_called()

    def test_window_change_during_query_is_unknown(self):
        self.current.side_effect = [True, False]
        self.assertFalse(self.observe())

    def test_locked_or_newly_locked_desktop_is_unknown(self):
        self.unlocked.return_value = False
        self.assertFalse(self.observe())
        self.api.WindowFromPoint.assert_not_called()
        self.unlocked.return_value = True
        self.unlocked.side_effect = [True, False]
        self.assertFalse(self.observe())

    def test_region_must_be_positive_and_wholly_inside_target(self):
        for rect in ((20, 20, 20, 30), (20, 20, 30, 20), (-1, 20, 30, 30), (20, 20, 161, 30)):
            with self.subTest(rect=rect):
                self.assertFalse(windows.can_observe_disappearance(rect, self.window))
        self.api.WindowFromPoint.assert_not_called()

    def test_missing_target_or_owner_in_chain_is_unknown(self):
        self.order = [10, 90]
        self.assertFalse(self.observe())
        self.order = [10, 51]
        self.owner = 90
        self.assertFalse(self.observe())

    def test_missing_point_owner_or_root_is_unknown(self):
        self.owner = 0
        self.assertFalse(self.observe())
        self.owner = 900
        self.roots[900] = 0
        self.assertFalse(self.observe())

    def test_cycle_cannot_hang_or_confirm_disappearance(self):
        self.api.GetWindow.side_effect = lambda hwnd, flag: {10: 51, 51: 90, 90: 51}[hwnd]
        self.assertFalse(self.observe())
        self.assertEqual(self.api.GetWindow.call_count, 3)

    def test_overlong_chain_is_unknown(self):
        self.order = list(range(1, 4098))
        self.assertFalse(self.observe())
        self.assertEqual(self.api.GetWindow.call_count, 4096)

    def test_failed_z_order_query_is_unknown(self):
        self.api.GetTopWindow.return_value = 0
        self.api.GetTopWindow.side_effect = None
        self.assertFalse(self.observe())
        self.api.GetTopWindow.side_effect = lambda _: 10

        def failed_next(_hwnd, _flag):
            self.error = 5
            return 0

        self.api.GetWindow.side_effect = failed_next
        self.assertFalse(self.observe())

    def test_destroyed_chain_window_is_unknown(self):
        self.api.IsWindow.side_effect = lambda hwnd: hwnd != 10
        self.assertFalse(self.observe())

    def test_failed_foreign_pid_or_geometry_is_unknown(self):
        self.pids[10] = 0
        self.assertFalse(self.observe())
        self.pids[10] = 88
        self.bounds.clear()
        self.assertFalse(self.observe())

    def test_api_error_is_unknown(self):
        self.api.WindowFromPoint.side_effect = OSError("query unavailable")
        self.assertFalse(self.observe())


if __name__ == "__main__":
    unittest.main()
