from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from hwinfo_plotter.core import HWiNFOData, SensorColumn
from hwinfo_plotter.gui import HWiNFOPlotterApp


class GuiBehaviorTests(unittest.TestCase):
    def test_preview_request_scales_to_window_and_keeps_colors(self) -> None:
        base_time = datetime(2026, 4, 13, 12, 0, 0)
        data = HWiNFOData(
            source_path=Path("synthetic.csv"),
            encoding="utf-8",
            headers=["Date", "Time", "CPU"],
            columns=[SensorColumn(index=2, name="CPU", occurrence=1, display_name="[002] CPU")],
            timestamps=[base_time + timedelta(seconds=index) for index in range(3)],
            rows=[
                ["13/04/2026", "12:00:00", "1.0"],
                ["13/04/2026", "12:00:01", "2.0"],
                ["13/04/2026", "12:00:02", "3.0"],
            ],
        )

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.data = data
            app.selected_column_indices = {2}
            app.column_colors = {2: "#123456"}
            app.width_var.set("1600")
            app.height_var.set("900")
            app.dpi_var.set("144")
            app.title_var.set("预览测试")

            with patch.object(app.preview_scroll_canvas, "winfo_width", return_value=824), patch.object(
                app.preview_scroll_canvas,
                "winfo_height",
                return_value=624,
            ):
                preview_request = app.build_preview_request()

            self.assertEqual(preview_request.width_px, 800)
            self.assertEqual(preview_request.height_px, 450)
            self.assertEqual(preview_request.dpi, 72)
            self.assertEqual(preview_request.color_by_column, {2: "#123456"})
            self.assertEqual(preview_request.style.title, "预览测试")
        finally:
            app.on_close()


if __name__ == "__main__":
    unittest.main()
