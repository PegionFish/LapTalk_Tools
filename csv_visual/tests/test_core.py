from __future__ import annotations

import unittest
from pathlib import Path

from hwinfo_plotter.core import build_figure, load_hwinfo_csv, save_figure


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CSV = ROOT / "R23-15.CSV"


class CoreSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = load_hwinfo_csv(SAMPLE_CSV)

    def test_loads_sample_csv(self) -> None:
        self.assertGreater(len(self.data.timestamps), 0)
        self.assertGreater(len(self.data.columns), 100)
        self.assertEqual(self.data.source_path.name, "R23-15.CSV")

    def test_exports_png(self) -> None:
        column_index = next(
            column.index
            for column in self.data.columns
            if len(self.data.extract_series(column.index)[1]) > 10
        )

        figure = build_figure(
            self.data,
            [column_index],
            title="Smoke Test",
            width_px=1280,
            height_px=720,
            dpi=120,
        )

        output_path = ROOT / "_smoke_chart.png"

        try:
            save_figure(figure, output_path)
            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)
        finally:
            if output_path.exists():
                output_path.unlink()


if __name__ == "__main__":
    unittest.main()
