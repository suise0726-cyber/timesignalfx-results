# TimeSignalFX 日次実績パイプライン

MT4 が出力する確定取引履歴から、JST 日次の勝敗・pips・確定円損益・月間累計を集計し、X 投稿文と 1600×900 PNG を生成します。

初期状態は **dry-run 専用**です。`python timesignalfx_daily.py` を実行しても X 投稿や GitHub 公開は行いません。

## 現在のデータ経路

- 出力元: MT4 `ReadOnlyAccountHistoryExporter`（15分間隔）
- 履歴CSV: `C:\Users\Owner\AppData\Roaming\MetaQuotes\Terminal\Common\Files\account_history_export.csv`
- 日付基準: CSV の `close_time_jst`
- 対象取引: 9月EAのマジック番号 `2026091xxx`
- 確定円損益: `profit + swap + commission`
- pips: USDJPY の価格差を売買方向に合わせて計算（1 pip = 0.01円）

## セットアップ

```powershell
python -m pip install -r requirements.txt
```

## dry-run

前日分（JST）を生成:

```powershell
python timesignalfx_daily.py
```

日付と対象マジック番号を指定:

```powershell
python timesignalfx_daily.py --date 2026-08-28 --magic-prefix 2026081
```

生成物:

- `build/YYYY-MM-DD/post.txt`
- `build/YYYY-MM-DD/result.json`
- `build/YYYY-MM-DD/YYYY-MM-DD.png`
- `build/YYYY-MM-DD/buffer-payload.json`（確認用。API送信はしない）

`--allow-empty` を付けると取引ゼロの日も「取引なし」として生成できます。通常は履歴欠損との取り違えを防ぐためエラー終了します。

## Buffer と GitHub（次段階）

Buffer は現在 GraphQL API を使います。APIキーは Buffer の **Settings → API → Personal Access → New Key** で作成し、ソースへ書かず Windows の環境変数 `BUFFER_API_KEY` に保存します。チャンネルIDも `BUFFER_CHANNEL_ID` に保存します。

Buffer の画像投稿は公開 HTTPS URL が必須です。実運用では生成PNGをこの公開リポジトリの `public/` にコミットし、次の形式の URL を `buffer-payload.json` の `image_url` として使います。

```text
https://raw.githubusercontent.com/suise0726-cyber/timesignalfx-results/main/public/YYYY-MM-DD.png
```

実際のGitHub公開とBuffer送信は、dry-run結果の確認後に有効化します。

