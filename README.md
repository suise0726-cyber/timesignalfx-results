# TimeSignalFX 日次X投稿パイプライン

TimeSignalFXの非公開正本から、JST日次の勝敗・pips・確定円損益・月間累計をPythonで確定し、X投稿文と1600×900 PNGを生成・公開します。

## データ経路

- MT4読み取り専用Exporter → 非公開SQLite正本
- サイトの前日確定公開（毎日06:30 JST）
- X処理起動（毎日06:45 JST）
- 最新の未投稿・取引あり確定日を選択
- サイトの日付・日次円・日次pips・月間円・月間pipsと一致確認
- PNG生成 → GitHub `public/YYYY-MM-DD.png` → HTTP 200とSHA-256一致確認
- Buffer GraphQL API → X公開 → 公開URLを投稿済みstateへ保存

サイトと数字が一致しない場合、画像公開に失敗した場合、またはBuffer/Xの結果が不明な場合は投稿しません。0トレード日と土日は投稿対象外です。

## 二重投稿防止

- `state/posted_dates.json` は日付・Buffer post ID・X post ID/URL・投稿日時だけを保持します。
- 同じ日付は再投稿しません。
- Buffer上の既存投稿も送信前に照合します。
- 結果不明時は即時再送せず、最大3回（初回＋再試行2回）で停止します。
- 月間損益はstateから計算せず、毎回正本から再集計します。

`state/`、`build/`、認証情報、正本、MT4履歴はGitHubへ含めません。APIキーはWindows DPAPIで暗号化し、実行時だけプロセス環境へ展開します。

## 実行

dry-run:

```powershell
python timesignalfx_daily.py --date 2026-08-28
```

自動投稿用ランナー:

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\run_daily_x.ps1
```

通常運用はWindowsタスク `FX_Performance_X_Daily` が毎日06:45 JSTにランナーを起動します。

## 公開範囲

X本文とPNGに含めるのは日付、勝敗、円損益、pips、月間累計だけです。内部Signal、magic、ticket、取引時刻、TP/SL/TO、family、口座番号は公開しません。
