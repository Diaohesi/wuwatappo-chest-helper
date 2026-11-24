import unittest
from dataclasses import replace
from unittest.mock import patch

from PIL import Image

import auto_chest as app
import chest_displays as screens
import chest_windows as desktop
from chest_assets import load_builtin_templates


class MultiDisplayTests(unittest.TestCase):
    def setUp(self):
        # 4K above-left, with a gap on the right and a shared edge on the left.
        self.layout = (screens.Monitor('4K', (-2000, -2160, 1840, 0), False, 144),
                       screens.Monitor('FHD', (0, 0, 1920, 1080), True, 96))
        self.art = load_builtin_templates()
        self.window = desktop.WindowInfo(1, 2, 'DesktopCatInput', (100, -50, 260, 50))
        self.frame = Image.new('RGB', (160, 100), (30, 45, 60))
        self.frame.paste(self.art[0][1], (30, 45))
        self.enum = self.enterContext(patch.object(screens, 'monitors', return_value=self.layout))
        self.enterContext(patch.object(desktop, 'virtual_rect', return_value=(-2000, -2160, 1920, 1080)))
        self.enterContext(patch.object(desktop, 'is_desktop_available', return_value=True))
        self.read = self.enterContext(patch.object(screens, 'grab_region', return_value=self.frame))
        self.enterContext(patch.object(desktop, 'find_pet_windows', side_effect=lambda: [self.window]))
        self.enterContext(patch.object(desktop, 'point_hits_window', return_value=True))

    def test_straddling_chest_on_mixed_dpi_screens_uses_one_physical_coordinate(self):
        self.assertEqual([(m['x'], m['y']) for m in app.scan_once(self.art, .94)], [(137, 1)])
        self.read.assert_called_once_with(self.window.rect)
        self.window = replace(self.window, rect=(-560, -230, -400, -130))
        self.assertEqual([(m['x'], m['y']) for m in app.scan_once(self.art, .94)], [(-523, -179)])

    def test_gap_invalidates_match_even_if_center_visible(self):
        self.window = replace(self.window, rect=(1805, -50, 1965, 50))
        self.read.return_value = self.frame.crop((0, 0, 115, 100))
        self.assertFalse(app.scan_once(self.art, .94))  # partially outside upper display
        self.assertTrue(screens.covered((1830,-2,1840,2), [m.rect for m in self.layout]))
        self.assertFalse(screens.covered((1830,-2,1841,2), [m.rect for m in self.layout]))

    def test_layout_change_during_capture_discards_same_sized_frame(self):
        self.enum.side_effect = [self.layout, (replace(self.layout[0], dpi=192), self.layout[1])]
        with self.assertRaises(desktop.CaptureUnavailable):
            desktop.capture_desktop([self.window])

    def test_driver_fallback_crops_using_negative_virtual_origin(self):
        self.read.side_effect = OSError('restricted display driver')
        self.layout = (screens.Monitor('left', (-80,0,0,100), False, 144),
                       screens.Monitor('main', (0,0,80,100), True, 96))
        self.enum.return_value = self.layout
        window = replace(self.window, rect=(-65, 5, -10, 60))
        with patch.object(desktop, 'virtual_rect', return_value=(-80,0,80,100)), \
                patch.object(desktop.ImageGrab, 'grab', return_value=self.frame):
            im, origin = desktop.capture_desktop([window])
        self.assertEqual(origin, (-65, 5))
        self.assertEqual(im.tobytes(), self.frame.crop((15,5,70,60)).tobytes())
