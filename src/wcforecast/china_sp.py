"""Manual China Sports Lottery SP review helpers.

China Sports Lottery SP values are user-entered review data only. They are
applied after the project's market-independent calibrated model probabilities
and are never used as model inputs.
"""
from __future__ import annotations

import csv
import html
import sys
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np

from . import predict
from .teams import INDEX

CSV_FIELDS = ("date", "home", "away", "neutral", "sp_home", "sp_draw", "sp_away", "actual")
OPTIONAL_FIELDS = ("stage",)
OUTCOMES = ("home", "draw", "away")
OUTCOME_LABELS = {"home": "主胜", "draw": "平局", "away": "客胜"}
BET_LABELS = {"home": "主胜下注", "draw": "平局下注", "away": "客胜下注"}
OUTCOME_MARKERS = {"home": "主", "draw": "平", "away": "客"}
ACTUAL_TO_OUTCOME = {"H": "home", "D": "draw", "A": "away"}
ACTUAL_LABELS = {"H": "主胜", "D": "平局", "A": "客胜", None: "待开奖"}
DEFAULT_STAGE = "未标注赛段"
TBD_NAMES = {"", "TBD", "待定", "待定球队", "未定", "To be decided"}
REPO_URL = "https://github.com/greenhand011/worldcup-forecast-china-sp"
TEAM_ZH = {
    "Algeria": "阿尔及利亚",
    "Argentina": "阿根廷",
    "Australia": "澳大利亚",
    "Austria": "奥地利",
    "Belgium": "比利时",
    "Bosnia and Herzegovina": "波黑",
    "Brazil": "巴西",
    "Canada": "加拿大",
    "Cape Verde": "佛得角",
    "Colombia": "哥伦比亚",
    "Croatia": "克罗地亚",
    "Curaçao": "库拉索",
    "Czech Republic": "捷克",
    "DR Congo": "民主刚果",
    "Ecuador": "厄瓜多尔",
    "Egypt": "埃及",
    "England": "英格兰",
    "France": "法国",
    "Germany": "德国",
    "Ghana": "加纳",
    "Haiti": "海地",
    "Iran": "伊朗",
    "Iraq": "伊拉克",
    "Ivory Coast": "科特迪瓦",
    "Japan": "日本",
    "Jordan": "约旦",
    "Mexico": "墨西哥",
    "Morocco": "摩洛哥",
    "Netherlands": "荷兰",
    "New Zealand": "新西兰",
    "Norway": "挪威",
    "Panama": "巴拿马",
    "Paraguay": "巴拉圭",
    "Portugal": "葡萄牙",
    "Qatar": "卡塔尔",
    "Saudi Arabia": "沙特",
    "Scotland": "苏格兰",
    "Senegal": "塞内加尔",
    "South Africa": "南非",
    "South Korea": "韩国",
    "Spain": "西班牙",
    "Sweden": "瑞典",
    "Switzerland": "瑞士",
    "Tunisia": "突尼斯",
    "Turkey": "土耳其",
    "United States": "美国",
    "Uruguay": "乌拉圭",
    "Uzbekistan": "乌兹别克斯坦",
    "TBD": "待定",
}


def read_china_sp_csv(path: str | Path) -> list[dict]:
    """Read the manual China Sports Lottery SP CSV into typed match rows.

    Lines starting with ``#`` are ignored so the committed template can explain
    that SP values must be manually replaced with real China Sports Lottery data.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"China SP review CSV not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        lines = [line for line in f if line.strip() and not line.lstrip().startswith("#")]

    if not lines:
        raise ValueError(f"China SP CSV is empty after comments are ignored: {path}")

    reader = csv.DictReader(lines)
    missing = [field for field in CSV_FIELDS if field not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"China SP CSV is missing required field(s): {', '.join(missing)}")

    rows = []
    for line_no, raw in enumerate(reader, start=2):
        if not any((value or "").strip() for value in raw.values()):
            continue
        rows.append(_parse_row(raw, line_no))
    return rows


def allocate_bankroll(
    probabilities: Mapping[str, float] | Sequence[float],
    bankroll: int = 10000,
    unit: int = 100,
) -> dict[str, int]:
    """Allocate ``bankroll`` across H/D/A by probability, rounded to ``unit``.

    Kept for the README-style probability split demonstration and unit tests.
    The China SP page uses :func:`allocate_best_edge` by default.
    """
    bankroll = int(bankroll)
    unit = int(unit)
    if bankroll <= 0:
        raise ValueError("bankroll must be positive")
    if unit <= 0:
        raise ValueError("unit must be positive")

    probs = _probability_vector(probabilities)
    total = float(sum(probs))
    if total <= 0:
        raise ValueError("probabilities must sum to a positive value")
    probs = [p / total for p in probs]

    amounts = [_round_to_unit(bankroll * p, unit) for p in probs]
    max_i = max(range(len(probs)), key=lambda i: probs[i])
    amounts[max_i] += bankroll - sum(amounts)
    return dict(zip(OUTCOMES, amounts))


def allocate_best_edge(
    edge: Mapping[str, float | None],
    bankroll: int = 100,
    unit: int = 100,
    min_edge: float = 0.0,
) -> tuple[dict[str, int], str | None, str]:
    """Put one flat review stake on the largest positive edge.

    This is a review-only stake selection layer. SP values still do not enter the
    model; they are used after prediction to decide whether a single 100-yuan
    review stake has positive expected value for that manually entered SP snapshot.
    """
    bankroll = int(bankroll)
    unit = int(unit)
    if bankroll <= 0:
        raise ValueError("bankroll must be positive")
    if unit <= 0:
        raise ValueError("unit must be positive")
    if bankroll < unit or bankroll % unit != 0:
        raise ValueError("bankroll must be a positive multiple of unit")

    if any(edge[outcome] is None for outcome in OUTCOMES):
        return _zero_allocation(), None, "待录入SP"

    best = max(OUTCOMES, key=lambda outcome: float(edge[outcome]))
    if float(edge[best]) <= float(min_edge):
        return _zero_allocation(), None, "edge未过阈值，观望"
    return {outcome: bankroll if outcome == best else 0 for outcome in OUTCOMES}, best, "模拟买入"


def review_match(
    row: Mapping[str, object],
    model,
    bankroll: int = 100,
    unit: int = 100,
    min_edge: float = 0.0,
    calibrator: Callable[[Sequence[float]], Sequence[float]] = predict.calibrate,
) -> dict:
    """Review one manually-entered SP row against calibrated model probabilities."""
    home = str(row["home"]).strip()
    away = str(row["away"]).strip()
    unresolved = _is_tbd(home) or _is_tbd(away)
    if not unresolved and (home not in INDEX or away not in INDEX):
        raise ValueError(f"unknown team(s): {home} vs {away}")

    neutral = bool(row["neutral"])
    sp = {
        "home": row.get("sp_home"),
        "draw": row.get("sp_draw"),
        "away": row.get("sp_away"),
    }
    sp_entered_count = sum(value is not None for value in sp.values())
    has_any_sp = sp_entered_count > 0
    has_complete_sp = sp_entered_count == len(OUTCOMES)

    if unresolved:
        probabilities = None
        fair_odds = None
        edge = {outcome: None for outcome in OUTCOMES}
        allocation = _zero_allocation()
        selected_outcome = None
        stake_status = "对阵待定"
    else:
        raw = model.match_probs(home, away, home_advantage=0.0 if neutral else 1.0)
        calibrated = np.asarray(calibrator(raw), dtype=float)
        if calibrated.shape != (3,):
            raise ValueError("calibrator must return a length-3 probability vector")
        probabilities = dict(zip(OUTCOMES, (float(x) for x in calibrated)))
        fair_odds = {outcome: 1.0 / probabilities[outcome] for outcome in OUTCOMES}
        edge = {
            outcome: None if sp[outcome] is None else probabilities[outcome] * float(sp[outcome]) - 1.0
            for outcome in OUTCOMES
        }
        allocation, selected_outcome, stake_status = allocate_best_edge(
            edge, bankroll=bankroll, unit=unit, min_edge=min_edge
        )

    stake_total = sum(allocation.values())
    actual = row.get("actual")
    actual = None if actual in ("", None) else str(actual).strip().upper()
    if actual and actual not in ACTUAL_TO_OUTCOME:
        raise ValueError(f"actual must be H/D/A or blank, got: {actual}")

    if actual:
        actual_outcome = ACTUAL_TO_OUTCOME[actual]
        actual_sp = sp[actual_outcome]
        pnl = (allocation[actual_outcome] * float(actual_sp) - stake_total) if stake_total else 0.0
        status = "settled"
    else:
        actual_outcome = None
        pnl = None
        status = "pending"

    return {
        "date": str(row["date"]).strip(),
        "stage": str(row.get("stage") or DEFAULT_STAGE).strip() or DEFAULT_STAGE,
        "home": home or "TBD",
        "away": away or "TBD",
        "home_zh": _team_zh(home or "TBD"),
        "away_zh": _team_zh(away or "TBD"),
        "neutral": neutral,
        "unresolved": unresolved,
        "probabilities": probabilities,
        "sp": sp,
        "sp_entered_count": sp_entered_count,
        "has_any_sp": has_any_sp,
        "has_complete_sp": has_complete_sp,
        "fair_odds": fair_odds,
        "edge": edge,
        "allocation": allocation,
        "selected_outcome": selected_outcome,
        "stake_total": stake_total,
        "stake_status": stake_status,
        "actual": actual,
        "actual_outcome": actual_outcome,
        "actual_label": ACTUAL_LABELS[actual],
        "pnl": pnl,
        "status": status,
        "bankroll": int(bankroll),
        "unit": int(unit),
        "min_edge": float(min_edge),
    }


def build_review(
    input_path: str | Path,
    model,
    bankroll: int = 100,
    unit: int = 100,
    min_edge: float = 0.0,
    today: str | date | None = None,
    calibrator: Callable[[Sequence[float]], Sequence[float]] = predict.calibrate,
) -> dict:
    """Build a structured China SP review for CLI and HTML rendering."""
    review_date = _parse_review_date(today)
    rows = read_china_sp_csv(input_path)
    matches = [
        review_match(
            row,
            model,
            bankroll=bankroll,
            unit=unit,
            min_edge=min_edge,
            calibrator=calibrator,
        )
        for row in rows
    ]
    settled = [m for m in matches if m["status"] == "settled"]
    raw_pending = [m for m in matches if m["status"] == "pending"]
    awaiting_result = [m for m in raw_pending if _should_await_result(m, review_date)]
    display_pending = [
        m for m in raw_pending
        if _should_show_pending_card(m) and not _should_await_result(m, review_date)
    ]
    template_pending = [
        m for m in raw_pending
        if not _should_show_pending_card(m) and not _should_await_result(m, review_date)
    ]
    staked_settled = [m for m in settled if m["stake_total"] > 0]
    profitable = [m for m in staked_settled if float(m["pnl"]) > 0]
    cumulative_pnl = sum(float(m["pnl"]) for m in settled)
    hit_rate = (len(profitable) / len(staked_settled)) if staked_settled else None
    for match in matches:
        match["needs_result"] = _should_await_result(match, review_date)
    return {
        "input": str(input_path),
        "bankroll": int(bankroll),
        "unit": int(unit),
        "min_edge": float(min_edge),
        "matches": matches,
        "display_pending": display_pending,
        "awaiting_result": awaiting_result,
        "template_pending": template_pending,
        "settled": settled,
        "summary": {
            "cumulative_pnl": cumulative_pnl,
            "settled_count": len(settled),
            "pending_count": len(display_pending),
            "awaiting_result_count": len(awaiting_result),
            "raw_pending_count": len(raw_pending),
            "template_count": len(template_pending),
            "match_count": len(matches),
            "staked_count": len([m for m in matches if m["stake_total"] > 0]),
            "staked_settled_count": len(staked_settled),
            "profitable_count": len(profitable),
            "hit_rate": hit_rate,
            "stage_count": _stage_count(matches),
        },
    }


def format_console_table(review: Mapping[str, object]) -> str:
    """Return a concise Chinese console table for ``wcforecast china-sp-review``."""
    headers = [
        "赛段",
        "对阵",
        "模型胜/平/负%",
        "体彩SP胜/平/负",
        "分配胜/平/负",
        "实际",
        "盈亏",
        "策略",
    ]
    rows = []
    for match in _main_display_matches(review):
        rows.append([
            match["stage"],
            _match_title(match),
            _fmt_pct_triplet(match["probabilities"]),
            _fmt_sp_triplet(match["sp"]),
            _fmt_alloc_triplet(match["allocation"]),
            match["actual_label"],
            "待开奖" if match["pnl"] is None else _fmt_console_currency(match["pnl"]),
            match["stake_status"] if match["selected_outcome"] is None
            else f"{OUTCOME_LABELS[match['selected_outcome']]} {_edge_hint(match['edge'])}",
        ])

    widths = [
        max(_display_width(str(row[i])) for row in ([headers] + rows))
        for i in range(len(headers))
    ]
    line = "  ".join(_pad(headers[i], widths[i]) for i in range(len(headers)))
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = ["  ".join(_pad(str(row[i]), widths[i]) for i in range(len(headers))) for row in rows]
    summary = review["summary"]
    footer = (
        f"合计：共 {summary['match_count']} 场，已结算 {summary['settled_count']} 场，"
        f"未来预测 {summary['pending_count']} 场，待赛果复核 {summary.get('awaiting_result_count', 0)} 场，"
        f"对阵待定模板 {summary.get('template_count', 0)} 场，"
        f"已模拟买入 {summary['staked_count']} 场，累计收益 {_fmt_console_currency(summary['cumulative_pnl'])}"
    )
    return _console_safe("\n".join(["中国体彩 SP 世界杯复盘", "", line, sep, *body, "", footer]))


def render_html(review: Mapping[str, object]) -> str:
    """Render the review as a self-contained static HTML document."""
    matches = list(review["matches"])
    pending = list(review.get("display_pending") or [m for m in matches if m["status"] == "pending" and _should_show_pending_card(m)])
    awaiting_result = list(review.get("awaiting_result") or [])
    template_pending = list(review.get("template_pending") or [m for m in matches if m["status"] == "pending" and not _should_show_pending_card(m)])
    settled = list(review.get("settled") or [m for m in matches if m["status"] == "settled"])
    summary = review["summary"]
    hit_rate = summary.get("hit_rate")
    hit_rate_card = "" if hit_rate is None else f"""
      <div class="summary-item">
        <span>已买入命中率</span>
        <strong>{_fmt_pct(float(hit_rate))}</strong>
      </div>"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>中国体彩 SP 世界杯预测</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f8fb;
      --card: #ffffff;
      --text: #1f2937;
      --muted: #6b7280;
      --line: #d9e0e8;
      --soft-line: #edf1f5;
      --blue: #2563eb;
      --blue-soft: #eff6ff;
      --green: #159957;
      --green-soft: #eaf8f0;
      --red: #dc2626;
      --red-soft: #fef2f2;
      --amber: #b45309;
      --amber-soft: #fff7ed;
      --shadow: 0 10px 30px rgba(31, 41, 55, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      letter-spacing: 0;
    }}
    main {{
      width: min(980px, calc(100% - 28px));
      margin: 0 auto;
      padding: 22px 0 44px;
    }}
    .topbar {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 16px;
      align-items: center;
      padding: 14px 0 16px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: clamp(24px, 3.4vw, 34px);
      line-height: 1.16;
      letter-spacing: 0;
    }}
    .subtitle {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.6;
    }}
    .github-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 40px;
      padding: 0 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--text);
      background: var(--card);
      text-decoration: none;
      font-weight: 700;
      box-shadow: 0 2px 10px rgba(31, 41, 55, 0.05);
      white-space: nowrap;
    }}
    .summary {{
      display: grid;
      grid-template-columns: minmax(0, 1.35fr) repeat(5, minmax(0, 1fr));
      gap: 10px;
      margin: 8px 0 24px;
    }}
    .summary-item {{
      min-height: 76px;
      padding: 13px 15px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--card);
      box-shadow: var(--shadow);
    }}
    .summary-item span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 7px;
    }}
    .summary-item strong {{
      display: block;
      font-size: 24px;
      line-height: 1.1;
    }}
    .summary-item.profit strong {{
      color: var(--green);
      font-size: 30px;
    }}
    .summary-item.profit.negative strong {{ color: var(--red); }}
    .diagnosis {{
      margin: 0 0 18px;
      padding: 12px 14px;
      border: 1px solid #fed7aa;
      border-radius: 8px;
      background: var(--amber-soft);
      color: #7c2d12;
      line-height: 1.7;
      font-size: 13px;
    }}
    .section-heading {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
      margin: 24px 0 12px;
    }}
    .section-heading h2 {{
      margin: 0;
      font-size: 20px;
      letter-spacing: 0;
    }}
    .section-heading span {{
      color: var(--muted);
      font-size: 13px;
    }}
    .card-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    .match-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--card);
      box-shadow: var(--shadow);
      padding: 14px;
    }}
    .match-head {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: start;
      margin-bottom: 12px;
    }}
    .date {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 5px;
    }}
    .stage {{
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 0 7px;
      margin-left: 6px;
      border-radius: 999px;
      color: var(--amber);
      background: var(--amber-soft);
      border: 1px solid #fed7aa;
      font-size: 11px;
      font-weight: 800;
      vertical-align: middle;
    }}
    .match-title {{
      margin: 0;
      font-size: 18px;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }}
    .match-title span {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 500;
      margin: 0 6px;
    }}
    .status {{
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 0 9px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }}
    .status.pending, .status.staked {{
      color: var(--blue);
      background: var(--blue-soft);
      border: 1px solid #c7ddff;
    }}
    .status.waiting {{
      color: var(--amber);
      background: var(--amber-soft);
      border: 1px solid #fed7aa;
    }}
    .status.positive {{
      color: var(--green);
      background: var(--green-soft);
      border: 1px solid #bfe8ce;
    }}
    .status.negative {{
      color: var(--red);
      background: var(--red-soft);
      border: 1px solid #fecaca;
    }}
    .bet-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }}
    .bet-column {{
      min-width: 0;
      padding: 10px 9px;
      border: 1px solid var(--soft-line);
      border-radius: 8px;
      background: #fbfcfe;
    }}
    .bet-column.selected {{
      background: var(--blue-soft);
      border-color: #bfdbfe;
    }}
    .bet-column.hit {{
      background: var(--green-soft);
      border-color: #bfe8ce;
    }}
    .bet-label {{
      display: flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.2;
      margin-bottom: 6px;
    }}
    .bet-team {{
      color: var(--text);
      font-size: 13px;
      font-weight: 800;
      line-height: 1.2;
      min-height: 16px;
      margin-bottom: 6px;
      overflow-wrap: anywhere;
    }}
    .marker {{
      display: inline-grid;
      place-items: center;
      flex: 0 0 22px;
      width: 22px;
      height: 22px;
      border-radius: 999px;
      color: #ffffff;
      background: var(--blue);
      font-size: 12px;
      font-weight: 900;
    }}
    .amount {{
      font-size: 18px;
      line-height: 1.1;
      font-weight: 850;
      white-space: nowrap;
    }}
    .sp {{
      margin-top: 5px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }}
    .model-details {{
      margin-top: 12px;
      border-top: 1px solid var(--soft-line);
      padding-top: 10px;
    }}
    .model-details summary {{
      cursor: pointer;
      color: var(--blue);
      font-size: 13px;
      font-weight: 800;
      list-style-position: inside;
    }}
    .detail-grid {{
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }}
    .detail-row {{
      display: grid;
      grid-template-columns: 78px repeat(3, minmax(0, 1fr));
      gap: 6px;
      align-items: center;
      font-size: 12px;
    }}
    .detail-label {{
      color: var(--muted);
      font-weight: 700;
    }}
    .detail-value {{
      padding: 6px;
      border-radius: 6px;
      background: #f8fafc;
      text-align: right;
      white-space: nowrap;
    }}
    .detail-value b {{
      display: block;
      color: var(--muted);
      font-size: 10px;
      font-weight: 700;
      text-align: left;
      margin-bottom: 2px;
    }}
    .positive {{ color: var(--green); }}
    .negative {{ color: var(--red); }}
    .empty {{
      padding: 20px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.65);
    }}
    .template-box {{
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.75);
      padding: 12px 14px;
    }}
    .template-box summary {{
      cursor: pointer;
      color: var(--blue);
      font-size: 13px;
      font-weight: 800;
    }}
    .template-list {{
      margin-top: 10px;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }}
    .template-row {{
      padding: 9px 10px;
      border: 1px solid var(--soft-line);
      border-radius: 8px;
      background: #ffffff;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}
    .template-row strong {{
      display: block;
      color: var(--text);
      font-size: 13px;
      margin-top: 2px;
      overflow-wrap: anywhere;
    }}
    .disclaimer {{
      margin-top: 28px;
      padding-top: 16px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
      line-height: 1.7;
    }}
    @media (max-width: 860px) {{
      .summary {{ grid-template-columns: 1fr 1fr; }}
      .summary-item.profit {{ grid-column: 1 / -1; }}
    }}
    @media (max-width: 760px) {{
      main {{ width: min(100% - 20px, 980px); }}
      .topbar {{ grid-template-columns: 1fr; }}
      .github-link {{ justify-self: start; }}
      .card-grid {{ grid-template-columns: 1fr; }}
      .template-list {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 460px) {{
      .summary {{ grid-template-columns: 1fr; }}
      .bet-grid {{ grid-template-columns: 1fr; }}
      .detail-row {{ grid-template-columns: 1fr; }}
      .detail-value {{ text-align: left; }}
    }}
  </style>
</head>
<body>
  <main>
    <header class="topbar">
      <div>
        <h1>中国体彩 SP 世界杯预测</h1>
        <p class="subtitle">基于模型概率 + 用户手动录入中国体彩胜平负 SP 的复盘页面；每场最多模拟 100 元，不构成投注建议</p>
      </div>
      <a class="github-link" href="{REPO_URL}">GitHub</a>
    </header>

    <section class="summary" aria-label="复盘汇总">
      <div class="summary-item profit {_negative_class(summary['cumulative_pnl'])}">
        <span>累计收益</span>
        <strong>{_fmt_signed_currency(summary['cumulative_pnl'])}</strong>
      </div>
      <div class="summary-item"><span>总赛程</span><strong>{summary['match_count']}</strong></div>
      <div class="summary-item"><span>已结算</span><strong>{summary['settled_count']}</strong></div>
      <div class="summary-item"><span>未来预测</span><strong>{summary['pending_count']}</strong></div>
      <div class="summary-item"><span>待赛果复核</span><strong>{summary.get('awaiting_result_count', 0)}</strong></div>
      <div class="summary-item"><span>对阵待定模板</span><strong>{summary.get('template_count', 0)}</strong></div>
      <div class="summary-item"><span>已模拟买入</span><strong>{summary['staked_count']}</strong></div>{hit_rate_card}
    </section>

    <div class="diagnosis">
      亏损诊断：旧页面亏损主要来自 demo SP/赛果不是官方中国体彩数据，以及“没有正 edge 也硬买”的策略偏差。
      当前版本只在用户录入完整 SP 且最大 edge 高于阈值时模拟买入 100 元；当前阈值为 {_fmt_pct(float(review.get("min_edge", 0.0)))}。
      否则标记为待录入或观望，避免为了少量样本调参而过拟合。
    </div>

    <section>
      <div class="section-heading">
        <h2>未来预测 {len(pending)}</h2>
        <span>未来比赛；中国体彩胜平负 SP 已录入或已公开导入</span>
      </div>
      {_render_card_grid(pending, empty_text="暂无已录入 SP 的待开奖比赛。")}
    </section>

    <section>
      <div class="section-heading">
        <h2>待赛果复核 {len(awaiting_result)}</h2>
        <span>已到比赛日但 actual 尚未填写；补 H/D/A 后进入历史复盘并结算盈亏</span>
      </div>
      {_render_card_grid(awaiting_result, empty_text="暂无待赛果复核比赛。")}
    </section>

    <section>
      <div class="section-heading">
        <h2>历史复盘 {len(settled)}</h2>
        <span>actual = H / D / A 的比赛</span>
      </div>
      {_render_card_grid(settled, empty_text="暂无已结算比赛。")}
    </section>

    <section>
      <div class="section-heading">
        <h2>待录入赛程模板 {len(template_pending)}</h2>
        <span>未抓到或未录入 SP 的赛程统一折叠；公开 SP 行进入预测区</span>
      </div>
      {_render_template_section(template_pending)}
    </section>

    <footer class="disclaimer">
      本页面仅用于模型复盘和学习，不构成投注建议。中国体彩 SP 可由用户手动录入，也可从公开展示页导入后复核；空白 SP 表示待录入。
      公开导入数据不是自动投注接口，也不是模型输入。SP 和市场赔率只用于预测后的比较与复盘，不作为模型输入。
      模型概率不是保证，历史盈亏不能证明长期存在 edge。
    </footer>
  </main>
</body>
</html>
"""


def write_html(review: Mapping[str, object], output_path: str | Path) -> Path:
    """Write the static HTML review page and return its path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html_text = "\n".join(line.rstrip() for line in render_html(review).splitlines()) + "\n"
    output_path.write_text(html_text, encoding="utf-8")
    return output_path


def _parse_row(raw: Mapping[str, str], line_no: int) -> dict:
    date = (raw.get("date") or "").strip()
    home = (raw.get("home") or "").strip()
    away = (raw.get("away") or "").strip()
    if not date:
        raise ValueError(f"line {line_no}: date is required")
    return {
        "date": date,
        "stage": (raw.get("stage") or DEFAULT_STAGE).strip() or DEFAULT_STAGE,
        "home": home,
        "away": away,
        "neutral": _parse_bool(raw.get("neutral"), line_no),
        "sp_home": _parse_sp(raw.get("sp_home"), "sp_home", line_no),
        "sp_draw": _parse_sp(raw.get("sp_draw"), "sp_draw", line_no),
        "sp_away": _parse_sp(raw.get("sp_away"), "sp_away", line_no),
        "actual": _parse_actual(raw.get("actual"), line_no),
    }


def _parse_bool(value: object, line_no: int) -> bool:
    v = str(value or "").strip().lower()
    if v in {"", "true", "1", "yes", "y"}:
        return True
    if v in {"false", "0", "no", "n"}:
        return False
    raise ValueError(f"line {line_no}: neutral must be true/false")


def _parse_sp(value: object, field: str, line_no: int) -> float | None:
    v = str(value or "").strip()
    if not v:
        return None
    try:
        sp = float(v)
    except ValueError as exc:
        raise ValueError(f"line {line_no}: {field} must be numeric or blank") from exc
    if sp <= 1.0:
        raise ValueError(f"line {line_no}: {field} should be greater than 1.0")
    return sp


def _parse_actual(value: object, line_no: int) -> str | None:
    actual = str(value or "").strip().upper()
    if not actual:
        return None
    if actual not in ACTUAL_TO_OUTCOME:
        raise ValueError(f"line {line_no}: actual must be H/D/A or blank")
    return actual


def _is_tbd(name: str) -> bool:
    return name.strip() in TBD_NAMES


def _zero_allocation() -> dict[str, int]:
    return {outcome: 0 for outcome in OUTCOMES}


def _stage_count(matches: Sequence[Mapping[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in matches:
        stage = str(match.get("stage") or DEFAULT_STAGE)
        counts[stage] = counts.get(stage, 0) + 1
    return counts


def _probability_vector(probabilities: Mapping[str, float] | Sequence[float]) -> list[float]:
    if isinstance(probabilities, Mapping):
        return [float(probabilities[outcome]) for outcome in OUTCOMES]
    if len(probabilities) != 3:
        raise ValueError("probabilities must contain exactly three values")
    return [float(p) for p in probabilities]


def _round_to_unit(value: float, unit: int) -> int:
    ratio = Decimal(str(value)) / Decimal(unit)
    return int(ratio.quantize(Decimal("1"), rounding=ROUND_HALF_UP) * unit)


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _fmt_edge(value: float | None) -> str:
    if value is None:
        return "待录入SP"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.1f}%"


def _fmt_currency(value: float | None) -> str:
    return "—" if value is None else f"¥{int(round(value)):,.0f}"


def _fmt_signed_currency(value: float | None) -> str:
    if value is None:
        return "待开奖"
    value = 0.0 if abs(float(value)) < 0.05 else float(value)
    sign = "+" if value >= 0 else "-"
    return f"{sign}¥{abs(value):,.1f}"


def _fmt_console_currency(value: float | None) -> str:
    if value is None:
        return "待开奖"
    value = 0.0 if abs(float(value)) < 0.05 else float(value)
    sign = "+" if value >= 0 else "-"
    return f"{sign}{abs(value):,.1f} 元"


def _fmt_sp(value: float | None) -> str:
    return "待录入SP" if value is None else f"@{value:.2f}"


def _fmt_odds(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _fmt_triplet(values: Mapping[str, float | None] | None, formatter: Callable[[float | None], str]) -> str:
    if values is None:
        return "—/—/—"
    return "/".join(formatter(values[outcome]) for outcome in OUTCOMES)


def _fmt_pct_triplet(values: Mapping[str, float] | None) -> str:
    return _fmt_triplet(values, _fmt_pct)


def _fmt_sp_triplet(values: Mapping[str, float | None]) -> str:
    return _fmt_triplet(values, _fmt_sp)


def _fmt_alloc_triplet(values: Mapping[str, int]) -> str:
    return "/".join(f"{int(values[outcome]):,}" for outcome in OUTCOMES)


def _edge_hint(edge: Mapping[str, float | None]) -> str:
    if any(edge[outcome] is None for outcome in OUTCOMES):
        return "待录入SP"
    best = max(OUTCOMES, key=lambda outcome: float(edge[outcome]))
    label = OUTCOME_LABELS[best]
    if float(edge[best]) > 0:
        return f"正 edge：{label} {_fmt_edge(edge[best])}"
    return f"无正 edge，最佳 {label} {_fmt_edge(edge[best])}"


def _display_width(value: str) -> int:
    import unicodedata

    width = 0
    for char in value:
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _pad(value: str, width: int) -> str:
    return value + " " * (width - _display_width(value))


def _negative_class(value: float | None) -> str:
    return "negative" if value is not None and float(value) < 0 else ""


def _value_class(value: float | None) -> str:
    if value is None:
        return ""
    return "positive" if float(value) >= 0 else "negative"


def _html_escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _console_safe(value: str) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    return value.encode(encoding, errors="replace").decode(encoding)


def _team_zh(team: str) -> str:
    return TEAM_ZH.get(str(team), str(team))


def _match_title(match: Mapping[str, object]) -> str:
    return f"{match.get('home_zh') or _team_zh(match['home'])} vs {match.get('away_zh') or _team_zh(match['away'])}"


def _bet_side_label(match: Mapping[str, object], outcome: str) -> str:
    if outcome == "home":
        return str(match.get("home_zh") or _team_zh(str(match.get("home", ""))))
    if outcome == "away":
        return str(match.get("away_zh") or _team_zh(str(match.get("away", ""))))
    return "平局"


def _parse_review_date(value: str | date | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _match_date(match: Mapping[str, object]) -> date | None:
    try:
        return date.fromisoformat(str(match.get("date", "")))
    except ValueError:
        return None


def _should_await_result(match: Mapping[str, object], review_date: date) -> bool:
    match_date = _match_date(match)
    return (
        match.get("status") == "pending"
        and bool(match.get("has_any_sp"))
        and not bool(match.get("unresolved"))
        and match_date is not None
        and match_date <= review_date
    )


def _should_show_pending_card(match: Mapping[str, object]) -> bool:
    """Return True when a pending match has SP data worth reviewing."""
    return (
        match.get("status") == "pending"
        and not bool(match.get("unresolved"))
        and bool(match.get("has_any_sp"))
    )


def _main_display_matches(review: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Matches that deserve the main console/web review surface."""
    matches = list(review.get("matches", []))
    pending = list(review.get("display_pending") or [
        m for m in matches if m.get("status") == "pending" and _should_show_pending_card(m)
    ])
    awaiting = list(review.get("awaiting_result") or [])
    settled = list(review.get("settled") or [m for m in matches if m.get("status") == "settled"])
    return [*awaiting, *pending, *settled]


def _render_card_grid(matches: Iterable[Mapping[str, object]], empty_text: str) -> str:
    matches = list(matches)
    if not matches:
        return f'<div class="empty">{_html_escape(empty_text)}</div>'
    cards = "\n".join(_render_card(match) for match in matches)
    return f'<div class="card-grid">{cards}</div>'


def _render_template_section(matches: Iterable[Mapping[str, object]]) -> str:
    matches = list(matches)
    if not matches:
        return '<div class="empty">暂无待录入模板。</div>'
    rows = "\n".join(_render_template_row(match) for match in matches)
    return f"""
      <details class="template-box">
        <summary>展开查看 {len(matches)} 场待录入模板</summary>
        <div class="template-list">{rows}</div>
      </details>
"""


def _render_template_row(match: Mapping[str, object]) -> str:
    return f"""
          <div class="template-row">
            {_html_escape(match['date'])} · {_html_escape(match['stage'])}
            <strong>{_html_escape(_match_title(match))}</strong>
          </div>"""


def _render_card(match: Mapping[str, object]) -> str:
    if match["status"] == "pending":
        if match["unresolved"]:
            status = '<span class="status waiting">对阵待定</span>'
        elif match["stake_status"] == "待录入SP":
            status = '<span class="status waiting">待录入SP</span>'
        elif match.get("needs_result"):
            status = '<span class="status waiting">待赛果</span>'
        elif match["stake_total"] > 0:
            status = '<span class="status staked">待开奖</span>'
        else:
            status = '<span class="status waiting">观望</span>'
    else:
        pnl = float(match["pnl"])
        status = (
            f'<span class="status {_value_class(pnl)}">'
            f'实际 {match["actual_label"]} {_fmt_signed_currency(pnl)}</span>'
        )

    return f"""
        <article class="match-card">
          <header class="match-head">
            <div>
              <div class="date">{_html_escape(match['date'])}<span class="stage">{_html_escape(match['stage'])}</span></div>
              <h3 class="match-title">{_html_escape(_match_title(match))}</h3>
            </div>
            {status}
          </header>
          <div class="bet-grid">
            {_render_bet_column(match, "home")}
            {_render_bet_column(match, "draw")}
            {_render_bet_column(match, "away")}
          </div>
          {_render_model_details(match)}
        </article>
"""


def _render_bet_column(match: Mapping[str, object], outcome: str) -> str:
    classes = []
    if match["allocation"][outcome] > 0:
        classes.append("selected")
    if match["actual_outcome"] == outcome:
        classes.append("hit")
    class_attr = "" if not classes else " " + " ".join(classes)

    # When SP has not been entered, this is a prediction card, not a betting
    # review card. Show the model probability in the main three columns instead
    # of three useless "¥0" boxes.
    if match.get("has_any_sp"):
        label = BET_LABELS[outcome]
        amount = _fmt_currency(match["allocation"][outcome])
    else:
        label = f"{OUTCOME_LABELS[outcome]}概率"
        probs = match.get("probabilities")
        amount = _fmt_pct(None if probs is None else probs[outcome])

    return f"""
            <div class="bet-column{class_attr}">
              <div class="bet-label"><span class="marker">{OUTCOME_MARKERS[outcome]}</span>{label}</div>
              <div class="bet-team">{_html_escape(_bet_side_label(match, outcome))}</div>
              <div class="amount">{amount}</div>
              <div class="sp">{_fmt_sp(match["sp"][outcome])}</div>
            </div>"""


def _render_model_details(match: Mapping[str, object]) -> str:
    return f"""
          <details class="model-details">
            <summary>查看模型细节</summary>
            <div class="detail-grid">
              {_render_detail_row("模型概率", match["probabilities"], _fmt_pct)}
              {_render_detail_row("公允赔率", match["fair_odds"], _fmt_odds)}
              {_render_detail_row("Edge", match["edge"], _fmt_edge, signed=True)}
              <div class="detail-row"><div class="detail-label">策略</div><div class="detail-value" style="grid-column: span 3; text-align:left;">{_html_escape(match["stake_status"])}</div></div>
            </div>
          </details>"""


def _render_detail_row(
    label: str,
    values: Mapping[str, float | None] | None,
    formatter: Callable[[float | None], str],
    signed: bool = False,
) -> str:
    cells = []
    for outcome in OUTCOMES:
        value = None if values is None else values[outcome]
        cls = f" {_value_class(value)}" if signed and value is not None else ""
        cells.append(
            f'<div class="detail-value{cls}"><b>{OUTCOME_LABELS[outcome]}</b>{formatter(value)}</div>'
        )
    return f'<div class="detail-row"><div class="detail-label">{label}</div>{"".join(cells)}</div>'
