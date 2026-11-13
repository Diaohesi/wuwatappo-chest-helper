"""Native PNG raster holdouts independent of production template generation.

The direct pipeline draws each original transparent layer at final physical
resolution using bilinear interpolation. Other pipelines compose a complete
scene at 3x or 5x CSS resolution and resize that scene as a whole. Neither
uses the production 4x composite, interior renderer or screenshot templates.
These are independent synthetic raster checks, not real WebView/GPU captures.
"""
from dataclasses import dataclass
import math
import unittest

from PIL import Image

from chest_artwork import load_ready_artwork
from chest_detection import detect_chests


SCENE_SIZE = (320, 220)
ORIGINS = ((230.25, 44.75), (207.25, 112.75))
CASES = (("direct", .5, 1.), ("direct", .75, 2.),
         ("direct", 1.25, 1.), ("direct", 2., 2.),
         ("3", .75, 1.), ("3", 1.25, 2.),
         ("5", .5, 2.), ("5", 2., 1.))


@dataclass
class Holdout:
    image: Image.Image
    centers: list[tuple[float, float]]


def render_holdout(pipeline, pet_scale, dpi):
    artwork = load_ready_artwork()
    effective = pet_scale * dpi
    raster_scale = effective if pipeline == "direct" else float(pipeline)
    frame = Image.new("RGBA", tuple(round(v*raster_scale) for v in SCENE_SIZE),
                      (24, 45, 68, 255))
    interpolation = (Image.Resampling.BILINEAR if pipeline == "direct"
                     else Image.Resampling.BICUBIC)
    for left, top in ORIGINS:
        button = artwork["bubble"].resize(
            (round(62*raster_scale), round(44*raster_scale)), interpolation)
        for key in ("base", "lid"):
            layer = artwork[key].resize(
                (round(28*raster_scale), round(21*raster_scale)), interpolation)
            button.alpha_composite(layer, (round(14*raster_scale), round(11*raster_scale)))
        frame.alpha_composite(button, (round(left*raster_scale), round(top*raster_scale)))
    image = frame.convert("RGB")
    if pipeline != "direct":
        image = image.resize(tuple(round(v*effective) for v in SCENE_SIZE),
                             Image.Resampling.BILINEAR if pipeline == "3"
                             else Image.Resampling.BICUBIC)
    sx, sy = image.width/frame.width, image.height/frame.height
    centers = [((round(x*raster_scale)+28*raster_scale)*sx,
                (round(y*raster_scale)+21.5*raster_scale)*sy)
               for x, y in ORIGINS]
    return Holdout(image, centers)


class NativeRasterHoldoutTests(unittest.TestCase):
    def test_direct_and_three_five_times_scene_rasters_at_saved_threshold(self):
        for pipeline, scale, dpi in CASES:
            with self.subTest(pipeline=pipeline, pet_scale=scale, dpi=dpi):
                scene = render_holdout(pipeline, scale, dpi)
                found = detect_chests(scene.image, .96)
                self.assertEqual(len(found), 2,
                                 [(variant, m.center, m.score) for variant, m in found])
                remaining = list(scene.centers)
                for variant, match in found:
                    expected = min(remaining, key=lambda p: math.dist(p, match.center))
                    self.assertGreaterEqual(match.score, .96)
                    self.assertLessEqual(abs(match.center[0]-expected[0]), 2.,
                                         (variant, match.center, expected))
                    self.assertLessEqual(abs(match.center[1]-expected[1]), 2.,
                                         (variant, match.center, expected))
                    remaining.remove(expected)
                self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()
