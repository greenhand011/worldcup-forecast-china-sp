"""Manual China Sports Lottery SP review helpers.

China Sports Lottery SP values are user-entered review data only. They are
applied after the project's market-independent calibrated model probabilities
and are never used as model inputs.
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
BET_LABELS = {"home": "主胜下注", "draw": "平局下注", "away": "客胜下注"}
OUTCOME_MARKERS = {"home": "主", "draw": "平", "away": "客"}
ACTUAL_TO_OUTCOME = {"H": "home", "D": "draw", "A": "away"}
ACTUAL_LABELS = {"H": "主胜", "D": "平局", "A": "客胜", None: "待开奖"}
REPO_URL = "https://github.com/greenhand011/worldcup-forecast-china-sp"


def read_china_sp_csv(path: str | Path) -> list[dict]:
    """Read the manual China Sports Lottery SP CSV into typed match rows.

    Lines starting with ``#`` are ignored so the committed demo file can explain
    that its SP values are placeholders, not official China Sports Lottery data.
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

    The rounded allocations are forced to sum exactly to ``bankroll`` by adding
    the residual to the largest-probability outcome.
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
    profitable = [m for m in settled if float(m["pnl"]) > 0]
    hit_rate = (len(profitable) / len(settled)) if settled else None
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
            "profitable_count": len(profitable),
            "hit_rate": hit_rate,
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
            "待开奖" if match["pnl"] is None else _fmt_console_currency(match["pnl"]),
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
        f"累计收益 {_fmt_console_currency(summary['cumulative_pnl'])}"
    )
    return "\n".join(["中国体彩 SP 世界杯复盘", "", line, sep, *body, "", footer])


def render_html(review: Mapping[str, object]) -> str:
    """Render the review as a self-contained static HTML document."""
    matches = list(review["matches"])
    pending = [m for m in matches if m["status"] == "pending"]
    settled = [m for m in matches if m["status"] == "settled"]
    summary = review["summary"]
    hit_rate = summary.get("hit_rate")
    hit_rate_card = "" if hit_rate is None else f"""
      <div class="summary-item">
        <span>盈利场次率</span>
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
      grid-template-columns: minmax(0, 1.35fr) repeat(3, minmax(0, 1fr));
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
    .status.pending {{
      color: var(--blue);
      background: var(--blue-soft);
      border: 1px solid #c7ddff;
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
      margin-bottom: 8px;
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
    .disclaimer {{
      margin-top: 28px;
      padding-top: 16px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
      line-height: 1.7;
    }}
    @media (max-width: 760px) {{
      main {{ width: min(100% - 20px, 980px); }}
      .topbar {{ grid-template-columns: 1fr; }}
      .github-link {{ justify-self: start; }}
      .summary {{ grid-template-columns: 1fr 1fr; }}
      .summary-item.profit {{ grid-column: 1 / -1; }}
      .card-grid {{ grid-template-columns: 1fr; }}
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
        <p class="subtitle">基于模型概率 + 用户手动录入中国体彩胜平负 SP 的复盘页面</p>
      </div>
      <a class="github-link" href="{REPO_URL}">GitHub</a>
    </header>

    <section class="summary" aria-label="复盘汇总">
      <div class="summary-item profit {_negative_class(summary['cumulative_pnl'])}">
        <span>累计收益</span>
        <strong>{_fmt_signed_currency(summary['cumulative_pnl'])}</strong>
      </div>
      <div class="summary-item">
        <span>已结算场次</span>
        <strong>{summary['settled_count']}</strong>
      </div>
      <div class="summary-item">
        <span>待开奖场次</span>
        <strong>{summary['pending_count']}</strong>
      </div>{hit_rate_card}
    </section>

    <section>
      <div class="section-heading">
        <h2>未来预测 {len(pending)}</h2>
        <span>actual 留空的比赛</span>
      </div>
      {_render_card_grid(pending, empty_text="暂无待开奖比赛。")}
    </section>

    <section>
      <div class="section-heading">
        <h2>历史复盘 {len(settled)}</h2>
        <span>actual = H / D / A 的比赛</span>
      </div>
      {_render_card_grid(settled, empty_text="暂无已结算比赛。")}
    </section>

    <footer class="disclaimer">
      本页面仅用于模型复盘和学习，不构成投注建议。中国体彩 SP 需用户手动录入。示例 SP 和赛果只是 demo 占位，不代表官方中国体彩数据。模型概率不是保证，历史盈亏不能证明长期存在 edge。
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


def _fmt_currency(value: float) -> str:
    return f"¥{int(round(value)):,.0f}"


def _fmt_signed_currency(value: float) -> str:
    value = 0.0 if abs(float(value)) < 0.05 else float(value)
    sign = "+" if value >= 0 else "-"
    return f"{sign}¥{abs(value):,.1f}"


def _fmt_console_currency(value: float) -> str:
    value = 0.0 if abs(float(value)) < 0.05 else float(value)
    sign = "+" if value >= 0 else "-"
    return f"{sign}{abs(value):,.1f} 元"


def _fmt_sp(value: float) -> str:
    return f"@{value:.2f}"


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


def _render_card_grid(matches: Iterable[Mapping[str, object]], empty_text: str) -> str:
    matches = list(matches)
    if not matches:
        return f'<div class="empty">{_html_escape(empty_text)}</div>'
    cards = "\n".join(_render_card(match) for match in matches)
    return f'<div class="card-grid">{cards}</div>'


def _render_card(match: Mapping[str, object]) -> str:
    if match["status"] == "pending":
        status = '<span class="status pending">待开奖</span>'
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
              <div class="date">{_html_escape(match['date'])}</div>
              <h3 class="match-title">{_html_escape(match['home'])}<span>vs</span>{_html_escape(match['away'])}</h3>
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
    hit = " hit" if match["actual_outcome"] == outcome else ""
    return f"""
            <div class="bet-column{hit}">
              <div class="bet-label"><span class="marker">{OUTCOME_MARKERS[outcome]}</span>{BET_LABELS[outcome]}</div>
              <div class="amount">{_fmt_currency(match["allocation"][outcome])}</div>
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
            </div>
          </details>"""


def _render_detail_row(
    label: str,
    values: Mapping[str, float],
    formatter: Callable[[float], str],
    signed: bool = False,
) -> str:
    cells = []
    for outcome in OUTCOMES:
        value = float(values[outcome])
        cls = f" {_value_class(value)}" if signed else ""
        cells.append(
            f'<div class="detail-value{cls}"><b>{OUTCOME_LABELS[outcome]}</b>{formatter(value)}</div>'
        )
    return f'<div class="detail-row"><div class="detail-label">{label}</div>{"".join(cells)}</div>'
