# stock-tracker(高値ブレイク板)

東証プライム全銘柄の日次データを蓄積し、52週高値ブレイク銘柄を統計的にスコアリングして、スマホのWebアプリ(GitHub Pages)で確認するプロジェクト。

## ユーザーについて
- 日本語で応答すること。Python / GitHub / SQLite は初心者。専門用語は噛み砕き、手順は具体的に示す。
- 環境は Windows(`D:\stock-tracker`)。Python は `py -3.14` を使う(`python3` は不可)。
- **ファイルを変更したら、変更したファイル名を必ず伝える。**

## 目的
1. 日次データ自動蓄積(GitHub Actions、月100〜1,000円以内=実質無料)
2. 52週高値更新銘柄の統計分析(入ってよい/避けるチャート、タイミング、利確・損切り位置)
3. 損を減らすトレードルール(オニール式の現代的改良)
4. 毎日ひけ後に、ルール合致銘柄を総合スコア順にリストアップ
5. スマホで確認(`docs/` を GitHub Pages で公開。Publicリポジトリ)
将来は他者利用も視野(投資助言業規制に注意。画面には免責を表示済み)。

## 構成
- データ取得: `main.py`(指数→マスタ→J-Quants→yfinance穴埋め)、`backfill.py`、`fetch_*.py`、`jquants_client.py`、`db.py`
  - J-Quants無料プランは12週間(84日)遅延。直近分は yfinance で補完。
- 分析: `analyze_*.py`、`technical_features.py`、`backtest_rule.py`、`optimize_tp_sl.py`
- 日次処理: `daily_screener.py`(→ `analysis_output/screening_YYYY-MM-DD.csv`)→ `build_web_data.py`(→ `docs/data/`、直近60日分のみ保持)→ `detect_patterns.py`(判定は `chart_patterns.py`。→ `docs/data/patterns.json`、「注目」タブ用)
- Web: `docs/index.html` / `app.js` / `style.css`(フレームワーク不使用)
- 自動実行: `.github/workflows/daily_fetch.yml`(毎日 JST 16:00。DBは Release `db-latest` から取得→更新→検証→アップロード、その他は `git add -A .` でコミット)
- DB保守: `db_maintenance.py`(compact / count / verify)。テストは `py -3.14 -m unittest discover tests`
- ルール文書: `TRADING_RULE.md` / `HOW_TO_TRADE.md` / `README.md`

## 採用ルール(daily_screener.py)
52週高値から10%以内 かつ 直近120日の高値更新回数≤3 かつ 直前20日の値上がり+1〜8%。
損切り-8%前後 / 利確+15%前後 / 最大20営業日。
総合スコア = 期待値 + 業種調整(×0.5済) + チャートの締まり。表示値の単純合計がスコア。

## 守ること
- **`market_data.sqlite3` はGitで管理しない(GitHub Releases `db-latest` に置く)。** 手元へは `gh release download db-latest --pattern market_data.sqlite3 --clobber` で取得する。手元で更新したDBをアップロードしない(Actionsとの二重更新でデータ消失が起きる。実際に2025年分が消えた)。バックフィル等でアップロードが必要なときはユーザーに確認してから行う。作業前は必ず pull する。
- DB・`analysis_output/screening_*.csv`・`docs/data/` は自動生成物。手で編集しない。
- Actions の `git add` は `git add -A .` を維持(存在しないパス指定は失敗する)。
- JSON出力で NaN を出さない(`safe_num()` で None に変換)。
- argparse の help 内の `%` は `%%` にする。pandas の `groupby().shift()` で bool が object 化する点に注意。
- `JQUANTS_API_KEY` は環境変数/GitHub Secrets。コードやコミットに書かない。
- リポジトリはPublic。公開して困る情報を置かない。

## 既知の限界
- バックテストは手数料・税・スリッページ未考慮、検証期間は約1.5年。業種効果(銀行業など)は相場環境依存の可能性。
- DBはReleases移行済みだが、移行前にコミットされた過去のDBがGit履歴に残っている(約160MB)。削減には履歴の書き換え(force push)が必要で、未実施。
- `index.html` の算出式表記「業種調整 × 0.5」と、JSONの `sector_adjustment_pct`(0.5倍後の値)が紛らわしい。

## 今後の候補
- 履歴タブの初期表示日を、騰落率に差が出る日にする
- スコア式の見直し、検証期間延長後の再分析
- サービス化に向けた法的整理・データ保存方法の見直し
