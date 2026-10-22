import sqlite3
import tempfile
import unittest
from pathlib import Path

import db
import db_maintenance


def _index_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def _make_old_db(db_path: Path, n_codes: int = 200, n_days: int = 50) -> None:
    """重複索引(idx_stocks_daily_code)がある旧スキーマのDBを作る。"""
    conn = db.get_connection(db_path)
    db.init_db(conn)
    conn.execute("CREATE INDEX idx_stocks_daily_code ON stocks_daily(code)")
    rows = [
        {
            "code": f"{c:05d}", "date": f"2026-{d // 28 + 1:02d}-{d % 28 + 1:02d}",
            "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
            "volume": 100, "turnover": 150.0, "adj_factor": 1.0,
        }
        for c in range(n_codes)
        for d in range(n_days)
    ]
    db.upsert_stocks_daily(conn, rows)
    conn.close()


class CompactTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def test_compact_drops_redundant_index_and_shrinks_file(self):
        _make_old_db(self.db_path)
        self.assertIn("idx_stocks_daily_code", _index_names(self.db_path))
        before = self.db_path.stat().st_size

        db_maintenance.compact(self.db_path)

        names = _index_names(self.db_path)
        self.assertNotIn("idx_stocks_daily_code", names)
        self.assertIn("idx_stocks_daily_date", names)
        self.assertLess(self.db_path.stat().st_size, before)

    def test_compact_keeps_all_rows(self):
        _make_old_db(self.db_path, n_codes=10, n_days=5)
        db_maintenance.compact(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM stocks_daily").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 50)


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.sqlite3"
        _make_old_db(self.db_path, n_codes=10, n_days=5)

    def tearDown(self):
        self._tmp.cleanup()

    def test_count_stock_rows(self):
        self.assertEqual(db_maintenance.count_stock_rows(self.db_path), 50)

    def test_verify_ok_when_rows_not_decreased(self):
        self.assertEqual(db_maintenance.verify(self.db_path, min_rows=50), [])

    def test_verify_fails_when_rows_decreased(self):
        problems = db_maintenance.verify(self.db_path, min_rows=51)
        self.assertEqual(len(problems), 1)
        self.assertIn("50", problems[0])


if __name__ == "__main__":
    unittest.main()
