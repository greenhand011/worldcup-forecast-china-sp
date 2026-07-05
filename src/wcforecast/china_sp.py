"""Manual China Sports Lottery SP review helpers.

This module treats China Sports Lottery SP values as user-entered review data only.
The SP values are never used as model inputs; they are applied after the model's
market-independent 1X2 probabilities have been calibrated.
"""
from __future__ import annotations

import csv
import html
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np

from . import predict
from .teams import INDEX

CSV_FIELDS = ("date", "home", "away", "neutral", "sp_home", "sp_draw", "sp_away", "actual")
OUTCOMES = ("home", "draw", "away")
OUTCOME_LABELS = {"home": "主胜", "draw": "平局", "away": "客胜"}
ACTUAL_TO_OUTCOME = {"H": "home", "D": "draw", "A": "away"}
ACTUAL_LABELS = {"H": "主胜", "D": "平局", "A": "客胜", None: "待开奖"}


def read_china_sp_csv(path: str | Path) -> list[dict]:
    """Read the manual China Sports Lottery SP CSV into typed match rows."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"China SP review CSV not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
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

    The rounded allocations are forced to sum exactly to ``bankroll`` by adding the
    residual to the largest-probability outcome.
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


def review_match(
    row: Mapping[str, object],
    model,
    bankroll: int = 10000,
    unit: int = 100,
    calibrator: Callable[[Sequence[float]], Sequence[float]] = predict.calibrate,
) -> dict:
    """Review one manually-entered SP row against calibrated model probabilities."""
    home = str(row["home"]).strip()
    away = str(row["away"]).strip()
    if home not in INDEX or away not in INDEX:
        raise ValueError(f"unknown team(s): {home} vs {away}")

    neutral = bool(row["neutral"])
    raw = model.match_probs(home, away, home_advantage=0.0 if neutral else 1.0)
    calibrated = np.asarray(calibrator(raw), dtype=float)
    if calibrated.shape != (3,):
        raise ValueError("calibrator must return a length-3 probability vector")

    probabilities = dict(zip(OUTCOMES, (float(x) for x in calibrated)))
    sp = {"home": float(row["sp_home"]), "draw": float(row["sp_draw"]), "away": float(row["sp_away"])}
    fair_odds = {outcome: 1.0 / probabilities[outcome] for outcome in OUTCOMES}
    edge = {outcome: probabilities[outcome] * sp[outcome] - 1.0 for outcome in OUTCOMES}
    allocation = allocate_bankroll(probabilities, bankroll=bankroll, unit=unit)

    actual = row.get("actual")
    actual = None if actual in ("", None) else str(actual).strip().upper()
    if actual and actual not in ACTUAL_TO_OUTCOME:
        raise ValueError(f"actual must be H/D/A or blank, got: {actual}")

    if actual:
        actual_outcome = ACTUAL_TO_OUTCOME[actual]
        pnl = allocation[actual_outcome] * sp[actual_outcome] - int(bankroll)
        status = "settled"
    else:
        actual_outcome = None
        pnl = None
        status = "pending"

    return {
        "date": str(row["date"]).strip(),
        "home": home,
        "away": away,
        "neutral": neutral,
        "probabilities": probabilities,
        "sp": sp,
        "fair_odds": fair_odds,
        "edge": edge,
        "allocation": allocation,
        "actual": actual,
        "actual_outcome": actual_outcome,
        "actual_label": ACTUAL_LABELS[actual],
        "pnl": pnl,
        "status": status,
        "bankroll": int(bankroll),
        "unit": int(unit),
    }


def build_review(
    input_path: str | Path,
    model,
    bankroll: int = 10000,
    unit: int = 100,
    calibrator: Callable[[Sequence[float]], Sequence[float]] = predict.calibrate,
) -> dict:
    """Build a structured China SP review for CLI and HTML rendering."""
    rows = read_china_sp_csv(input_path)
    matches = [
        review_match(row, model, bankroll=bankroll, unit=unit, calibrator=calibrator)
        for row in rows
    ]
    settled = [m for m in matches if m["status"] == "settled"]
    pending = [m for m in matches if m["status"] == "pending"]
    cumulative_pnl = sum(float(m["pnl"]) for m in settled)
    return {
        "input": str(input_path),
        "bankroll": int(bankroll),
        "unit": int(unit),
        "matches": matches,
        "summary": {
            "cumulative_pnl": cumulative_pnl,
            "settled_count": len(settled),
            "pending_count": len(pending),
            "match_count": len(matches),
        },
    }


def format_console_table(review: Mapping[str, object]) -> str:
    """Return the Chinese console table required by ``wcforecast china-sp-review``."""
    headers = [
        "对阵",
        "模型胜/平/负%",
        "体彩SP胜/平/负",
        "公允赔率胜/平/负",
        "分配胜/平/负",
        "实际",
        "盈亏",
        "edge提示",
    ]
    rows = []
    for match in review["matches"]:
        rows.append([
            f"{match['home']} vs {match['away']}",
            _fmt_pct_triplet(match["probabilities"]),
            _fmt_odds_triplet(match["sp"]),
            _fmt_odds_triplet(match["fair_odds"]),
            _fmt_alloc_triplet(match["allocation"]),
            match["actual_label"],
            "待开奖" if match["pnl"] is None else _fmt_money(match["pnl"]),
            _edge_hint(match["edge"]),
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
        f"合计：已结算 {summary['settled_count']} 场，待开奖 {summary['pending_count']} 场，"
        f"累计盈亏 {_fmt_money(summary['cumulative_pnl'])}"
    )
    return "\n".join(["中国体彩 SP 世界杯复盘", "", line, sep, *body, "", footer])


def render_html(review: Mapping[str, object]) -> str:
    """Render the review as a self-contained static HTML document."""
    matches = list(review["matches"])
    pending = [m for m in matches if m["status"] == "pending"]
    settled = [m for m in matches if m["status"] == "settled"]
    summary = review["summary"]
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>中国体彩 SP 世界杯复盘</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101318;
      --panel: #191f27;
      --panel-2: #202833;
      --text: #eef3f7;
      --muted: #9caab8;
      --line: #303a46;
      --green: #48d17d;
      --red: #ff6b6b;
      --gold: #f2c14e;
      --cyan: #5bc0eb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        linear-gradient(135deg, rgba(91, 192, 235, .12), transparent 32rem),
        radial-gradient(circle at 88% 8%, rgba(242, 193, 78, .12), transparent 18rem),
        var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      letter-spacing: 0;
    }}
    main {{
      width: min(1180px, calc(100% - 32px));
      margin: 0 auto;
      padding: 36px 0 48px;
    }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 24px;
      align-items: end;
      padding: 22px 0 28px;
      border-bottom: 1px solid var(--line);
    }}
    .eyebrow {{
      color: var(--gold);
      font-size: 13px;
      font-weight: 700;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 8px 0 10px;
      font-size: clamp(30px, 4vw, 52px);
      line-height: 1.05;
      letter-spacing: 0;
    }}
    .subtle {{
      margin: 0;
      color: var(--muted);
      line-height: 1.7;
      max-width: 760px;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin: 24px 0 30px;
    }}
    .summary-item {{
      padding: 16px 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .summary-item span {{
      display: block;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 6px;
    }}
    .summary-item strong {{
      font-size: 30px;
      line-height: 1.15;
    }}
    h2.section-title {{
      margin: 34px 0 14px;
      font-size: 22px;
      letter-spacing: 0;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(310px, 1fr));
      gap: 16px;
    }}
    .match-card {{
      background: linear-gradient(180deg, var(--panel), var(--panel-2));
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 18px 50px rgba(0, 0, 0, .22);
    }}
    .match-head {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 14px;
      align-items: start;
      border-bottom: 1px solid var(--line);
      padding-bottom: 14px;
      margin-bottom: 14px;
    }}
    .date {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 5px;
    }}
    .match-title {{
      margin: 0;
      font-size: 22px;
      line-height: 1.2;
      overflow-wrap: anywhere;
    }}
    .match-title span {{
      color: var(--muted);
      font-weight: 500;
      font-size: 15px;
      margin: 0 5px;
    }}
    .badge {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      color: var(--cyan);
      white-space: nowrap;
      font-size: 13px;
      background: rgba(91, 192, 235, .08);
    }}
    .badge.settled {{
      color: var(--gold);
      background: rgba(242, 193, 78, .08);
    }}
    .rows {{
      display: grid;
      gap: 10px;
    }}
    .metric {{
      display: grid;
      grid-template-columns: 84px repeat(3, minmax(0, 1fr));
      gap: 8px;
      align-items: center;
      font-size: 14px;
    }}
    .metric .label {{
      color: var(--muted);
      font-size: 13px;
    }}
    .pill {{
      min-width: 0;
      padding: 8px 9px;
      border-radius: 6px;
      background: rgba(255, 255, 255, .045);
      border: 1px solid rgba(255, 255, 255, .06);
      text-align: right;
      white-space: nowrap;
    }}
    .pill b {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 600;
      margin-bottom: 2px;
      text-align: left;
    }}
    .pos {{ color: var(--green); }}
    .neg {{ color: var(--red); }}
    .result {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--line);
    }}
    .result div {{
      color: var(--muted);
      font-size: 13px;
    }}
    .result strong {{
      display: block;
      color: var(--text);
      font-size: 20px;
      margin-top: 3px;
    }}
    .empty {{
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 22px;
    }}
    .disclaimer {{
      margin-top: 36px;
      padding: 18px 0 0;
      border-top: 1px solid var(--line);
      color: var(--muted);
      line-height: 1.7;
      font-size: 14px;
    }}
    @media (max-width: 720px) {{
      main {{ width: min(100% - 20px, 1180px); padding-top: 22px; }}
      .hero {{ grid-template-columns: 1fr; }}
      .summary {{ grid-template-columns: 1fr; }}
      .metric {{ grid-template-columns: 1fr; }}
      .pill {{ text-align: left; }}
      .result {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div>
        <div class="eyebrow">Manual SP Review</div>
        <h1>中国体彩 SP 世界杯复盘</h1>
        <p class="subtle">基于项目原有模型的校准胜平负概率，叠加用户手动录入的中国体彩竞彩足球胜平负 SP，用统一模拟本金复盘分配和结算。</p>
      </div>
    </section>

    <section class="summary" aria-label="复盘汇总">
      <div class="summary-item"><span>累计盈亏</span><strong class="{_sign_class(summary['cumulative_pnl'])}">{_fmt_money(summary['cumulative_pnl'])}</strong></div>
      <div class="summary-item"><span>已结算场次</span><strong>{summary['settled_count']}</strong></div>
      <div class="summary-item"><span>待开奖场次</span><strong>{summary['pending_count']}</strong></div>
    </section>

    <h2 class="section-title">未来预测 / 待开奖</h2>
    {_render_card_grid(pending, empty_text="暂无待开奖比赛。")}

    <h2 class="section-title">历史复盘</h2>
    {_render_card_grid(settled, empty_text="暂无已结算比赛。")}

    <footer class="disclaimer">
      本页面仅用于模型复盘和学习，不构成投注建议。中国体彩 SP 需用户手动录入。模型概率不是保证，历史盈亏不能证明长期存在 edge。
    </footer>
  </main>
</body>
</html>
"""


def write_html(review: Mapping[str, object], output_path: str | Path) -> Path:
    """Write the static HTML review page and return its path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html(review), encoding="utf-8")
    return output_path


def _parse_row(raw: Mapping[str, str], line_no: int) -> dict:
    date = (raw.get("date") or "").strip()
    home = (raw.get("home") or "").strip()
    away = (raw.get("away") or "").strip()
    if not date or not home or not away:
        raise ValueError(f"line {line_no}: date, home and away are required")
    return {
        "date": date,
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
    if v in {"true", "1", "yes", "y"}:
        return True
    if v in {"false", "0", "no", "n"}:
        return False
    raise ValueError(f"line {line_no}: neutral must be true/false")


def _parse_sp(value: object, field: str, line_no: int) -> float:
    try:
        sp = float(str(value or "").strip())
    except ValueError as exc:
        raise ValueError(f"line {line_no}: {field} must be numeric") from exc
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


def _probability_vector(probabilities: Mapping[str, float] | Sequence[float]) -> list[float]:
    if isinstance(probabilities, Mapping):
        return [float(probabilities[outcome]) for outcome in OUTCOMES]
    if len(probabilities) != 3:
        raise ValueError("probabilities must contain exactly three values")
    return [float(p) for p in probabilities]


def _round_to_unit(value: float, unit: int) -> int:
    ratio = Decimal(str(value)) / Decimal(unit)
    return int(ratio.quantize(Decimal("1"), rounding=ROUND_HALF_UP) * unit)


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _fmt_edge(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.1f}%"


def _fmt_money(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}{abs(value):,.0f} 元"


def _fmt_yuan(value: float) -> str:
    return f"{int(value):,} 元"


def _fmt_odds(value: float) -> str:
    return f"{value:.2f}"


def _fmt_triplet(values: Mapping[str, float], formatter: Callable[[float], str]) -> str:
    return "/".join(formatter(float(values[outcome])) for outcome in OUTCOMES)


def _fmt_pct_triplet(values: Mapping[str, float]) -> str:
    return _fmt_triplet(values, _fmt_pct)


def _fmt_odds_triplet(values: Mapping[str, float]) -> str:
    return _fmt_triplet(values, _fmt_odds)


def _fmt_alloc_triplet(values: Mapping[str, int]) -> str:
    return "/".join(f"{int(values[outcome]):,}" for outcome in OUTCOMES)


def _edge_hint(edge: Mapping[str, float]) -> str:
    best = max(OUTCOMES, key=lambda outcome: edge[outcome])
    label = OUTCOME_LABELS[best]
    if edge[best] > 0:
        return f"正edge：{label} {_fmt_edge(edge[best])}"
    return f"无正edge，最佳 {label} {_fmt_edge(edge[best])}"


def _display_width(value: str) -> int:
    import unicodedata

    width = 0
    for char in value:
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _pad(value: str, width: int) -> str:
    return value + " " * (width - _display_width(value))


def _sign_class(value: float | None) -> str:
    if value is None:
        return ""
    return "pos" if value >= 0 else "neg"


def _html_escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _render_card_grid(matches: Iterable[Mapping[str, object]], empty_text: str) -> str:
    matches = list(matches)
    if not matches:
        return f'<div class="empty">{_html_escape(empty_text)}</div>'
    cards = "\n".join(_render_card(match) for match in matches)
    return f'<section class="grid">{cards}</section>'


def _render_card(match: Mapping[str, object]) -> str:
    status_class = "settled" if match["status"] == "settled" else ""
    pnl = match["pnl"]
    pnl_text = "待开奖" if pnl is None else _fmt_money(float(pnl))
    result_class = "" if pnl is None else _sign_class(float(pnl))
    venue = "中立场" if match["neutral"] else "主场"
    return f"""
      <article class="match-card">
        <header class="match-head">
          <div>
            <div class="date">{_html_escape(match['date'])} · {venue}</div>
            <h3 class="match-title">{_html_escape(match['home'])}<span>vs</span>{_html_escape(match['away'])}</h3>
          </div>
          <div class="badge {status_class}">{_html_escape(match['actual_label'])}</div>
        </header>
        <div class="rows">
          {_render_metric("模型概率", match["probabilities"], _fmt_pct)}
          {_render_metric("体彩SP", match["sp"], _fmt_odds)}
          {_render_metric("公允赔率", match["fair_odds"], _fmt_odds)}
          {_render_metric("模拟分配", match["allocation"], _fmt_yuan)}
          {_render_metric("单项edge", match["edge"], _fmt_edge, value_class=True)}
        </div>
        <div class="result">
          <div>实际结果<strong>{_html_escape(match['actual_label'])}</strong></div>
          <div>盈亏<strong class="{result_class}">{pnl_text}</strong></div>
        </div>
      </article>
"""


def _render_metric(
    label: str,
    values: Mapping[str, float],
    formatter: Callable[[float], str],
    value_class: bool = False,
) -> str:
    cells = []
    for outcome in OUTCOMES:
        value = float(values[outcome])
        cls = _sign_class(value) if value_class else ""
        cells.append(
            f'<div class="pill {cls}"><b>{OUTCOME_LABELS[outcome]}</b>{formatter(value)}</div>'
        )
    return f'<div class="metric"><div class="label">{label}</div>{"".join(cells)}</div>'
