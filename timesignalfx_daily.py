from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont


DEFAULT_HISTORY = Path(
    r"C:\Users\Owner\AppData\Roaming\MetaQuotes\Terminal\Common\Files\account_history_export.csv"
)
DEFAULT_MAGIC_PREFIX = "2026091"
DEFAULT_IMAGE_BASE_URL = (
    "https://raw.githubusercontent.com/suise0726-cyber/"
    "timesignalfx-results/main/public"
)


@dataclass(frozen=True)
class Trade:
    ticket: str
    magic: str
    symbol: str
    side: str
    close_time_jst: datetime
    open_price: Decimal
    close_price: Decimal
    net_jpy: Decimal

    @property
    def pips(self) -> Decimal:
        multiplier = Decimal("1") if self.side == "BUY" else Decimal("-1")
        pip_size = Decimal("0.01") if self.symbol.upper().endswith("JPY") else Decimal("0.0001")
        return (self.close_price - self.open_price) * multiplier / pip_size


@dataclass(frozen=True)
class Summary:
    target_date: str
    trades: int
    wins: int
    losses: int
    draws: int
    daily_pips: float
    daily_net_jpy: int
    monthly_trades: int
    monthly_wins: int
    monthly_losses: int
    monthly_draws: int
    monthly_pips: float
    monthly_net_jpy: int


def parse_mt4_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y.%m.%d %H:%M:%S")


def load_trades(path: Path, magic_prefix: str, symbol: str) -> list[Trade]:
    if not path.exists():
        raise FileNotFoundError(f"MT4履歴CSVが見つかりません: {path}")

    trades: list[Trade] = []
    complete = False
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("record_type") == "#END":
                complete = True
                continue
            if row.get("record_type") != "CLOSED_ORDER":
                continue
            if not row.get("magic", "").startswith(magic_prefix):
                continue
            if row.get("symbol", "").upper() != symbol.upper():
                continue
            close_time = row.get("close_time_jst", "")
            if not close_time:
                continue
            net = sum(
                Decimal(row.get(field) or "0")
                for field in ("profit", "swap", "commission")
            )
            trades.append(
                Trade(
                    ticket=row["ticket"],
                    magic=row["magic"],
                    symbol=row["symbol"],
                    side=row["type"].upper(),
                    close_time_jst=parse_mt4_datetime(close_time),
                    open_price=Decimal(row["open_price"]),
                    close_price=Decimal(row["close_price"]),
                    net_jpy=net,
                )
            )
    if not complete:
        raise ValueError(f"MT4履歴CSVが未完了です（#ENDなし）: {path}")
    return trades


def summarize(trades: Iterable[Trade], target: date) -> Summary:
    all_trades = list(trades)
    month_trades = [
        trade
        for trade in all_trades
        if trade.close_time_jst.year == target.year
        and trade.close_time_jst.month == target.month
        and trade.close_time_jst.date() <= target
    ]
    day_trades = [trade for trade in month_trades if trade.close_time_jst.date() == target]

    def result_counts(items: list[Trade]) -> tuple[int, int, int]:
        return (
            sum(trade.net_jpy > 0 for trade in items),
            sum(trade.net_jpy < 0 for trade in items),
            sum(trade.net_jpy == 0 for trade in items),
        )

    wins, losses, draws = result_counts(day_trades)
    month_wins, month_losses, month_draws = result_counts(month_trades)
    return Summary(
        target_date=target.isoformat(),
        trades=len(day_trades),
        wins=wins,
        losses=losses,
        draws=draws,
        daily_pips=float(sum((t.pips for t in day_trades), Decimal("0"))),
        daily_net_jpy=int(sum((t.net_jpy for t in day_trades), Decimal("0"))),
        monthly_trades=len(month_trades),
        monthly_wins=month_wins,
        monthly_losses=month_losses,
        monthly_draws=month_draws,
        monthly_pips=float(sum((t.pips for t in month_trades), Decimal("0"))),
        monthly_net_jpy=int(sum((t.net_jpy for t in month_trades), Decimal("0"))),
    )


def signed_number(value: float | int, suffix: str = "") -> str:
    if isinstance(value, float):
        return f"{value:+,.1f}{suffix}"
    return f"{value:+,d}{suffix}"


def post_text(summary: Summary, brand: str) -> str:
    target = date.fromisoformat(summary.target_date)
    if summary.trades:
        daily = f"{summary.wins}勝{summary.losses}敗"
        if summary.draws:
            daily += f"{summary.draws}分"
    else:
        daily = "確定取引なし"
    month_record = f"{summary.monthly_wins}勝{summary.monthly_losses}敗"
    if summary.monthly_draws:
        month_record += f"{summary.monthly_draws}分"
    return "\n".join(
        [
            f"【{brand} 日次実績】{target:%Y/%m/%d}",
            f"本日: {daily} / {signed_number(summary.daily_pips, ' pips')}",
            f"確定損益: {signed_number(summary.daily_net_jpy, '円')}",
            f"月間累計: {month_record} / {signed_number(summary.monthly_pips, ' pips')} / {signed_number(summary.monthly_net_jpy, '円')}",
            "勝敗を含め、確定実績を毎日公開します。",
            "#FX #USDJPY #自動売買 #EA",
        ]
    )


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path(r"C:\Windows\Fonts\meiryob.ttc" if bold else r"C:\Windows\Fonts\meiryo.ttc"),
        Path(r"C:\Windows\Fonts\YuGothB.ttc" if bold else r"C:\Windows\Fonts\YuGothR.ttc"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def render_png(summary: Summary, path: Path, brand: str, sample: bool = False) -> None:
    width, height = 1600, 900
    bg = (8, 16, 33)
    panel = (18, 31, 54)
    white = (241, 245, 249)
    muted = (148, 163, 184)
    accent = (56, 189, 248)
    positive = (52, 211, 153)
    negative = (251, 113, 133)

    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((70, 60, 1530, 840), radius=36, fill=panel)
    draw.text((125, 110), brand, font=_font(58, True), fill=white)
    draw.text((125, 185), "DAILY RESULT", font=_font(28, True), fill=accent)
    draw.text((1470, 120), summary.target_date.replace("-", "."), font=_font(29), fill=muted, anchor="ra")

    record = f"{summary.wins} WIN  /  {summary.losses} LOSS"
    if summary.draws:
        record += f"  /  {summary.draws} DRAW"
    if not summary.trades:
        record = "NO CLOSED TRADES"
    draw.text((800, 345), record, font=_font(68, True), fill=white, anchor="mm")

    daily_color = positive if summary.daily_net_jpy >= 0 else negative
    draw.text((800, 460), signed_number(summary.daily_net_jpy, "円"), font=_font(104, True), fill=daily_color, anchor="mm")
    draw.text((800, 548), signed_number(summary.daily_pips, " pips"), font=_font(50, True), fill=white, anchor="mm")

    draw.line((125, 635, 1475, 635), fill=(51, 65, 85), width=2)
    draw.text((125, 685), "MONTH TO DATE", font=_font(25, True), fill=muted)
    month_record = f"{summary.monthly_wins}W - {summary.monthly_losses}L"
    if summary.monthly_draws:
        month_record += f" - {summary.monthly_draws}D"
    draw.text((500, 745), month_record, font=_font(38, True), fill=white, anchor="mm")
    draw.text((920, 745), signed_number(summary.monthly_pips, " pips"), font=_font(38, True), fill=white, anchor="mm")
    month_color = positive if summary.monthly_net_jpy >= 0 else negative
    draw.text((1375, 745), signed_number(summary.monthly_net_jpy, "円"), font=_font(38, True), fill=month_color, anchor="mm")

    if sample:
        draw.text((800, 85), "SAMPLE / TEST DATA", font=_font(24, True), fill=negative, anchor="ma")

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)


def build_payload(text: str, image_url: str) -> dict[str, object]:
    return {
        "dry_run": True,
        "endpoint": "https://api.buffer.com",
        "required_environment": ["BUFFER_API_KEY", "BUFFER_CHANNEL_ID"],
        "image_url": image_url,
        "request_body": {
            "query": """mutation CreateImagePost($text: String!, $channelId: ChannelId!, $imageUrl: String!) {
  createPost(input: {
    text: $text
    channelId: $channelId
    schedulingType: automatic
    mode: shareNow
    assets: [{ image: { url: $imageUrl } }]
  }) {
    ... on PostActionSuccess { post { id text status } }
    ... on MutationError { message }
  }
}""",
            "variables": {
                "text": text,
                "channelId": "${BUFFER_CHANNEL_ID}",
                "imageUrl": image_url,
            },
        },
    }


def parse_args() -> argparse.Namespace:
    yesterday_jst = datetime.now(ZoneInfo("Asia/Tokyo")).date() - timedelta(days=1)
    parser = argparse.ArgumentParser(description="TimeSignalFXの日次実績をdry-run生成")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--date", type=date.fromisoformat, default=yesterday_jst)
    parser.add_argument("--magic-prefix", default=DEFAULT_MAGIC_PREFIX)
    parser.add_argument("--symbol", default="USDJPY")
    parser.add_argument("--brand", default="TimeSignalFX")
    parser.add_argument("--output-root", type=Path, default=Path("build"))
    parser.add_argument("--image-base-url", default=DEFAULT_IMAGE_BASE_URL)
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--sample-watermark", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    trades = load_trades(args.history, args.magic_prefix, args.symbol)
    summary = summarize(trades, args.date)
    if summary.trades == 0 and not args.allow_empty:
        raise SystemExit(
            f"{args.date.isoformat()} (JST) の確定取引が0件です。"
            "履歴更新とマジック番号を確認してください。"
            "意図した取引なしなら --allow-empty を付けます。"
        )

    day_dir = args.output_root / args.date.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    image_path = day_dir / f"{args.date.isoformat()}.png"
    text = post_text(summary, args.brand)
    render_png(summary, image_path, args.brand, args.sample_watermark)
    image_url = f"{args.image_base_url.rstrip('/')}/{args.date.isoformat()}.png"

    (day_dir / "post.txt").write_text(text + "\n", encoding="utf-8")
    (day_dir / "result.json").write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (day_dir / "buffer-payload.json").write_text(
        json.dumps(build_payload(text, image_url), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(text)
    print(f"\nPNG: {image_path.resolve()}")
    print("DRY-RUN: GitHub公開・Buffer送信・X投稿は行っていません。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
