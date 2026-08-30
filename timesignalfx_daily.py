from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
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
DEFAULT_POST_SITE_URL = (
    "https://timesignalfx.com/%E5%AE%9F%E7%B8%BE/"
    "?utm_source=x&utm_medium=social&utm_campaign=daily_result"
)
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


def post_text(summary: Summary, brand: str, site_url: str = DEFAULT_POST_SITE_URL) -> str:
    target = date.fromisoformat(summary.target_date)
    daily_record = record_text(summary.wins, summary.losses, summary.draws) if summary.trades else "確定取引なし"
    return "\n".join([
        f"📊 {target.month}/{target.day} {brand} 確定実績",
        "",
        daily_record,
        f"{signed_number(summary.daily_net_jpy, '円')} / {signed_number(summary.daily_pips, ' pips')}",
        "",
        f"{target.month}月累計",
        f"{signed_number(summary.monthly_net_jpy, '円')} / {signed_number(summary.monthly_pips, ' pips')}",
        "",
        "全営業日の確定実績を公開中。",
        "",
        "実績👇",
        site_url,
        "",
        "#FX #USDJPY #EA",
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
        signed_number(summary.daily_pips, " pips"),
        signed_number(summary.monthly_net_jpy, "円"), signed_number(summary.monthly_pips, " pips"),
        record_text(summary.wins, summary.losses, summary.draws),
        f"{target.year}年{target.month}月{target.day}日",
    ]


def x_buffer_weighted_length(text: str) -> int:
    """BufferのX事前検証と同じく、URL短縮前のUnicode重みで安全側に数える。"""
    return sum(
        1 if ord(char) <= 0x10FF
        or 0x2000 <= ord(char) <= 0x200D
        or 0x2010 <= ord(char) <= 0x201F
        or 0x2032 <= ord(char) <= 0x2037
        else 2
        for char in text
    )


def verify_site_match(summary: Summary, site_url: str, timeout: int = 30) -> dict[str, object]:
    try:
        parts = urllib.parse.urlsplit(site_url)
        request_url = urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, urllib.parse.quote(parts.path, safe="/%"), parts.query, parts.fragment)
        )
        request = urllib.request.Request(request_url, headers={"User-Agent": "TimeSignalFX-daily-verifier/1.0"})
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


def build_payload(
    text: str, image_url: str, eligible_to_post: bool, reason: str, *, dry_run: bool = True,
) -> dict[str, object]:
    return {
        "dry_run": dry_run, "eligible_to_post": eligible_to_post, "gate_reason": reason,
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
    __typename
    ... on PostActionSuccess {
      post { id text status createdAt dueAt sentAt channelId assets { id mimeType source thumbnail } }
    }
    ... on MutationError { message }
  }
}""",
            "variables": {"text": text, "channelId": "${BUFFER_CHANNEL_ID}", "imageUrl": image_url},
        },
    }


def _read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_post_state(state_path: Path) -> dict[str, object]:
    value = _read_json(state_path, {})
    if not isinstance(value, dict):
        return {"version": 1, "not_before_date": "2026-08-28", "posts": []}
    posts = value.get("posts", [])
    if not isinstance(posts, list):
        posts = []
    return {
        "version": 1,
        "not_before_date": str(value.get("not_before_date") or "2026-08-28"),
        "posts": [item for item in posts if isinstance(item, dict)],
    }


def posted_dates(state_path: Path) -> set[str]:
    value = load_post_state(state_path)
    return {str(item.get("date")) for item in value["posts"] if item.get("date")}


def record_posted(
    state_path: Path, target: date, buffer_post_id: str, *, x_post_id: str | None = None,
    x_url: str | None = None, posted_at: str | None = None,
) -> None:
    if x_post_id is None and x_url:
        found = re.search(r"/status/(\d+)", x_url)
        x_post_id = found.group(1) if found else None
    value = load_post_state(state_path)
    target_text = target.isoformat()
    posts = [item for item in value["posts"] if str(item.get("date")) != target_text]
    posts.append({
        "date": target_text,
        "buffer_post_id": str(buffer_post_id),
        "x_post_id": x_post_id,
        "x_url": x_url,
        "posted_at": posted_at or datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds"),
    })
    posts.sort(key=lambda item: str(item["date"]))
    value["posts"] = posts
    _write_json_atomic(state_path, value)


def record_x_reference(state_path: Path, target: date, x_url: str) -> dict[str, object]:
    value = load_post_state(state_path)
    target_text = target.isoformat()
    match = next((item for item in value["posts"] if str(item.get("date")) == target_text), None)
    if match is None:
        raise ValueError(f"posted stateに{target_text}がありません")
    found = re.search(r"/status/(\d+)", x_url)
    match["x_url"] = x_url
    match["x_post_id"] = found.group(1) if found else None
    _write_json_atomic(state_path, value)
    return match


def canonical_snapshot(fx_root: Path, target: date) -> dict[str, object]:
    sys.path.insert(0, str(fx_root))
    try:
        import wordpress_daily_publication as daily_publication
    finally:
        sys.path.pop(0)
    config = json.loads((fx_root / "config.json").read_text(encoding="utf-8"))
    return daily_publication.build_snapshot(
        target, config, fx_root / "private" / "canonical" / "fx_performance.sqlite3",
    )


def latest_unposted_trade_date(fx_root: Path, state_path: Path, latest: date) -> date | None:
    state = load_post_state(state_path)
    not_before = date.fromisoformat(str(state["not_before_date"]))
    posted = posted_dates(state_path)
    snapshot = canonical_snapshot(fx_root, latest)
    candidates = [
        date.fromisoformat(str(row["period"]))
        for row in snapshot.get("daily", [])
        if isinstance(row, dict)
        and int(row.get("trades") or 0) > 0
        and not_before <= date.fromisoformat(str(row["period"])) <= latest
        and str(row["period"]) not in posted
    ]
    return max(candidates) if candidates else None


def _safe_run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, text=True, encoding="utf-8", errors="replace",
                            capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"command_failed:{command[0]}:{result.returncode}")
    return result


def publish_image_to_github(repo_root: Path, image_path: Path, target: date, image_url: str) -> dict[str, object]:
    public_path = repo_root / "public" / f"{target.isoformat()}.png"
    public_path.parent.mkdir(parents=True, exist_ok=True)
    local_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
    existing_hash = hashlib.sha256(public_path.read_bytes()).hexdigest() if public_path.exists() else None
    pushed = False
    if existing_hash != local_hash:
        shutil.copy2(image_path, public_path)
        _safe_run(["git", "add", "-f", "--", str(public_path)], repo_root)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet", "--", str(public_path)],
                                cwd=repo_root, check=False)
        if staged.returncode == 1:
            _safe_run(["git", "commit", "-m", f"Publish TimeSignalFX result {target.isoformat()}",
                       "--", str(public_path)], repo_root)
            _safe_run(["git", "push", "origin", "HEAD:main"], repo_root)
            pushed = True
        elif staged.returncode != 0:
            raise RuntimeError("git_diff_failed")
    request = urllib.request.Request(image_url, headers={"User-Agent": "TimeSignalFX-image-verifier/1.0"})
    remote = b""
    status = None
    content_type = ""
    last_error = ""
    for _ in range(6):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = int(response.status)
                content_type = str(response.headers.get("Content-Type") or "")
                remote = response.read()
            if status == 200 and content_type.startswith("image/") and hashlib.sha256(remote).hexdigest() == local_hash:
                break
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = type(exc).__name__
        time.sleep(2)
    matched = status == 200 and content_type.startswith("image/") and hashlib.sha256(remote).hexdigest() == local_hash
    if not matched:
        raise RuntimeError("github_image_verification_failed:" + (last_error or "mismatch"))
    return {"url": image_url, "http_status": status, "content_type": content_type,
            "sha256": local_hash, "pushed": pushed}


def buffer_graphql(api_key: str, query: str, variables: dict[str, object] | None = None,
                   timeout: int = 30) -> dict[str, object]:
    body = json.dumps({"query": query, "variables": variables or {}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        "https://api.buffer.com", data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "User-Agent": "TimeSignalFX-daily-publisher/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict) or value.get("errors"):
        messages = [str(item.get("message") or "") for item in (value.get("errors") or []) if isinstance(item, dict)]
        raise RuntimeError("buffer_graphql_error:" + " | ".join(messages)[:300])
    data = value.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("buffer_graphql_missing_data")
    return data


def buffer_organization_id(api_key: str) -> str:
    data = buffer_graphql(api_key, "query AccountOrganizations { account { organizations { id name } } }")
    organizations = ((data.get("account") or {}).get("organizations") or [])
    if not organizations:
        raise RuntimeError("buffer_organization_missing")
    return str(organizations[0]["id"])


def buffer_recent_posts(api_key: str, organization_id: str, channel_id: str) -> list[dict[str, object]]:
    org = json.dumps(organization_id)
    channel = json.dumps(channel_id)
    query = f"""query RecentPosts {{
  posts(first: 50, input: {{organizationId: {org}, filter: {{channelIds: [{channel}]}},
    sort: [{{field: createdAt, direction: desc}}]}}) {{
    edges {{ node {{ id text status createdAt dueAt sentAt channelId externalLink allowedActions
      assets {{ id mimeType source thumbnail }} }} }}
  }}
}}"""
    data = buffer_graphql(api_key, query)
    edges = ((data.get("posts") or {}).get("edges") or [])
    return [edge["node"] for edge in edges if isinstance(edge, dict) and isinstance(edge.get("node"), dict)]


def buffer_post(api_key: str, post_id: str) -> dict[str, object]:
    query = """query GetPost($id: PostId!) {
  post(input: {id: $id}) { id text status createdAt dueAt sentAt channelId externalLink allowedActions
    assets { id mimeType source thumbnail } }
}"""
    data = buffer_graphql(api_key, query, {"id": post_id})
    post = data.get("post")
    if not isinstance(post, dict):
        raise RuntimeError("buffer_post_missing")
    return post


def buffer_create_image_post(api_key: str, channel_id: str, text: str, image_url: str) -> dict[str, object]:
    query = """mutation CreateImagePost($text: String!, $channelId: ChannelId!, $imageUrl: String!) {
  createPost(input: {text: $text, channelId: $channelId, schedulingType: automatic,
    mode: shareNow, assets: [{image: {url: $imageUrl}}]}) {
    __typename
    ... on PostActionSuccess { post { id text status createdAt dueAt sentAt channelId externalLink allowedActions
      assets { id mimeType source thumbnail } } }
    ... on MutationError { message }
  }
}"""
    data = buffer_graphql(api_key, query, {"text": text, "channelId": channel_id, "imageUrl": image_url}, timeout=45)
    result = data.get("createPost")
    if not isinstance(result, dict) or result.get("__typename") != "PostActionSuccess":
        typename = str(result.get("__typename") or "missing") if isinstance(result, dict) else "missing"
        message = str(result.get("message") or "") if isinstance(result, dict) else ""
        raise RuntimeError(f"buffer_create_failed:{typename}:{message[:300]}")
    post = result.get("post")
    if not isinstance(post, dict) or not post.get("id"):
        raise RuntimeError("buffer_create_missing_post")
    return post


def wait_for_buffer_sent(api_key: str, post_id: str, timeout_seconds: int = 55) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        last = buffer_post(api_key, post_id)
        if str(last.get("status")) == "sent":
            return last
        if str(last.get("status")) == "error":
            raise RuntimeError("buffer_post_error")
        time.sleep(3)
    raise TimeoutError(f"buffer_post_not_sent:{post_id}")


def load_attempts(path: Path) -> dict[str, object]:
    value = _read_json(path, {"version": 1, "dates": {}})
    if not isinstance(value, dict) or not isinstance(value.get("dates"), dict):
        return {"version": 1, "dates": {}}
    return value


def record_attempt(path: Path, target: date, outcome: str, *, post_id: str | None = None) -> int:
    value = load_attempts(path)
    dates = value["dates"]
    item = dates.get(target.isoformat(), {}) if isinstance(dates.get(target.isoformat()), dict) else {}
    count = int(item.get("attempts") or 0) + (1 if outcome == "started" else 0)
    dates[target.isoformat()] = {
        "attempts": count,
        "last_outcome": outcome,
        "last_attempt_at": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds"),
        "buffer_post_id": post_id,
    }
    _write_json_atomic(path, value)
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TimeSignalFXの日次実績を生成・検証・X公開")
    parser.add_argument("--date", type=date.fromisoformat,
                        help="省略時は最新の未投稿・取引あり確定日を選ぶ")
    parser.add_argument("--fx-root", type=Path, default=DEFAULT_FX_ROOT)
    parser.add_argument("--history", type=Path, help="テスト専用の旧CSV入力")
    parser.add_argument("--magic-prefix", default="2026091", help="--history指定時だけ使用")
    parser.add_argument("--symbol", default="USDJPY", help="--history指定時だけ使用")
    parser.add_argument("--site-url", default=DEFAULT_POST_SITE_URL)
    parser.add_argument("--brand", default="TimeSignalFX")
    parser.add_argument("--output-root", type=Path, default=Path("build"))
    parser.add_argument("--state", type=Path, default=Path("state/posted_dates.json"))
    parser.add_argument("--attempt-state", type=Path, default=Path("state/attempts.json"))
    parser.add_argument("--image-base-url", default=DEFAULT_IMAGE_BASE_URL)
    parser.add_argument("--skip-site-check", action="store_true", help="架空fixture試験専用")
    parser.add_argument("--sample-watermark", action="store_true")
    parser.add_argument("--publish", action="store_true", help="ゲート通過時にGitHub公開とBuffer投稿を実行")
    parser.add_argument("--check-buffer", action="store_true", help="Buffer認証・対象チャンネルを読み取り確認")
    parser.add_argument("--confirm-x-url", help="公開確認済みX URLを投稿済みstateへ追記")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    if args.check_buffer:
        api_key = os.environ.get("BUFFER_API_KEY", "")
        channel_id = os.environ.get("BUFFER_CHANNEL_ID", "")
        if not api_key or not channel_id:
            print(json.dumps({"status": "failed", "reason": "buffer_credentials_missing"}, ensure_ascii=False))
            return 2
        organization_id = buffer_organization_id(api_key)
        recent = buffer_recent_posts(api_key, organization_id, channel_id)
        print(json.dumps({"status": "ok", "organization_present": bool(organization_id),
                          "channel_present": bool(channel_id), "recent_posts": len(recent),
                          "posts": [{"id": post.get("id"), "status": post.get("status"),
                                     "external_link": post.get("externalLink"),
                                     "allowed_actions": post.get("allowedActions")}
                                    for post in recent[:5]]}, ensure_ascii=False))
        return 0
    if args.confirm_x_url:
        if args.date is None:
            raise SystemExit("--confirm-x-urlには--dateが必要です")
        item = record_x_reference(args.state, args.date, args.confirm_x_url)
        print(json.dumps({"status": "x_reference_saved", "date": item["date"],
                          "buffer_post_id": item["buffer_post_id"], "x_post_id": item["x_post_id"],
                          "x_url": item["x_url"]}, ensure_ascii=False))
        return 0

    latest_confirmed = datetime.now(ZoneInfo("Asia/Tokyo")).date() - timedelta(days=1)
    target = args.date or latest_unposted_trade_date(args.fx_root, args.state, latest_confirmed)
    if target is None:
        print(json.dumps({"status": "skipped", "reason": "no_unposted_trade_day",
                          "latest_confirmed_date": latest_confirmed.isoformat()}, ensure_ascii=False))
        return 0
    weekend = target.weekday() >= 5
    summary = (
        summarize(load_trades(args.history, args.magic_prefix, args.symbol), target)
        if args.history else summary_from_canonical(args.fx_root, target)
    )
    day_dir = args.output_root / target.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    image_path = day_dir / f"{target.isoformat()}.png"
    text = post_text(summary, args.brand, args.site_url)
    render_png(summary, image_path, args.brand, args.sample_watermark)
    image_url = f"{args.image_base_url.rstrip('/')}/{target.isoformat()}.png"
    verification = (
        {"matched": True, "http_status": None, "reason": "test_site_check_skipped", "missing": []}
        if args.skip_site_check else verify_site_match(summary, args.site_url)
    )
    duplicate = target.isoformat() in posted_dates(args.state)
    if weekend:
        gate_reason = "weekend_target"
    elif summary.trades == 0:
        gate_reason = "weekday_zero_trades"
    elif duplicate:
        gate_reason = "already_posted"
    elif not verification["matched"]:
        gate_reason = str(verification["reason"])
    elif x_buffer_weighted_length(text) > 280:
        gate_reason = "x_text_too_long"
    else:
        gate_reason = "ready"
    eligible = gate_reason == "ready"
    (day_dir / "post.txt").write_text(text + "\n", encoding="utf-8")
    (day_dir / "result.json").write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (day_dir / "site-verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (day_dir / "buffer-payload.json").write_text(
        json.dumps(build_payload(text, image_url, eligible, gate_reason, dry_run=not args.publish),
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(text)
    print(f"\nPNG: {image_path.resolve()}")
    print(f"SITE MATCH: {verification['matched']} / BUFFER GATE: {gate_reason}")
    if not args.publish:
        print("DRY-RUN: createPost mutationは実行していません。")
        return 0 if eligible or gate_reason in {"weekend_target", "weekday_zero_trades", "already_posted"} else 2
    if not eligible:
        result = {"status": "skipped", "date": target.isoformat(), "reason": gate_reason,
                  "site_matched": bool(verification["matched"])}
        _write_json_atomic(day_dir / "publish-result.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if gate_reason in {"weekend_target", "weekday_zero_trades", "already_posted"} else 2

    api_key = os.environ.get("BUFFER_API_KEY", "")
    channel_id = os.environ.get("BUFFER_CHANNEL_ID", "")
    if not api_key or not channel_id:
        result = {"status": "failed", "date": target.isoformat(), "reason": "buffer_credentials_missing"}
        _write_json_atomic(day_dir / "publish-result.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 2

    repo_root = Path(__file__).resolve().parent
    try:
        image_check = publish_image_to_github(repo_root, image_path, target, image_url)
        organization_id = buffer_organization_id(api_key)
        recent = buffer_recent_posts(api_key, organization_id, channel_id)
        existing = next((post for post in recent if str(post.get("text")) == text), None)
        if existing:
            post_id = str(existing["id"])
            post = buffer_post(api_key, post_id)
            if str(post.get("status")) == "sent" and post.get("assets"):
                x_url = str(post.get("externalLink") or "") or None
                record_posted(args.state, target, post_id, x_url=x_url,
                              posted_at=str(post.get("sentAt") or post.get("createdAt")))
                result = {"status": "already_exists_recorded", "date": target.isoformat(),
                          "buffer_post_id": post_id, "buffer_status": post.get("status"),
                          "x_url": x_url, "image_attached": bool(post.get("assets")), "image": image_check}
                _write_json_atomic(day_dir / "publish-result.json", result)
                print(json.dumps(result, ensure_ascii=False))
                return 0
            result = {"status": "pending", "date": target.isoformat(), "reason": "matching_buffer_post_not_sent",
                      "buffer_post_id": post_id, "buffer_status": post.get("status")}
            _write_json_atomic(day_dir / "publish-result.json", result)
            print(json.dumps(result, ensure_ascii=False))
            return 2

        attempt_info = load_attempts(args.attempt_state)
        attempts = int(((attempt_info.get("dates") or {}).get(target.isoformat()) or {}).get("attempts") or 0)
        if attempts >= 3:
            result = {"status": "failed", "date": target.isoformat(), "reason": "max_attempts_reached",
                      "attempts": attempts}
            _write_json_atomic(day_dir / "publish-result.json", result)
            print(json.dumps(result, ensure_ascii=False))
            return 2
        attempt_number = record_attempt(args.attempt_state, target, "started")
        created = buffer_create_image_post(api_key, channel_id, text, image_url)
        post_id = str(created["id"])
        record_attempt(args.attempt_state, target, "created", post_id=post_id)
        sent = wait_for_buffer_sent(api_key, post_id)
        if str(sent.get("text")) != text or not sent.get("assets"):
            raise RuntimeError("buffer_sent_content_mismatch")
        x_url = str(sent.get("externalLink") or "") or None
        record_posted(args.state, target, post_id, x_url=x_url,
                      posted_at=str(sent.get("sentAt") or sent.get("createdAt")))
        record_attempt(args.attempt_state, target, "sent", post_id=post_id)
        result = {"status": "published", "date": target.isoformat(), "attempt": attempt_number,
                  "buffer_post_id": post_id, "buffer_status": sent.get("status"),
                  "x_url": x_url,
                  "text_matched": str(sent.get("text")) == text,
                  "image_attached": bool(sent.get("assets")), "image": image_check}
        _write_json_atomic(day_dir / "publish-result.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (urllib.error.URLError, TimeoutError) as exc:
        record_attempt(args.attempt_state, target, "uncertain")
        result = {"status": "uncertain", "date": target.isoformat(), "reason": type(exc).__name__,
                  "action": "no_immediate_retry"}
    except Exception as exc:  # noqa: BLE001
        record_attempt(args.attempt_state, target, "failed")
        result = {"status": "failed", "date": target.isoformat(), "reason": str(exc)[:500]}
    _write_json_atomic(day_dir / "publish-result.json", result)
    print(json.dumps(result, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
