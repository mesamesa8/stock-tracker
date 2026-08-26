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
| `fetch_stocks_yf.py` | yfinanceで直近12週間の個別銘柄データを取得(J-Quantsの遅延配信の穴埋め) |
| `technical_features.py` | 移動平均線(25/75/200日)・RSI(14日)などの特徴量計算をまとめた共通モジュール(他の分析スクリプトから利用) |
| `analyze_new_highs.py` | ②52週高値・年初来高値の更新を検出し、その後の値動きを集計する分析スクリプト |
| `analyze_big_gainers.py` | ②一定期間で大きく上昇した銘柄を検出し、上昇”直前”の特徴(値動き・出来高・52週高値からの位置)を分析するスクリプト |
| `analyze_breakout_vs_reversal.py` | ②52週高値圏の銘柄について利確/損切りをシミュレーションし、パターンの違いを比較するスクリプト |
| `analyze_by_sector.py` | ②利確/損切りシミュレーションの結果を33業種区分ごとに集計し、業種による違いを比較するスクリプト |
| `analyze_correlations.py` | ②全特徴量(テクニカル指標・業種・曜日・月・日経平均の地合い)とトレード結果の相関を自動探索するスクリプト |
| `analyze_breakout_count_detail.py` | ②「直近120日の高値更新回数」を1回刻みで詳細分析し、出来高・直前の値上がり率との組み合わせも見るスクリプト |
| `backtest_rule.py` | ③発見したルール(高値更新回数の上限・適度な上昇率の範囲)をフィルターとして適用し、フィルターなしの場合と成績を比較するバックテストスクリプト |
| `optimize_tp_sl.py` | ③利確ライン・損切りラインの組み合わせを総当たりで試し、最も平均リターンが良い設定を探す最適化スクリプト |
| `daily_screener.py` | ③毎日のひけ後に、ルールに合致する銘柄を期待値順にリストアップするスクリーニングスクリプト(GitHub Actionsで自動実行) |
| `build_web_data.py` | Webアプリ表示用に、過去60日分のスクリーニング結果+株価推移を軽量JSONとして書き出すスクリプト(GitHub Actionsで自動実行) |
| `docs/` | GitHub Pagesで公開するスマホ用Webアプリ本体(HTML/CSS/JS) |
| `TRADING_RULE.md` | ②③で確立したトレードルールをまとめた文書 |
| `HOW_TO_TRADE.md` | 実際の売買時にルールをどうチェックするかの手順書 |
| `.github/workflows/daily_fetch.yml` | GitHub Actionsでの自動実行設定 |
| `market_data.sqlite3` | 蓄積されるデータベース(初回実行時に自動作成) |

## 直近12週間のデータについて

J-Quants無料プランは12週間遅延配信のため、直近のデータは`fetch_stocks_yf.py`
がyfinance経由で個別に取得して埋めます。`main.py`を実行すると自動的に
ステップ4としてこれも実行されるので、通常は意識する必要はありません。
単独で実行したい場合は以下のようにも使えます。

```bash
python3 fetch_stocks_yf.py             # 直近95日分を取得
python3 fetch_stocks_yf.py --days 30   # 直近30日分だけ取得
```

古い日付のデータは後日J-Quants側から正式なデータが届いた時点で自動的に
上書き(より正確な値に更新)されます。

**前日比率が欲しい場合**は、`stocks_daily`テーブルではなく
`stocks_daily_with_change`という**ビュー**を参照してください。生データは
そのままに、SQL側で自動的に前営業日比の変化率(%)を計算して返します。

```sql
SELECT code, date, close, prev_close, change_pct
FROM stocks_daily_with_change
WHERE code = '86970'
ORDER BY date DESC
LIMIT 10;
```

## 無料プランのデータ遅延について(重要)

J-Quants無料プランは**データが約12週間(84日)遅延配信**されます。そのため
`main.py`は「今日」ではなく「**今日から84日前**」を自動的に対象日として
取得するように作ってあります(引数なしで実行した場合)。GitHub Actionsの
自動実行も、この仕組みにより日々新しく解禁される日付を自動で拾っていき
ます。

```bash
# 引数なし: 自動的に「今日から84日前」を取得(無料プラン向けデフォルト)
python main.py

# 特定日を明示的に指定したい場合
python main.py --date 2026-01-05
```

**将来、有料プラン(Light以上)にアップグレードした場合**は、遅延がなくなる
ので `--delay-days 0` を指定してください(GitHub Actionsのワークフロー
ファイル内の `python main.py` の行を `python main.py --delay-days 0` に
書き換えます)。

```bash
python main.py --delay-days 0   # 今日のデータを取得(有料プラン向け)
```

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

## ②: 大きく上昇した銘柄の"直前"の特徴分析(analyze_big_gainers.py)

「52週高値を更新した後」ではなく、「一定期間で大きく上昇した銘柄が、
上昇が始まる**直前**にどんな状態だったか」を調べるスクリプトです。

```bash
python3 analyze_big_gainers.py                              # 20営業日で+50%以上(デフォルト)
python3 analyze_big_gainers.py --horizon 60 --threshold 30   # 60営業日で+30%以上、のように変更可能
```

調べる特徴量は3つです。

- `trailing_return_pct`: 上昇開始前20日間の値動き(既に上昇基調だったか、横ばい/下落からの反発だったか)
- `volume_ratio_vs_avg`: 当日出来高が直近平均の何倍か(出来高急増を伴っていたか)
- `pct_below_52w_high`: 52週高値からどれだけ離れた位置にいたか(高値圏からの続伸か、安値圏からの反発か)

これらを「大きく上昇したイベント群」と「全期間平均(ベースライン)」で
比較し、差が出ているかを確認します。**重要な設計上のポイントとして、
特徴量はすべて上昇が始まる"前日以前"のデータだけを使って計算しています**
(未来の情報を使うと、実際のトレードでは使えない後知恵の指標になって
しまうため)。

出力は`analysis_output/big_gainers_precursor_features.csv`(比較結果)と
`big_gainers_events.csv`(個別イベント一覧)です。

## ②: 利確/損切りシミュレーション(analyze_breakout_vs_reversal.py)

「52週高値を更新した、または高値圏(-10%以内)にいる銘柄」を対象に、
実際に利確ライン・損切りラインを設定してトレードしたらどうなっていたかを
シミュレーションし、「うまくブレイクしたパターン」と「損切りになった
パターン」の違いを比較します。損切りを減らす条件を探すための分析です。

```bash
python3 analyze_breakout_vs_reversal.py
# 条件を変更する場合
python3 analyze_breakout_vs_reversal.py --cohort-max-below 10 --take-profit 15 --stop-loss 8 --hold-days 20
```

| オプション | 意味 | デフォルト |
|---|---|---|
| `--cohort-max-below` | 52週高値から何%以内を対象にするか | 10 |
| `--take-profit` | 利確ライン | +15% |
| `--stop-loss` | 損切りライン | -8% |
| `--hold-days` | 最大保有営業日数 | 20(約1ヶ月) |

エントリー(その日の終値で買う)後、指定した保有期間内に利確ラインと
損切りラインのどちらに先に到達するかを日次の高値・安値を使って判定し、
`take_profit`(利確) / `stop_loss`(損切り) / `timeout_win`・
`timeout_loss`(期間内にどちらにも届かず、最終的にプラス/マイナス)の
4パターンに分類します。同じ日に両方の条件を満たす場合は、日足データ
では日中の値動きの順序が分からないため、保守的に損切りを優先します。

出力される`analysis_output/breakout_vs_reversal_features.csv`では、
この4パターンごとに「エントリー時点で何が違ったか」(直前の値動き・
出来高・52週高値からの位置)を比較できます。ここで`stop_loss`グループ
と`take_profit`グループの間に明確な違いが見つかれば、それが**損切りを
減らすための実践的な条件**になります。

## ②: 業種別の分析(analyze_by_sector.py)

`analyze_breakout_vs_reversal.py`と同じ利確/損切りシミュレーションを
行い、その結果を33業種区分ごとに集計します。業種によって52週高値圏の
成功率に違いがあるかを確認できます。

```bash
python3 analyze_by_sector.py
python3 analyze_by_sector.py --min-events 100   # 業種ごとの最低イベント数を変更(デフォルト200)
```

結果は平均リターンの高い順に表示され、`analysis_output/sector_comparison.csv`
にも保存されます。イベント数が少ない業種は偶然のブレが大きく出やすいため、
デフォルトでは200件未満の業種は結果から除外しています。

## ②: 全特徴量の相関を自動探索(analyze_correlations.py)

これまで個別に試してきた特徴量(RSI・移動平均線・業種)に加えて、
**曜日・月(季節性)・日経平均の直近20日リターン(相場全体の地合い)**も
含めて、トレード結果との相関の強さを自動的にランキングします。「次に
どこを深掘りすべきか」の当たりをつけるための探索的ツールです。

```bash
python3 analyze_correlations.py
```

数値系の特徴量は相関係数、業種・曜日・月といったカテゴリ系の特徴量は
「グループ間の平均リターンの差(スプレッド)」で、それぞれ影響の大きさを
評価します。相関係数については、サンプル数から見て「このくらいの大きさ
があれば偶然のブレとは考えにくい」という簡易的な目安も一緒に表示されます
(厳密な統計的検定ではないので、あくまで参考値です)。

結果は`analysis_output/correlations.csv`(数値特徴量)、
`by_weekday.csv`・`by_month.csv`・`by_sector_full.csv`(カテゴリ別)に
保存されます。

## ③: 発見したルールのバックテスト(backtest_rule.py)

②で見つかった「高値更新回数」「適度な値上がり率」というルールを実際に
フィルターとして適用し、フィルターなしの場合(コホート全体)と成績を
比較します。

```bash
python3 backtest_rule.py
# 条件を変更する場合
python3 backtest_rule.py --max-breakout-count 3 --min-momentum 1 --max-momentum 8
```

| オプション | 意味 | デフォルト |
|---|---|---|
| `--max-breakout-count` | 直近120日の高値更新回数の上限 | 3 |
| `--min-momentum` / `--max-momentum` | 直前20日値上がり率の許容範囲 | 1.0% 〜 8.0% |

「フィルターなし」と「ルール適用後」の勝率・平均リターン・利確/損切りの
内訳を並べて比較できます。件数(n)が大きく減っている場合、ルールが
厳しすぎて実際のトレード機会が少なくなりすぎていないかも確認してください。

## ③: 利確/損切りラインの最適化(optimize_tp_sl.py)

backtest_rule.py で確立したエントリールールを固定した上で、利確ライン・
損切りラインの組み合わせを総当たりで試し、平均リターンが最も良い設定を
探します。

```bash
python3 optimize_tp_sl.py
# 試す幅を変更する場合
python3 optimize_tp_sl.py --tp-grid 8,10,12,15,20,25 --sl-grid 4,6,8,10,12
```

デフォルトでは利確5パターン×損切り5パターン=25通りを試します。
組み合わせ数が多いほど時間がかかるので、まずはデフォルトで試し、
良さそうな範囲が絞れてきたら`--tp-grid`・`--sl-grid`で細かく調整すると
効率的です。結果は平均リターンの高い順に表示され、
`analysis_output/optimize_tp_sl_results.csv`に全組み合わせが保存されます。

**注意**: 利確ラインを広げるほど平均リターンは上がりやすくなりますが、
その分「利確に届くまでの期間が長くなる」「途中で損切りに引っかかる
リスクも増える」というトレードオフがあります。単純に平均リターンが
一番高い設定を選ぶのではなく、勝率や損切り率とのバランスも見て判断
することをおすすめします。

## ③: 毎日のスクリーニング(daily_screener.py)

TRADING_RULE.md のエントリールールに合致する銘柄を、その日の終値時点で
抽出し、**総合スコア**の高い順にリストアップします。GitHub Actionsで
`main.py`の後に自動実行されるため、通常は手元で実行する必要はありません。

```bash
python3 daily_screener.py
python3 daily_screener.py --top 30   # 上位30件だけ表示
```

総合スコアは以下の3つの要素の合計です。

1. **期待値(縮小推定済み)**: 「高値更新回数×直前の値上がり率」の12パターンごとの過去の平均リターン。件数が少ないマスは、偶然のブレを避けるため全体平均へ寄せる補正をかけている
2. **業種調整(重み0.5倍)**: 業種ごとの過去の実績差。相場環境に依存する可能性があるため、影響は控えめに(半分の重み、かつ件数が少ない業種は0へ補正)
3. **チャートの締まり具合**: 本日の候補の中で、値幅が狭い(タイトな)銘柄ほど加点

各要素は結果のCSVに個別の列としても出力されるので、「なぜこのスコアになったか」を後から確認できます。

結果は `analysis_output/screening_latest.csv`(常に最新版)と
`analysis_output/screening_YYYY-MM-DD.csv`(その日ごとの履歴)に
保存され、GitHub Actionsではリポジトリに自動コミットされます。

実際の売買判断の手順は `HOW_TO_TRADE.md` を参照してください。

## Webアプリ用データの生成(build_web_data.py)

スマホ用Webアプリの表示に使う、軽量なJSONファイルを生成します。
GitHub Actionsで`daily_screener.py`の後に自動実行されるため、
通常は手元で実行する必要はありません。

```bash
python3 build_web_data.py
python3 build_web_data.py --days 60   # 遡る日数を変更する場合(デフォルト60日)
```

- 直近60日分の`screening_YYYY-MM-DD.csv`だけを対象にする(60日より古いものは自動的に無視・削除されるため、データ量は増え続けず一定に保たれる)
- 各銘柄について、ランキングに載った日から現在までの株価推移(`price_history`)と、現在価格・現在の騰落率(`current_close` / `current_pct_change`)を計算
- 出力先: `docs/data/latest.json`(最新日)、`docs/data/history/YYYY-MM-DD.json`(日ごとの履歴)、`docs/data/history/index.json`(閲覧可能な日付一覧)

## スマホ用Webアプリ(docs/)

`docs/`フォルダに、スマホで見られる一覧画面(HTML/CSS/JavaScript、
フレームワーク不使用)が入っています。GitHub Pagesで無料公開できます。

### GitHub Pagesの設定手順(初回のみ)

1. GitHubのリポジトリページで `Settings` タブを開く
2. 左メニューの `Pages` をクリック
3. `Build and deployment` の `Source` を `Deploy from a branch` に設定
4. `Branch` を `main` 、フォルダを **`/docs`** に設定して `Save`
5. 数分待つと、`https://<ユーザー名>.github.io/<リポジトリ名>/` でアクセスできるようになる

以降は、GitHub Actionsが毎日`docs/data/`以下のJSONを自動更新するので、
特別な操作なしにページの中身も毎日更新されます。

### 画面の内容

- **本日の候補**タブ: `daily_screener.py`が抽出した当日の候補銘柄を、総合スコアの高い順に一覧表示
- **履歴**タブ: 過去(最大60日分)の各日の候補銘柄一覧を、日付を選んで表示。各銘柄について、載った日からの株価推移をスパークライン(小さな折れ線グラフ)・騰落率・経過営業日数で確認できる
- **並び替え**: 総合スコア順に加えて、値上がり率順・値下がり率順(履歴タブのみ)・期待勝率順・経過日数順・銘柄コード順を選べる
- 各銘柄カードに、総合スコアの内訳(期待値・業種調整・チャートの締まり具合)を常時表示
- 画面右上の「?」ボタンから、免責事項とスコア算出式の説明を表示

## 次のステップ(②③に向けて)

`analyze_big_gainers.py` と `analyze_breakout_vs_reversal.py` は、
`technical_features.py` に定義された以下の特徴量を共通で使っています。

| 列名 | 意味 |
|---|---|
| `trailing_return_pct` | 直近20営業日の値動き(モメンタム) |
| `volume_ratio_vs_avg` | 当日出来高が直近20日平均の何倍か |
| `pct_below_52w_high` | 52週高値からの乖離率 |
| `pct_vs_ma25` / `pct_vs_ma75` / `pct_vs_ma200` | 25日/75日/200日移動平均線からの乖離率 |
| `ma25_above_ma75` | ゴールデンクロス状態(1=短期線が長期線より上) |
| `rsi14` | RSI(14日、0〜100。一般的に70以上で買われすぎ、30以下で売られすぎとされる) |
| `volatility_contraction_pct` | 直近20日間の1日の値幅(高値-安値)が終値に対して平均何%あったか。小さいほど「タイトなもみ合いからのブレイク」 |
| `gap_pct` | 当日の始値が前日終値に対してどれだけ窓を開けたか(%) |
| `recent_breakout_count_120d` | 直近120日以内に52週高値を更新した日数。多いほど「何度も高値を更新している最中」、少ないほど「久しぶりの高値更新」 |

このデータが1〜2ヶ月ほど溜まったら、次は

- `stocks_daily` テーブルから「52週高値を更新した銘柄」を検出するクエリ/スクリプトを作成
- その銘柄の更新後N日間のリターン分布を集計
- 勝率・平均リターン・最大ドローダウンなどから法則性を検証

という②のステップに進みます。データが溜まるまで、まずは①の自動収集が
安定して回ることを確認しましょう。

## ②: 52週高値・年初来高値の分析(analyze_new_highs.py)

```bash
python3 analyze_new_highs.py                # 全銘柄を対象に分析
python3 analyze_new_highs.py --code 86970    # 特定銘柄だけに絞って確認
```

実行すると以下が出力されます。

- コンソールに、高値更新タイプ(52週高値/年初来高値)ごと・観測期間
  (1日後/5日後/10日後/20日後/60日後)ごとの勝率・平均リターン・中央値
  などの集計結果
- `analysis_output/new_highs_summary.csv` — 上記の集計結果
- `analysis_output/new_highs_events.csv` — 個々の高値更新イベントの一覧
  (どの銘柄がいつ高値更新し、その後どうなったかを個別に確認できる)

### 集計結果の見方(3つの改善を反映)

出力される`new_highs_summary.csv`には、以下の列があります。

| 列名 | 意味 |
|---|---|
| `n_events` | 集計対象になったイベント数(連続する高値更新はまとめて1件、データ異常が疑われる期間は除外済み) |
| `win_rate_pct` | プラスで終わった割合 |
| `mean_return_pct` / `median_return_pct` | そのまま(市場調整なし)の平均・中央値リターン |
| `mean_excess_vs_market_pct` / `median_excess_vs_market_pct` | **同じ日の全銘柄平均リターンを差し引いた「超過リターン」**。これがプラスであれば、単なる地合いの良さではなく、高値更新という条件そのものに意味がある可能性が高い |

### 3つの改善内容

1. **連続イベントの重複排除**: 同じ上昇トレンド中に何日も連続で高値を更新するケースは、直前の日が高値更新でなかった「初回」だけをカウントするようにしました(`is_52w_high_break_first`列)。コンソールには「延べ件数」と「まとめた件数」の両方が表示されます。
2. **市場調整後の超過リターン**: 同じ日に全銘柄が平均してどれだけ動いたかを差し引いた`excess_return`列を追加しました。`mean_return_pct`(生のリターン)が良くても`mean_excess_vs_market_pct`(超過リターン)が0に近ければ、「ただ市場全体が上がっていただけ」の可能性が高いです。
3. **データ異常の除外**: 1日で±25%を超える変動があった日を「データ異常の疑い」として検出し(`analysis_output/anomaly_days.csv`に一覧出力)、その日を含む前方ウィンドウのリターンは集計から自動的に除外しています。株式分割の未調整などで極端な値が出るのを防ぎます。

### その他の注意点

- **52週高値**は「過去364日間(当日を除く)の最高値」を、当日の高値が
  上回った日として検出しています。ただし、その銘柄について過去
  **200営業日分のデータが無い**期間は「データ不足による見せかけの高値
  更新」の可能性があるため、集計対象から自動的に除外しています。
- **年初来高値**はその年の1/1〜前日までの最高値を基準にしているため、
  年明け直後はイベントが検出されやすくなる(基準となる過去データが
  少ないため)傾向があります。参考情報として見てください。
- ここで出てくる勝率・平均リターンは、あくまで**過去データ上の統計**
  であり、将来の値動きを保証するものではありません。③でトレード
  手法を検討する際は、業種・出来高・市場全体の地合いなど他の要因も
  合わせて検証することをおすすめします。
