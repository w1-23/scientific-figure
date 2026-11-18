"""Behavior checks using generated raster fixtures, without an SVG dependency."""
from pathlib import Path
import math
import re
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from scientific_figure.vectorize import trace_region


def painted(node, x, y):
    """Evaluate SVG evenodd fill at a point, independently of the tracer."""
    crossings = 0
    for subpath in node["geometry"]["d"].split("Z"):
        values = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", subpath)]
        points = list(zip(values[::2], values[1::2]))
        assert all(math.isfinite(v) for v in values)
        for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
            if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
                crossings += 1
    return crossings % 2 == 1


class VectorizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fixture.png"
        self.image = Image.new("RGBA", (80, 70), "white")
        self.draw = ImageDraw.Draw(self.image)

    def trace(self, region=None, **kwargs):
        self.image.save(self.path)
        return trace_region(self.path, region or [0, 0, 80, 70],
                            object_id="sample", **kwargs)

    def test_hole_and_region_local_coordinates(self):
        self.draw.rectangle((20, 20, 60, 55), fill="red")
        self.draw.rectangle((30, 30, 50, 45), fill="white")
        nodes, summary = self.trace([10, 10, 60, 50], palette=["#ffffff", "#ff0000"])
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertTrue(painted(node, 15, 15))
        self.assertFalse(painted(node, 30, 25))
        self.assertFalse(painted(node, 5, 5))
        self.assertEqual(node["geometry"]["d"].count("M"), 2)
        self.assertEqual(node["style"], {"fill": "#ff0000", "fill_rule": "evenodd"})
        self.assertEqual((node["id"], node["kind"], node["parent"]),
                         ("sample_p0001", "path", "sample"))
        self.assertEqual(summary["region"], [10, 10, 60, 50])
        self.assertEqual(summary["mask_pixel_count"], 41 * 36 - 21 * 16)
        self.assertGreaterEqual(summary["vertices"], 8)

    def test_multiple_objects_and_budget(self):
        self.draw.rectangle((40, 10, 55, 25), fill="red")
        self.draw.rectangle((10, 10, 25, 25), fill="red")
        args = {"palette": ["#ffffff", "#ff0000"]}
        first = self.trace(**args)
        self.assertEqual(first, self.trace(**args))
        self.assertEqual(first[1]["path_count"], 2)
        self.assertTrue(painted(first[0][0], 15, 15))
        self.assertTrue(painted(first[0][1], 45, 15))
        with self.assertRaisesRegex(ValueError, "budget"):
            self.trace(max_paths=1, **args)

    def test_quantization_and_determinism(self):
        self.draw.ellipse((10, 10, 35, 35), fill="blue")
        self.draw.rectangle((45, 40, 65, 60), fill="red")
        first = self.trace()
        self.assertEqual(first, self.trace())
        self.assertEqual(first[1]["backend"], "opencv-contours")
        self.assertEqual(first[1]["path_count"], 2)
        self.assertTrue(any(painted(n, 20, 20) for n in first[0]))
        self.assertFalse(any(painted(n, 4, 4) for n in first[0]))

    def test_hsv_alpha_and_fill(self):
        self.draw.rectangle((10, 10, 50, 50), fill=(255, 0, 0, 128))
        self.draw.rectangle((20, 20, 40, 40), fill=(255, 0, 0, 0))
        args = {"hsv": [[0, 200, 200], [10, 255, 255]]}
        nodes, summary = self.trace(**args)
        self.assertEqual(nodes[0]["style"]["fill"], "#ff0000")
        self.assertFalse(painted(nodes[0], 30, 30))
        self.assertTrue(painted(nodes[0], 15, 15))
        self.assertEqual(summary["mask_pixel_count"], 41 * 41 - 21 * 21)
        nodes, _ = self.trace(palette=["#ffffff", "#00ff00"], **args)
        self.assertEqual(nodes[0]["style"]["fill"], "#00ff00")

    def test_nearest_palette_and_transparent_pixels(self):
        self.image.paste((250, 250, 250, 255), (0, 0, 80, 70))
        self.draw.rectangle((10, 10, 30, 30), fill=(245, 5, 5, 255))
        self.draw.rectangle((40, 40, 60, 60), fill=(245, 5, 5, 0))
        nodes, summary = self.trace(palette=["#ffffff", "#ff0000"])
        self.assertEqual(len(nodes), 1)
        self.assertTrue(painted(nodes[0], 20, 20))
        self.assertFalse(painted(nodes[0], 50, 50))
        self.assertEqual(summary["mask_pixel_count"], 21 * 21)

    def test_invalid_regions_parameters_and_empty_result(self):
        self.image.save(self.path)
        for region in ([-1, 0, 20, 20], [0, 0, 0, 20], [70, 0, 20, 20],
                       [0, 65, 20, 20], [0, 0, 1.5, 20], [0, 0, -2, 4]):
            with self.subTest(region=region), self.assertRaises(ValueError):
                trace_region(self.path, region, object_id="sample")
        for kwargs in ({"simplify": float("nan")}, {"min_area": float("inf")},
                       {"max_paths": 0}, {"hsv": [[0, 0, 0], [180, 255, 255]]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.trace(**kwargs)
        with self.assertRaisesRegex(ValueError, "no contours"):
            self.trace(palette=["#ffffff", "#ff0000"])


if __name__ == "__main__":
    unittest.main()
