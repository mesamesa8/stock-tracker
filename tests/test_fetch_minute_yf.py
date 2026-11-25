import unittest
from datetime import datetime

import pandas as pd

import fetch_minute_yf as fm


class TestFetchMinuteYf(unittest.TestCase):
    def test_parse_existing_dates(self):
        lines = ["minute_2026-11-24.csv.gz", "minute_2026-11-25.csv.gz", "", "other.txt"]
        self.assertEqual(fm.parse_existing_dates(lines), {"2026-11-24", "2026-11-25"})

    def test_dates_to_save_skips_existing_and_unfinished_today(self):
        available = ["2026-11-20", "2026-11-24", "2026-11-25"]
        existing = {"2026-11-20"}
        before_close = datetime(2026, 11, 25, 15, 0, tzinfo=fm.JST)
        after_close = datetime(2026, 11, 25, 16, 7, tzinfo=fm.JST)
        self.assertEqual(fm.dates_to_save(available, existing, before_close), ["2026-11-24"])
        self.assertEqual(fm.dates_to_save(available, existing, after_close), ["2026-11-24", "2026-11-25"])

    def test_extract_multiindex_converts_to_jst_rows(self):
        idx = pd.DatetimeIndex(["2026-11-25 00:00", "2026-11-25 00:01"], tz="UTC")
        cols = pd.MultiIndex.from_product([["7203.T", "6758.T"], ["Open", "High", "Low", "Close", "Volume"]])
        data = pd.DataFrame(
            [[1, 2, 0.5, 1.5, 100, None, None, None, None, None],
             [1.5, 2, 1, 1.8, 200, None, None, None, None, None]],
            index=idx, columns=cols,
        )
        frames, missing = fm.extract(data, ["7203.T", "6758.T"], {"7203.T": "72030", "6758.T": "67580"})
        self.assertEqual(missing, ["6758.T"])
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["datetime"].tolist(), ["2026-11-25 09:00", "2026-11-25 09:01"])
        self.assertEqual(frames[0]["code"].iloc[0], "72030")
        self.assertEqual(frames[0]["volume"].tolist(), [100, 200])

    def test_extract_handles_failed_download(self):
        frames, missing = fm.extract(None, ["7203.T"], {"7203.T": "72030"})
        self.assertEqual((frames, missing), ([], ["7203.T"]))


if __name__ == "__main__":
    unittest.main()
