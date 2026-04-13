from __future__ import annotations

import base64
import unittest

from hwinfo_plotter.win32_image import get_png_dimensions, resize_png_bytes


TEST_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg=="
)


class Win32ImageTests(unittest.TestCase):
    def test_get_png_dimensions_reads_png_header(self) -> None:
        self.assertEqual(get_png_dimensions(TEST_PNG_BYTES), (1, 1))

    def test_resize_png_bytes_updates_png_dimensions(self) -> None:
        resized_png_bytes = resize_png_bytes(TEST_PNG_BYTES, 32, 16)

        self.assertEqual(get_png_dimensions(resized_png_bytes), (32, 16))


if __name__ == "__main__":
    unittest.main()
