# TimeSignalFX 日次実績パイプライン

TimeSignalFXの正本SQLiteから、JST日次の勝敗・pips・確定円損益・月間累計を集計し、X投稿文と1600×900 PNGを生成します。

初期状態は **dry-run 専用**です。`python timesignalfx_daily.py` を実行しても X 投稿や GitHub 公開は行いません。

## 現在のデータ経路

- 出力元: MT4 `ReadOnlyAccountHistoryExporter` → TimeSignalFX正本SQLite
- 正本: `C:\keiba_scraper\fx_performance\private\canonical\fx_performance.sqlite3`
- 日付基準: 正本の `close_time_jst`
- 対象取引: サイトと同じ `NORMAL_SIGNAL + UNKNOWN`（AI実験は除外）
- 確定円損益: サイト日次公開と同じPython集計
- pips: USDJPY の価格差を売買方向に合わせて計算（1 pip = 0.01円）
- 投稿前確認: 実績ページHTTP 200、日次・月間・pips・勝敗の表示一致
- 二重防止: 投稿成功後だけ `state/posted.json` へ営業日を記録する想定。月間集計には不使用

## セットアップ

```powershell
python -m pip install -r requirements.txt
```

## dry-run

前日分（JST）を生成:

```powershell
python timesignalfx_daily.py
```

日付を指定:

```powershell
python timesignalfx_daily.py --date 2026-08-28
```

生成物:

- `build/YYYY-MM-DD/post.txt`
- `build/YYYY-MM-DD/result.json`
- `build/YYYY-MM-DD/YYYY-MM-DD.png`
- `build/YYYY-MM-DD/site-verification.json`
- `build/YYYY-MM-DD/buffer-payload.json`（確認用。API送信はしない）

平日の取引ゼロ日は投稿文・PNG・ログを生成しますが、payloadの`eligible_to_post=false`とします。土日も投稿対象外です。サイトと数値が不一致の場合も同様にBuffer送信を禁止します。

## Buffer と GitHub（次段階）

Buffer は現在 GraphQL API を使います。APIキーは Buffer の **Settings → API → Personal Access → New Key** で作成し、ソースへ書かず Windows の環境変数 `BUFFER_API_KEY` に保存します。チャンネルIDも `BUFFER_CHANNEL_ID` に保存します。

Buffer の画像投稿は公開 HTTPS URL が必須です。実運用では生成PNGをこの公開リポジトリの `public/` にコミットし、次の形式の URL を `buffer-payload.json` の `image_url` として使います。

```text
https://raw.githubusercontent.com/suise0726-cyber/timesignalfx-results/main/public/YYYY-MM-DD.png
```

`buffer-payload.json`には送信直前のGraphQL本文を入れますが、このスクリプト自身は`createPost` mutationを実行しません。
