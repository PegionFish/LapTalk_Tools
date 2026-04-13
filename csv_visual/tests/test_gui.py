from __future__ import annotations

import base64
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hwinfo_plotter.core import HWiNFOData, SensorColumn
from hwinfo_plotter.gui import HWiNFOPlotterApp, PreloadSeriesResult


TEST_PREVIEW_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg=="
)


def build_synthetic_data() -> HWiNFOData:
    base_time = datetime(2026, 4, 13, 12, 0, 0)
    return HWiNFOData(
        source_path=Path("synthetic.csv"),
        encoding="utf-8",
        headers=["Date", "Time", "CPU", "GPU"],
        columns=[
            SensorColumn(index=2, name="CPU", occurrence=1, display_name="[002] CPU"),
            SensorColumn(index=3, name="GPU", occurrence=1, display_name="[003] GPU"),
        ],
        timestamps=[base_time + timedelta(seconds=index) for index in range(3)],
        rows=[
            ["13/04/2026", "12:00:00", "1.0", "2.0"],
            ["13/04/2026", "12:00:01", "2.0", "3.0"],
            ["13/04/2026", "12:00:02", "3.0", "4.0"],
        ],
    )


class GuiBehaviorTests(unittest.TestCase):
    def test_filter_list_has_usable_height_without_fullscreen(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.update()

            self.assertGreaterEqual(app.column_listbox.winfo_height(), 120)
            self.assertGreater(app.control_scroll_canvas.winfo_height(), 0)
        finally:
            app.on_close()

    def test_mousewheel_scrolls_control_panel_from_content_area(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            event = SimpleNamespace(widget=app.control_panel, delta=-120, num=None, state=0)

            with patch.object(app.control_scroll_canvas, "yview_scroll") as mock_scroll:
                result = app._on_mousewheel(event)

            mock_scroll.assert_called_once_with(1, "units")
            self.assertEqual(result, "break")
        finally:
            app.on_close()

    def test_mousewheel_scrolls_parameter_list_without_hovering_scrollbar(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            event = SimpleNamespace(widget=app.column_listbox, delta=-120, num=None, state=0)

            with patch.object(app.column_listbox, "yview_scroll") as mock_scroll:
                result = app._on_mousewheel(event)

            mock_scroll.assert_called_once_with(1, "units")
            self.assertEqual(result, "break")
        finally:
            app.on_close()

    def test_mousewheel_does_not_scroll_preview_area(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            event = SimpleNamespace(widget=app.preview_host, delta=-120, num=None, state=0)

            result = app._on_mousewheel(event)

            self.assertIsNone(result)
        finally:
            app.on_close()

    def test_preview_request_keeps_export_size_and_colors(self) -> None:
        data = build_synthetic_data()

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.data = data
            app.selected_column_indices = {2}
            app.column_colors = {2: "#123456"}
            app.configure_trim_controls()
            app.width_var.set("1600")
            app.height_var.set("900")
            app.dpi_var.set("144")
            app.title_var.set("预览测试")
            app.time_density_var.set(10)
            app.fixed_time_interval_var.set("2")
            app.fixed_time_interval_unit_var.set("分钟")
            app.trim_start_var.set(1)
            app.trim_end_var.set(2)

            preview_request = app.build_preview_request()

            self.assertEqual(preview_request.width_px, 1600)
            self.assertEqual(preview_request.height_px, 900)
            self.assertEqual(preview_request.dpi, 144)
            self.assertEqual(preview_request.color_by_column, {2: "#123456"})
            self.assertEqual(preview_request.style.title, "预览测试")
            self.assertEqual(preview_request.style.time_tick_density, 10)
            self.assertEqual(preview_request.style.fixed_time_interval_seconds, 120)
            self.assertEqual(preview_request.visible_range_seconds, (1.0, 2.0))
        finally:
            app.on_close()

    def test_show_preview_image_replaces_placeholder_with_png(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()

            app.show_preview_image(TEST_PREVIEW_PNG_BYTES)
            app.update_idletasks()

            self.assertIsNotNone(app.preview_image)
            self.assertIsNotNone(app.preview_label)
            self.assertTrue(app.preview_label.winfo_exists())
            image_value = app.preview_label.cget("image")
            if isinstance(image_value, tuple):
                image_value = image_value[0]
            self.assertEqual(image_value, str(app.preview_image))
            self.assertFalse(app.preview_placeholder.winfo_ismapped())
        finally:
            app.on_close()

    def test_load_current_file_clears_stale_filter_and_lists_columns_immediately(self) -> None:
        data = build_synthetic_data()

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.file_var.set("synthetic.csv")
            app.filter_var.set("missing")

            with patch.object(app, "start_background_preload") as mock_preload, patch(
                "hwinfo_plotter.gui.load_hwinfo_csv",
                return_value=data,
            ) as mock_load:
                app.load_current_file()

            mock_load.assert_called_once_with("synthetic.csv", preload_numeric=False)
            mock_preload.assert_called_once_with(data)
            self.assertEqual(app.filter_var.get(), "")
            self.assertEqual(app.visible_column_indices, [2, 3])
            self.assertEqual(app.column_listbox.get(0, "end"), ("[002] CPU", "[003] GPU"))
        finally:
            app.on_close()

    def test_process_preview_results_reports_preload_completion_without_selection(self) -> None:
        data = build_synthetic_data()

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.data = data
            app.active_preload_request_id = 3
            app.preload_results.put(PreloadSeriesResult(request_id=3, data=data))

            with patch.object(app, "after"):
                app.process_preview_results()

            self.assertEqual(app.status_var.get(), "全部数值序列已在后台预载入内存，后续预览和导出会更快。")
        finally:
            app.on_close()


if __name__ == "__main__":
    unittest.main()
