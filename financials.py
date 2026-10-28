"""
決算短信のサマリー(fins_summary)から、直近四半期(3ヶ月)の売上高・営業利益の
前年同期比を計算する。

決算短信の数値は期首からの累計なので、四半期(3ヶ月)の値は
  1Q = 1Qの累計、2Q = 2Q累計 - 1Q累計、3Q = 3Q累計 - 2Q累計、FY(4Q) = 通期 - 3Q累計
で求め、前年の同じ四半期と比べる。

ピックアップの条件(オニールの好業績の目安): 売上高 +10%以上 かつ 営業利益 +20%以上。
前年の営業利益が0以下(赤字)のときは増益率を計算できないため、黒字転換でも条件は満たさない。
"""

from __future__ import annotations

import sqlite3

import pandas as pd

SALES_YOY_MIN = 10.0
OP_YOY_MIN = 20.0

PREV_TYPE = {"2Q": "1Q", "3Q": "2Q", "FY": "3Q"}
QUARTER_LABEL = {"1Q": "1Q", "2Q": "2Q", "3Q": "3Q", "FY": "4Q"}


def load_fins(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """銘柄コード -> その銘柄の決算サマリーの行のリスト。テーブルがなければ空。"""
    try:
        df = pd.read_sql(
            "SELECT code, disc_date, cur_per_type, cur_fy_st, cur_fy_en, sales, op FROM fins_summary", conn
        )
    except (sqlite3.Error, pd.errors.DatabaseError):
        return {}
    df = df.astype(object).where(df.notna(), None)
    return {code: g.drop(columns="code").to_dict("records") for code, g in df.groupby("code")}


def _one_year_before(d: str) -> str:
    return (pd.Timestamp(d) - pd.DateOffset(years=1)).strftime("%Y-%m-%d")


def _is_12_months(fy_st: str, fy_en: str) -> bool:
    # 決算期の変更などで12ヶ月でない年度は、前年と比べられないため除外する
    return fy_en is not None and (pd.Timestamp(fy_st) + pd.DateOffset(years=1, days=-1)).strftime("%Y-%m-%d") == fy_en


def _quarter_values(latest: dict[tuple[str, str], dict], fy_st: str, per_type: str) -> tuple | None:
    """(売上高, 営業利益) の3ヶ月の値。必要な決算がそろっていなければ None。"""
    cur = latest.get((fy_st, per_type))
    if cur is None:
        return None
    prev_type = PREV_TYPE.get(per_type)
    if prev_type is None:
        return cur["sales"], cur["op"]
    prev = latest.get((fy_st, prev_type))
    if prev is None:
        return None

    def diff(key):
        return None if cur[key] is None or prev[key] is None else cur[key] - prev[key]

    return diff("sales"), diff("op")


def _yoy(now: float | None, before: float | None) -> float | None:
    if now is None or before is None or before <= 0:
        return None
    return round((now / before - 1) * 100, 1)


def quarterly_yoy(rows: list[dict], as_of: str) -> dict | None:
    """as_of の日までに開示された最新の決算について、四半期の前年同期比を返す。"""
    rows = [r for r in rows if r["disc_date"] <= as_of and r["cur_per_type"] in QUARTER_LABEL and r["cur_fy_st"]]
    if not rows:
        return None
    # 同じ期の決算が複数(訂正など)あれば、後から開示されたものを使う
    latest: dict[tuple[str, str], dict] = {}
    for r in sorted(rows, key=lambda r: r["disc_date"]):
        latest[(r["cur_fy_st"], r["cur_per_type"])] = r
    newest = max(latest.values(), key=lambda r: (r["disc_date"], r["cur_fy_st"], r["cur_per_type"]))
    fy_st, per_type = newest["cur_fy_st"], newest["cur_per_type"]
    fy_en = newest["cur_fy_en"]
    result = {
        "disc_date": newest["disc_date"],
        "period": f"{fy_en[:4]}年{int(fy_en[5:7])}月期 {QUARTER_LABEL[per_type]}" if fy_en else per_type,
        "sales_yoy": None,
        "op_yoy": None,
        "op_turnaround": False,
        "meets": False,
    }
    prev_fy_st = _one_year_before(fy_st)
    prev_fy = next((r for r in latest.values() if r["cur_fy_st"] == prev_fy_st), None)
    if not _is_12_months(fy_st, fy_en) or prev_fy is None or not _is_12_months(prev_fy_st, prev_fy["cur_fy_en"]):
        return result
    now = _quarter_values(latest, fy_st, per_type)
    before = _quarter_values(latest, prev_fy_st, per_type)
    if now is None or before is None:
        return result
    result["sales_yoy"] = _yoy(now[0], before[0])
    result["op_yoy"] = _yoy(now[1], before[1])
    result["op_turnaround"] = before[1] is not None and now[1] is not None and before[1] <= 0 < now[1]
    result["meets"] = (
        result["sales_yoy"] is not None and result["sales_yoy"] >= SALES_YOY_MIN
        and result["op_yoy"] is not None and result["op_yoy"] >= OP_YOY_MIN
    )
    return result
