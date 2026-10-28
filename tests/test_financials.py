import sqlite3
import unittest

import db
import fetch_financials as ff
from financials import load_fins, quarterly_yoy


def _row(disc_date, per_type, fy_st, sales, op):
    """4月始まり(3月決算)の会社の決算サマリー1行。"""
    fy_en = f"{int(fy_st[:4]) + 1}-03-31"
    return {"disc_date": disc_date, "cur_per_type": per_type, "cur_fy_st": fy_st, "cur_fy_en": fy_en,
            "sales": sales, "op": op}


# 3月決算の会社。累計値で、前年(2025年3月期)と当年(2026年3月期)の決算
ROWS = [
    _row("2024-08-05", "1Q", "2024-04-01", 100, 10),
    _row("2024-11-05", "2Q", "2024-04-01", 210, 22),
    _row("2025-02-05", "3Q", "2024-04-01", 330, 30),
    _row("2025-05-10", "FY", "2024-04-01", 460, 45),
    _row("2025-08-05", "1Q", "2025-04-01", 115, 13),   # 1Q: 売上+15%、営業益+30%
    _row("2025-11-05", "2Q", "2025-04-01", 235, 25),   # 2Q単独: 120 vs 110(+9.1%)、12 vs 12(0%)
]


class QuarterlyYoyTest(unittest.TestCase):
    def test_first_quarter_uses_cumulative_value(self):
        res = quarterly_yoy(ROWS, "2025-08-31")
        self.assertEqual(res["period"], "2026年3月期 1Q")
        self.assertEqual(res["disc_date"], "2025-08-05")
        self.assertAlmostEqual(res["sales_yoy"], 15.0)
        self.assertAlmostEqual(res["op_yoy"], 30.0)
        self.assertTrue(res["meets"])

    def test_second_quarter_uses_three_month_values(self):
        res = quarterly_yoy(ROWS, "2025-12-01")
        self.assertEqual(res["period"], "2026年3月期 2Q")
        self.assertAlmostEqual(res["sales_yoy"], round((120 / 110 - 1) * 100, 1))
        self.assertAlmostEqual(res["op_yoy"], 0.0)
        self.assertFalse(res["meets"])

    def test_ignores_disclosures_after_as_of(self):
        res = quarterly_yoy(ROWS, "2025-08-04")
        self.assertEqual(res["period"], "2025年3月期 4Q")
        # 4Q単独: 130 vs (前年データなし) -> 比較できない
        self.assertIsNone(res["sales_yoy"])

    def test_turnaround_is_flagged_but_not_counted(self):
        rows = [_row("2024-08-05", "1Q", "2024-04-01", 100, -5), _row("2025-08-05", "1Q", "2025-04-01", 130, 8)]
        res = quarterly_yoy(rows, "2025-08-31")
        self.assertIsNone(res["op_yoy"])
        self.assertTrue(res["op_turnaround"])
        self.assertFalse(res["meets"])

    def test_missing_previous_quarter_gives_no_yoy(self):
        rows = [r for r in ROWS if not (r["cur_fy_st"] == "2024-04-01" and r["cur_per_type"] == "1Q")]
        res = quarterly_yoy(rows, "2025-12-01")
        self.assertIsNone(res["sales_yoy"])

    def test_correction_replaces_earlier_disclosure(self):
        rows = ROWS + [_row("2025-08-20", "1Q", "2025-04-01", 105, 11)]  # 訂正
        res = quarterly_yoy(rows, "2025-08-31")
        self.assertAlmostEqual(res["sales_yoy"], 5.0)

    def test_none_when_no_disclosure(self):
        self.assertIsNone(quarterly_yoy(ROWS, "2024-01-01"))


class FetchFinancialsTest(unittest.TestCase):
    def test_keeps_only_financial_statements(self):
        base = {"DiscNo": "1", "Code": "11110", "DiscDate": "2025-08-05", "CurPerType": "1Q",
                "CurFYSt": "2025-04-01", "CurFYEn": "2026-03-31", "Sales": "115", "OP": ""}
        row = ff.to_row({**base, "DocType": "1QFinancialStatements_Consolidated_JP"})
        self.assertEqual(row["sales"], 115.0)
        self.assertIsNone(row["op"])
        self.assertIsNone(ff.to_row({**base, "DocType": "EarnForecastRevision"}))

    def test_saved_rows_round_trip(self):
        conn = sqlite3.connect(":memory:")
        db.init_db(conn)
        row = ff.to_row({"DiscNo": "1", "Code": "11110", "DiscDate": "2025-08-05", "CurPerType": "1Q",
                         "CurFYSt": "2025-04-01", "CurFYEn": "2026-03-31", "Sales": "115", "OP": "13",
                         "DocType": "1QFinancialStatements_Consolidated_JP"})
        db.upsert_fins_summary(conn, [row])
        loaded = load_fins(conn)
        self.assertEqual(loaded["11110"][0]["sales"], 115.0)
        self.assertEqual(loaded["11110"][0]["cur_per_type"], "1Q")


if __name__ == "__main__":
    unittest.main()
