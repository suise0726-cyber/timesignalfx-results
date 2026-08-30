from __future__ import annotations

import argparse
import csv
import html
import json
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont


DEFAULT_FX_ROOT = Path(r"C:\keiba_scraper\fx_performance")
DEFAULT_SITE_URL = "https://timesignalfx.com/実績/"
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
    """Legacy fixture reader; production uses the canonical SQLite path."""
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
            net = sum(Decimal(row.get(field) or "0") for field in ("profit", "swap", "commission"))
            trades.append(
                Trade(
                    ticket=row["ticket"], magic=row["magic"], symbol=row["symbol"],
                    side=row["type"].upper(), close_time_jst=parse_mt4_datetime(close_time),
                    open_price=Decimal(row["open_price"]), close_price=Decimal(row["close_price"]),
                    net_jpy=net,
                )
            )
    if not complete:
        raise ValueError(f"MT4履歴CSVが未完了です（#ENDなし）: {path}")
    return trades


def summarize(trades: Iterable[Trade], target: date) -> Summary:
    all_trades = list(trades)
    month_trades = [
        trade for trade in all_trades
        if trade.close_time_jst.year == target.year
        and trade.close_time_jst.month == target.month
        and trade.close_time_jst.date() <= target
    ]
    day_trades = [trade for trade in month_trades if trade.close_time_jst.date() == target]

    def counts(items: list[Trade]) -> tuple[int, int, int]:
        return (
            sum(trade.net_jpy > 0 for trade in items),
            sum(trade.net_jpy < 0 for trade in items),
            sum(trade.net_jpy == 0 for trade in items),
        )

    wins, losses, draws = counts(day_trades)
    month_wins, month_losses, month_draws = counts(month_trades)
    return Summary(
        target_date=target.isoformat(), trades=len(day_trades), wins=wins, losses=losses, draws=draws,
        daily_pips=round(float(sum((trade.pips for trade in day_trades), Decimal("0"))), 1),
        daily_net_jpy=round(sum((trade.net_jpy for trade in day_trades), Decimal("0"))),
        monthly_trades=len(month_trades), monthly_wins=month_wins, monthly_losses=month_losses,
        monthly_draws=month_draws,
        monthly_pips=round(float(sum((trade.pips for trade in month_trades), Decimal("0"))), 1),
        monthly_net_jpy=round(sum((trade.net_jpy for trade in month_trades), Decimal("0"))),
    )


def summary_from_canonical(fx_root: Path, target: date) -> Summary:
    sys.path.insert(0, str(fx_root))
    try:
        import wordpress_daily_publication as daily_publication
    finally:
        sys.path.pop(0)
    config = json.loads((fx_root / "config.json").read_text(encoding="utf-8"))
    snapshot = daily_publication.build_snapshot(
        target, config, fx_root / "private" / "canonical" / "fx_performance.sqlite3",
    )
    day = next(row for row in snapshot["daily"] if row["period"] == target.isoformat())
    month = next(row for row in snapshot["monthly"] if row["period"] == target.strftime("%Y-%m"))
    return Summary(
        target_date=target.isoformat(), trades=int(day["trades"]), wins=int(day["wins"]),
        losses=int(day["losses"]), draws=int(day["breakeven"]), daily_pips=float(day["pips"]),
        daily_net_jpy=round(float(day["net_profit"])), monthly_trades=int(month["trades"]),
        monthly_wins=int(month["wins"]), monthly_losses=int(month["losses"]),
        monthly_draws=int(month["breakeven"]), monthly_pips=float(month["pips"]),
        monthly_net_jpy=round(float(month["net_profit"])),
    )


def signed_number(value: float | int, suffix: str = "") -> str:
    return f"{value:+,.1f}{suffix}" if isinstance(value, float) else f"{value:+,d}{suffix}"


def record_text(wins: int, losses: int, draws: int) -> str:
    result = f"{wins}勝{losses}敗"
    return result + (f"{draws}分" if draws else "")


def post_text(summary: Summary, brand: str, site_url: str = DEFAULT_SITE_URL) -> str:
    target = date.fromisoformat(summary.target_date)
    daily_record = record_text(summary.wins, summary.losses, summary.draws) if summary.trades else "確定取引なし"
    return "\n".join([
        f"【{brand} 日次実績】{target:%Y/%m/%d}",
        f"本日: {daily_record} / {signed_number(summary.daily_net_jpy, '円')} / {signed_number(summary.daily_pips, ' pips')}",
        f"{target.month}月累計: {signed_number(summary.monthly_net_jpy, '円')} / {signed_number(summary.monthly_pips, ' pips')}",
        f"実績: {site_url}",
        "#FX #USDJPY #自動売買 #EA",
    ])


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
    navy, panel, text = (13, 35, 64), (255, 255, 255), (15, 27, 45)
    muted, border = (91, 107, 128), (227, 232, 239)
    positive, negative = (11, 110, 102), (179, 39, 30)
    image = Image.new("RGB", (width, height), (244, 246, 249))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((70, 60, 1530, 840), radius=26, fill=panel, outline=border, width=2)
    draw.rectangle((70, 60, 1530, 190), fill=navy)
    draw.text((125, 104), brand, font=_font(52, True), fill=(255, 255, 255))
    draw.text((1475, 120), summary.target_date.replace("-", "."), font=_font(28), fill=(198, 211, 226), anchor="ra")
    day_record = record_text(summary.wins, summary.losses, summary.draws) if summary.trades else "確定取引なし"
    draw.text((125, 255), "日次確定実績", font=_font(27, True), fill=muted)
    draw.text((125, 318), day_record, font=_font(58, True), fill=text)
    day_color = positive if summary.daily_net_jpy >= 0 else negative
    draw.text((125, 445), signed_number(summary.daily_net_jpy, "円"), font=_font(104, True), fill=day_color)
    draw.text((130, 565), signed_number(summary.daily_pips, " pips"), font=_font(48, True), fill=text)
    draw.line((125, 650, 1475, 650), fill=border, width=2)
    draw.text((125, 695), f"{date.fromisoformat(summary.target_date).month}月累計", font=_font(25, True), fill=muted)
    month_record = record_text(summary.monthly_wins, summary.monthly_losses, summary.monthly_draws)
    draw.text((530, 755), month_record, font=_font(37, True), fill=text, anchor="mm")
    draw.text((950, 755), signed_number(summary.monthly_pips, " pips"), font=_font(37, True), fill=text, anchor="mm")
    month_color = positive if summary.monthly_net_jpy >= 0 else negative
    draw.text((1370, 755), signed_number(summary.monthly_net_jpy, "円"), font=_font(37, True), fill=month_color, anchor="mm")
    if sample:
        draw.text((800, 82), "SAMPLE / TEST DATA", font=_font(22, True), fill=(255, 138, 122), anchor="ma")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)


def expected_site_strings(summary: Summary) -> list[str]:
    target = date.fromisoformat(summary.target_date)
    return [
        summary.target_date, signed_number(summary.daily_net_jpy, "円"),
        signed_number(summary.monthly_net_jpy, "円"), signed_number(summary.monthly_pips, " pips"),
        record_text(summary.monthly_wins, summary.monthly_losses, summary.monthly_draws),
        f"{target.month}月損益",
    ]


def verify_site_match(summary: Summary, site_url: str, timeout: int = 30) -> dict[str, object]:
    try:
        request = urllib.request.Request(site_url, headers={"User-Agent": "TimeSignalFX-daily-verifier/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"matched": False, "http_status": None, "reason": type(exc).__name__, "missing": []}
    normalized = html.unescape(body).replace("&#43;", "+")
    expected = expected_site_strings(summary)
    missing = [value for value in expected if value not in normalized]
    return {
        "matched": status == 200 and not missing, "http_status": status,
        "reason": "ok" if status == 200 and not missing else "site_values_mismatch",
        "missing": missing, "expected": expected,
    }


def build_payload(text: str, image_url: str, eligible_to_post: bool, reason: str) -> dict[str, object]:
    return {
        "dry_run": True, "eligible_to_post": eligible_to_post, "gate_reason": reason,
        "endpoint": "https://api.buffer.com", "required_environment": ["BUFFER_API_KEY", "BUFFER_CHANNEL_ID"],
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
            "variables": {"text": text, "channelId": "${BUFFER_CHANNEL_ID}", "imageUrl": image_url},
        },
    }


def posted_dates(state_path: Path) -> set[str]:
    if not state_path.exists():
        return set()
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {str(item) for item in value.get("posted_dates", [])}


def parse_args() -> argparse.Namespace:
    yesterday_jst = datetime.now(ZoneInfo("Asia/Tokyo")).date() - timedelta(days=1)
    parser = argparse.ArgumentParser(description="TimeSignalFXの日次実績を投稿直前まで生成")
    parser.add_argument("--date", type=date.fromisoformat, default=yesterday_jst)
    parser.add_argument("--fx-root", type=Path, default=DEFAULT_FX_ROOT)
    parser.add_argument("--history", type=Path, help="テスト専用の旧CSV入力")
    parser.add_argument("--magic-prefix", default="2026091", help="--history指定時だけ使用")
    parser.add_argument("--symbol", default="USDJPY", help="--history指定時だけ使用")
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    parser.add_argument("--brand", default="TimeSignalFX")
    parser.add_argument("--output-root", type=Path, default=Path("build"))
    parser.add_argument("--state", type=Path, default=Path("state/posted.json"))
    parser.add_argument("--image-base-url", default=DEFAULT_IMAGE_BASE_URL)
    parser.add_argument("--skip-site-check", action="store_true", help="架空fixture試験専用")
    parser.add_argument("--sample-watermark", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    weekend = args.date.weekday() >= 5
    summary = (
        summarize(load_trades(args.history, args.magic_prefix, args.symbol), args.date)
        if args.history else summary_from_canonical(args.fx_root, args.date)
    )
    day_dir = args.output_root / args.date.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    image_path = day_dir / f"{args.date.isoformat()}.png"
    text = post_text(summary, args.brand, args.site_url)
    render_png(summary, image_path, args.brand, args.sample_watermark)
    image_url = f"{args.image_base_url.rstrip('/')}/{args.date.isoformat()}.png"
    verification = (
        {"matched": True, "http_status": None, "reason": "test_site_check_skipped", "missing": []}
        if args.skip_site_check else verify_site_match(summary, args.site_url)
    )
    duplicate = args.date.isoformat() in posted_dates(args.state)
    if weekend:
        gate_reason = "weekend_target"
    elif summary.trades == 0:
        gate_reason = "weekday_zero_trades"
    elif duplicate:
        gate_reason = "already_posted"
    elif not verification["matched"]:
        gate_reason = str(verification["reason"])
    else:
        gate_reason = "ready"
    eligible = gate_reason == "ready"
    (day_dir / "post.txt").write_text(text + "\n", encoding="utf-8")
    (day_dir / "result.json").write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (day_dir / "site-verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (day_dir / "buffer-payload.json").write_text(
        json.dumps(build_payload(text, image_url, eligible, gate_reason), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(text)
    print(f"\nPNG: {image_path.resolve()}")
    print(f"SITE MATCH: {verification['matched']} / BUFFER GATE: {gate_reason}")
    print("DRY-RUN: createPost mutationは実行していません。")
    return 0 if eligible or gate_reason in {"weekend_target", "weekday_zero_trades", "already_posted"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
