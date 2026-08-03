# 株価トレード分析ツール - データ収集基盤(ステップ①)

日経平均・NASDAQ・SOX等の主要指数、および東証プライム全銘柄の日次
始値・高値・安値・終値・出来高を毎日自動で蓄積するツールです。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `main.py` | 毎日実行するメインスクリプト |
| `db.py` | SQLiteのテーブル定義・保存処理 |
| `jquants_client.py` | J-Quants API (V2) の呼び出しラッパー |
| `fetch_stocks.py` | J-Quantsから東証プライム銘柄の四本値を取得 |
| `fetch_indices.py` | yfinanceから日経平均・CME日経225先物・ドル円・NASDAQ・SOX・S&P500・VIX・NYダウ・KOSPI・TOPIX(ETF代替)を取得 |
| `.github/workflows/daily_fetch.yml` | GitHub Actionsでの自動実行設定 |
| `market_data.sqlite3` | 蓄積されるデータベース(初回実行時に自動作成) |

## セットアップ手順

### 1. ローカルでまず動作確認する

```bash
cd stock-tracker
pip install -r requirements.txt

# J-QuantsのAPIキーを環境変数にセット(ダッシュボードから取得したもの)
export JQUANTS_API_KEY="あなたのAPIキー"

python main.py
```

正常に動くと `market_data.sqlite3` というファイルが作られ、指数データと
銘柄データが保存されます。中身は以下のように確認できます。

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('market_data.sqlite3')
print(conn.execute('SELECT * FROM indices_daily LIMIT 5').fetchall())
print(conn.execute('SELECT * FROM stocks_daily LIMIT 5').fetchall())
"
```

**注意(無料プランの場合)**: J-Quants無料プランはデータが12週間遅延配信
されるため、`python main.py`(=本日分)を実行しても株価データは0件に
なります。過去日付を指定して動作確認してください。

```bash
python main.py --date 2026-05-01
```

### 1.5 過去分をまとめて取得したい場合(バックフィル)

`main.py --date` は「1日分だけ」取得する仕様です。たとえば2026年1月1日
から今日までのように、過去の一定期間をまとめて取得したい場合は
`backfill.py` を使ってください。

```bash
python3 backfill.py --start-date 2026-01-01
```

終了日を省略すると本日まで自動で取得します。土日・祝日は自動でスキップ
され、途中でエラーが出て止まっても、同じコマンドをもう一度実行すれば
(既に取得済みの日付は自動スキップされるので)続きから再開できます。

無料プランはレート制限が厳しいため、期間が長いと数十分かかることがあり
ます。気長に待ってください。

日々の自動更新(GitHub Actions)は `main.py`(1日分)がそのまま使われる
ので、バックフィルは最初の1回だけ実行すればOKです。

### 2. GitHubリポジトリを作る

1. GitHubで新しいリポジトリを作成(Private でも Public でもどちらでも可)
2. このフォルダの中身をすべてそのリポジトリにpush

```bash
cd stock-tracker
git init
git add .
git commit -m "Initial commit: data collection pipeline"
git branch -M main
git remote add origin https://github.com/<あなたのユーザー名>/<リポジトリ名>.git
git push -u origin main
```

### 3. APIキーをGitHub Secretsに登録する

GitHub Actions上ではAPIキーをコードに書けないため、Secretsという仕組みで
安全に渡します。

1. リポジトリの `Settings` → `Secrets and variables` → `Actions` を開く
2. `New repository secret` をクリック
3. Name: `JQUANTS_API_KEY` / Secret: あなたのAPIキー を入力して保存

### 4. 自動実行を確認する

`.github/workflows/daily_fetch.yml` により、毎日 日本時間16:00頃に自動で
データ取得が走り、結果が `market_data.sqlite3` としてリポジトリに
コミットされます。

すぐに動作確認したい場合は、GitHubリポジトリの `Actions` タブ →
`Daily Market Data Fetch` → `Run workflow` で手動実行できます。

## 次のステップ(②③に向けて)

このデータが1〜2ヶ月ほど溜まったら、次は

- `stocks_daily` テーブルから「52週高値を更新した銘柄」を検出するクエリ/スクリプトを作成
- その銘柄の更新後N日間のリターン分布を集計
- 勝率・平均リターン・最大ドローダウンなどから法則性を検証

という②のステップに進みます。データが溜まるまで、まずは①の自動収集が
安定して回ることを確認しましょう。
