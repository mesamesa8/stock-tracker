"""
SQLiteデータベースの初期化とデータ保存を行うモジュール。

テーブル構成:
  - indices_daily : 日経平均・NASDAQ・SOXなど主要指数の日次OHLCV
  - stocks_daily  : 東証プライム個別銘柄の日次OHLCV
  - stocks_master : 銘柄マスタ(コード・銘柄名・市場区分など)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "market_data.sqlite3"


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")  # 同時書き込み耐性を上げておく
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """テーブルが無ければ作成する。既にあれば何もしない(何度実行しても安全)。"""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS indices_daily (
            symbol      TEXT NOT NULL,   -- 例: '^N225', '^IXIC', '^SOX'
            name        TEXT,            -- 例: '日経平均', 'NASDAQ総合', 'SOX指数'
            date        TEXT NOT NULL,   -- 'YYYY-MM-DD'
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      INTEGER,
            PRIMARY KEY (symbol, date)
        );

        CREATE TABLE IF NOT EXISTS stocks_master (
            code            TEXT PRIMARY KEY,  -- 銘柄コード(例: '86970')
            company_name    TEXT,
            market_code     TEXT,               -- '0111' = プライム 等
            market_name     TEXT,               -- 'プライム' 等
            sector33_name   TEXT,
            updated_at      TEXT                -- マスタを取得した日付
        );

        CREATE TABLE IF NOT EXISTS stocks_daily (
            code        TEXT NOT NULL,
            date        TEXT NOT NULL,   -- 'YYYY-MM-DD'
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      INTEGER,
            turnover    REAL,            -- 売買代金
            adj_factor  REAL,            -- 株式分割等の調整係数
            PRIMARY KEY (code, date)
        );

        CREATE INDEX IF NOT EXISTS idx_stocks_daily_date ON stocks_daily(date);
        CREATE INDEX IF NOT EXISTS idx_stocks_daily_code ON stocks_daily(code);

        -- 前日比率を自動計算するビュー。生データ(stocks_daily)はそのままに、
        -- 参照時に前営業日比の変化率(%)を計算して返す。
        DROP VIEW IF EXISTS stocks_daily_with_change;
        CREATE VIEW stocks_daily_with_change AS
        SELECT
            code,
            date,
            open,
            high,
            low,
            close,
            volume,
            turnover,
            adj_factor,
            LAG(close) OVER (PARTITION BY code ORDER BY date) AS prev_close,
            ROUND(
                (close - LAG(close) OVER (PARTITION BY code ORDER BY date)) * 100.0
                / NULLIF(LAG(close) OVER (PARTITION BY code ORDER BY date), 0),
                2
            ) AS change_pct
        FROM stocks_daily;
        """
    )
    conn.commit()


def upsert_indices(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows: [{'symbol':..., 'name':..., 'date':..., 'open':..., 'high':..., 'low':..., 'close':..., 'volume':...}, ...]"""
    conn.executemany(
        """
        INSERT INTO indices_daily (symbol, name, date, open, high, low, close, volume)
        VALUES (:symbol, :name, :date, :open, :high, :low, :close, :volume)
        ON CONFLICT(symbol, date) DO UPDATE SET
            name=excluded.name, open=excluded.open, high=excluded.high,
            low=excluded.low, close=excluded.close, volume=excluded.volume
        """,
        rows,
    )
    conn.commit()


def upsert_stocks_master(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO stocks_master (code, company_name, market_code, market_name, sector33_name, updated_at)
        VALUES (:code, :company_name, :market_code, :market_name, :sector33_name, :updated_at)
        ON CONFLICT(code) DO UPDATE SET
            company_name=excluded.company_name, market_code=excluded.market_code,
            market_name=excluded.market_name, sector33_name=excluded.sector33_name,
            updated_at=excluded.updated_at
        """,
        rows,
    )
    conn.commit()


def upsert_stocks_daily(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO stocks_daily (code, date, open, high, low, close, volume, turnover, adj_factor)
        VALUES (:code, :date, :open, :high, :low, :close, :volume, :turnover, :adj_factor)
        ON CONFLICT(code, date) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
            volume=excluded.volume, turnover=excluded.turnover, adj_factor=excluded.adj_factor
        """,
        rows,
    )
    conn.commit()
