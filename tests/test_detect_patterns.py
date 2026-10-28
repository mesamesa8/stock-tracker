import unittest

import numpy as np
import pandas as pd

import detect_patterns as dp
from tests.test_chart_patterns import _cup_closes, _cup_ohlv


def _stock(closes):
    dates = pd.bdate_range("2025-01-06", periods=len(closes)).strftime("%Y-%m-%d")
    h, l, c, v = _cup_ohlv(closes)
    g = pd.DataFrame({"date": dates, "high": h, "low": l, "close": c, "volume": v})
    return dp.Stock(g)


NAMES = pd.DataFrame({"company_name": ["テスト"], "sector33_name": ["機械"]}, index=["11110"])


class DetectOnTest(unittest.TestCase):
    def test_past_date_ignores_later_prices(self):
        cup = _cup_closes()
        date = _stock(cup).dates[-1]
        # 判定日の後に大きく動いても、判定日時点の結果は変わらない
        later = np.concatenate([cup, np.linspace(97, 130, 30)])
        only_past = dp.detect_on({"11110": _stock(cup)}, date, NAMES)
        with_future = dp.detect_on({"11110": _stock(later)}, date, NAMES)
        self.assertEqual(len(only_past), 1)
        self.assertEqual(only_past, with_future)
        self.assertEqual(only_past[0]["chart"]["end_date"], date)

    def test_skips_stock_with_split_like_jump(self):
        cup = _cup_closes()
        cup[50:] *= 0.5  # 分割未調整のような急落
        s = _stock(cup)
        self.assertEqual(dp.detect_on({"11110": s}, s.dates[-1], NAMES), [])


class FollowupTest(unittest.TestCase):
    def setUp(self):
        self.cup = _cup_closes()
        s = _stock(self.cup)
        self.date = s.dates[-1]
        self.item = dp.detect_on({"11110": s}, self.date, NAMES)[0]

    def test_none_on_latest_date(self):
        self.assertIsNone(dp.followup(self.item, _stock(self.cup), self.date))

    def test_breakout_days_after_forming(self):
        after = [98.0, 99.0, 103.0, 104.0]  # 3日目の終値でピボット(約100.5)を上抜け
        res = dp.followup(self.item, _stock(np.concatenate([self.cup, after])), self.date)
        self.assertEqual(res["days"], 4)
        self.assertEqual(res["breakout_days"], 3)
        self.assertAlmostEqual(res["pct"], (104 / 97 - 1) * 100, places=1)
        self.assertEqual(res["closes"], after)

    def test_no_breakout(self):
        res = dp.followup(self.item, _stock(np.concatenate([self.cup, [96.0, 95.0]])), self.date)
        self.assertIsNone(res["breakout_days"])
        self.assertLess(res["pct"], 0)


class BuildPickupTest(unittest.TestCase):
    def test_picks_recent_stocks_with_strong_earnings(self):
        def item(code, label):
            return {"code": code, "pattern_label": label, "status": "forming"}

        days = {
            "2026-10-20": [item("11110", "VCP")],
            "2026-10-21": [item("11110", "カップウィズハンドル"), item("22220", "VCP")],
            "2026-10-22": [item("33330", "VCP")],
        }
        good = [
            {"disc_date": "2026-07-30", "cur_per_type": "1Q", "cur_fy_st": "2026-04-01",
             "cur_fy_en": "2027-03-31", "sales": 120, "op": 15},
            {"disc_date": "2025-07-30", "cur_per_type": "1Q", "cur_fy_st": "2025-04-01",
             "cur_fy_en": "2026-03-31", "sales": 100, "op": 10},
        ]
        weak = [dict(good[0], sales=105), good[1]]
        fins = {"11110": good, "22220": weak, "33330": good}
        picks = dp.build_pickup(days, ["2026-10-21", "2026-10-22"], fins, "2026-10-22")
        self.assertEqual([p["code"] for p in picks], ["11110", "33330"])
        # 期間内の出現だけを数え、表示は最後に出たときの判定結果を使う
        self.assertEqual([a["date"] for a in picks[0]["appearances"]], ["2026-10-21"])
        self.assertEqual(picks[0]["pattern_label"], "カップウィズハンドル")
        self.assertEqual(picks[0]["detected_date"], "2026-10-21")
        self.assertAlmostEqual(picks[0]["fins"]["sales_yoy"], 20.0)


if __name__ == "__main__":
    unittest.main()
