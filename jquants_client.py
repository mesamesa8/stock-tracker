"""
J-Quants API (V2) 呼び出し用のシンプルなクライアント。

認証方式: 2025年12月のV2リニューアル以降、ダッシュボードで発行する
API キーを 'x-api-key' ヘッダーに付与する方式に統一されている。
リフレッシュトークン方式(V1)は廃止済みなので実装しない。

参考: https://jpx-jquants.com/ja/spec/quickstart
"""

from __future__ import annotations

import os
import time
import requests

BASE_URL = "https://api.jquants.com/v2"

# 無料プランは 5リクエスト/分 という制限。60/5=12秒が理論上の下限だが、
# 安全マージンを取って15秒間隔にする(60/15=4回/分に収まる)。
# 有料プラン(Light以上は60回/分)に上げたらもっと短くしてよい。
DEFAULT_SLEEP_SEC = 15.0

# 429(レート制限超過)が出たときのリトライ待機時間(秒)。公式ドキュメントに
# よると「大幅に超過すると5分程度遮断される」ことがあるため、段階的に
# 長めに待つ。
RETRY_WAIT_SECONDS = [30, 60, 120, 180]


class JQuantsClient:
    def __init__(self, api_key: str | None = None, sleep_sec: float = DEFAULT_SLEEP_SEC):
        self.api_key = api_key or os.environ.get("JQUANTS_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "JQUANTS_API_KEY が設定されていません。環境変数か引数で指定してください。"
            )
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": self.api_key})
        self.sleep_sec = sleep_sec

    def _get(self, path: str, params: dict) -> dict:
        url = f"{BASE_URL}{path}"
        resp = self.session.get(url, params=params, timeout=30)

        if resp.status_code == 429:
            for i, wait_sec in enumerate(RETRY_WAIT_SECONDS, start=1):
                print(
                    f"  [WARN] レート制限(429)を検知。{wait_sec}秒待機してリトライします "
                    f"({i}/{len(RETRY_WAIT_SECONDS)})..."
                )
                time.sleep(wait_sec)
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code != 429:
                    break

        if resp.status_code >= 400:
            # エラー内容をログに出しておく(400番台は原因がボディに書かれていることが多い)
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            print(f"  [DEBUG] APIエラー応答本文: {detail}")

        resp.raise_for_status()
        return resp.json()

    def _get_paginated(self, path: str, params: dict, data_key: str) -> list[dict]:
        """pagination_key がある限り全ページ取得して結合する。"""
        results: list[dict] = []
        params = dict(params)
        while True:
            payload = self._get(path, params)
            results.extend(payload.get(data_key, []))
            pagination_key = payload.get("pagination_key")
            if not pagination_key:
                break
            params["pagination_key"] = pagination_key
            time.sleep(self.sleep_sec)
        return results

    def get_listed_master(self, date: str | None = None) -> list[dict]:
        """
        上場銘柄一覧を取得する。
        date を省略すると最新(当日)時点のマスタが返る。
        戻り値の各要素は 'Code', 'CoName', 'Mkt', 'MktNm', 'S33Nm' 等を含む。
        """
        params = {}
        if date:
            params["date"] = date
        return self._get_paginated("/equities/master", params, data_key="data")

    def get_daily_bars(self, date: str) -> list[dict]:
        """
        指定日の株価四本値を「全銘柄分」取得する(code を指定しないことで
        その日の全上場銘柄分をまとめて取得できる)。
        date 形式: 'YYYYMMDD' または 'YYYY-MM-DD'
        戻り値の各要素は 'Code','Date','O','H','L','C','Vo','Va','AdjFactor' 等を含む。
        """
        return self._get_paginated("/equities/bars/daily", {"date": date}, data_key="data")
