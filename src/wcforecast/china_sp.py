"""China Sports Lottery SP review helpers.

SP values are post-prediction review data only. They are never used as model inputs.
The default staking layer is model-favorite based: every complete-SP match receives one flat simulated stake on the model's highest-probability outcome. SP remains post-prediction review data only.
"""
from __future__ import annotations

import csv
import html
import sys
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

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
STRATEGIES = {"favorite-flat", "edge-flat", "prob-split", "kelly"}
COMPARISON_STRATEGIES = ("favorite-flat", "prob-split", "edge-flat", "kelly")

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
    """Read the China SP review CSV into typed rows.

    Lines starting with ``#`` are ignored so the committed CSV can keep human notes.
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
    bankroll: int = 100,
    unit: int = 1,
) -> dict[str, int]:
    """Legacy probability split: allocate the full bankroll by model probability."""
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


def allocate_model_favorite(
    probabilities: Mapping[str, float],
    bankroll: int = 100,
    unit: int = 1,
    edge: Mapping[str, float | None] | None = None,
) -> tuple[dict[str, int], str, str]:
    """Mandatory flat stake on the model's highest-probability outcome.

    This is the default when the review must place a simulated stake on every
    complete-SP match. It avoids the two failure modes seen in review:
    probability-splitting leaks bankroll across losing outcomes, while pure
    edge selection can chase low-probability long shots. SP still does not feed
    the model; it is reported only as a post-prediction value check.
    """
    bankroll = int(bankroll)
    unit = int(unit)
    if bankroll <= 0:
        raise ValueError("bankroll must be positive")
    if unit <= 0:
        raise ValueError("unit must be positive")
    if bankroll < unit:
        raise ValueError("bankroll must be at least one unit")

    favorite = max(OUTCOMES, key=lambda outcome: float(probabilities[outcome]))
    stake = _round_to_unit(bankroll, unit)
    stake = min(stake, bankroll)
    if stake <= 0:
        raise ValueError("stake must be positive")
    edge_text = ""
    if edge is not None and edge.get(favorite) is not None:
        edge_text = f"，edge {_fmt_edge(float(edge[favorite]))}"
    return {outcome: stake if outcome == favorite else 0 for outcome in OUTCOMES}, favorite, (
        f"favorite-flat：买入模型第一选择{OUTCOME_LABELS[favorite]}，"
        f"模型概率 {_fmt_pct(float(probabilities[favorite]))}{edge_text}"
    )


def allocate_best_edge(
    edge: Mapping[str, float | None],
    bankroll: int = 100,
    unit: int = 1,
    min_edge: float = 0.05,
    probabilities: Mapping[str, float] | None = None,
    require_model_favorite: bool = True,
    min_probability: float = 0.40,
) -> tuple[dict[str, int], str | None, str]:
    """Conservative flat value strategy.

    The market/SP layer is noisy on tiny samples. To avoid chasing long-shot
    edges created by model-market disagreement, the default value strategy only
    bets when the best-edge side is also the model's top probability side and
    its probability is high enough. This changes the review layer only; SP still
    never enters the model.
    """
    bankroll = int(bankroll)
    unit = int(unit)
    if bankroll <= 0:
        raise ValueError("bankroll must be positive")
    if unit <= 0:
        raise ValueError("unit must be positive")
    if bankroll < unit:
        raise ValueError("bankroll must be at least one unit")

    if any(edge.get(outcome) is None for outcome in OUTCOMES):
        return _zero_allocation(), None, "待录入SP"

    best = max(OUTCOMES, key=lambda outcome: float(edge[outcome]))
    best_edge = float(edge[best])
    if best_edge <= float(min_edge):
        return _zero_allocation(), None, f"无正edge，观望（最佳 {OUTCOME_LABELS[best]} {_fmt_edge(best_edge)}）"

    if probabilities is not None:
        model_favorite = max(OUTCOMES, key=lambda outcome: float(probabilities[outcome]))
        best_probability = float(probabilities[best])
        if require_model_favorite and best != model_favorite:
            return _zero_allocation(), None, (
                f"观望：最佳edge为{OUTCOME_LABELS[best]} {_fmt_edge(best_edge)}，"
                f"但模型第一选择是{OUTCOME_LABELS[model_favorite]}"
            )
        if best_probability < float(min_probability):
            return _zero_allocation(), None, (
                f"观望：{OUTCOME_LABELS[best]} edge {_fmt_edge(best_edge)}，"
                f"但模型概率仅 {_fmt_pct(best_probability)}，低于稳健阈值 {_fmt_pct(min_probability)}"
            )

    stake = _round_to_unit(bankroll, unit)
    stake = min(stake, bankroll)
    if stake <= 0:
        return _zero_allocation(), None, "edge过阈值但下注额低于最小单位，观望"
    return {outcome: stake if outcome == best else 0 for outcome in OUTCOMES}, best, (
        f"edge-flat：买入{OUTCOME_LABELS[best]}，edge {_fmt_edge(best_edge)}"
    )


def allocate_fractional_kelly(
    probabilities: Mapping[str, float],
    sp: Mapping[str, float | None],
    bankroll: int = 100,
    unit: int = 1,
    min_edge: float = 0.05,
    kelly_fraction: float = 0.25,
    max_stake_fraction: float = 1.0,
) -> tuple[dict[str, int], str | None, str]:
    """Fractional Kelly on the best positive-edge outcome only."""
    bankroll = int(bankroll)
    unit = int(unit)
    if bankroll <= 0:
        raise ValueError("bankroll must be positive")
    if unit <= 0:
        raise ValueError("unit must be positive")
    if kelly_fraction <= 0:
        raise ValueError("kelly_fraction must be positive")
    if max_stake_fraction <= 0:
        raise ValueError("max_stake_fraction must be positive")
    if any(sp.get(outcome) is None for outcome in OUTCOMES):
        return _zero_allocation(), None, "待录入SP"

    candidates = []
    for outcome in OUTCOMES:
        probability = float(probabilities[outcome])
        odds = float(sp[outcome])
        edge = probability * odds - 1.0
        b = odds - 1.0
        if b <= 0 or edge <= float(min_edge):
            continue
        full_kelly = edge / b
        if full_kelly <= 0:
            continue
        candidates.append((edge, outcome, full_kelly))

    if not candidates:
        best = max(OUTCOMES, key=lambda outcome: float(probabilities[outcome]) * float(sp[outcome]) - 1.0)
        best_edge = float(probabilities[best]) * float(sp[best]) - 1.0
        return _zero_allocation(), None, f"无正edge，观望（最佳 {OUTCOME_LABELS[best]} {_fmt_edge(best_edge)}）"

    edge, best, full_kelly = max(candidates, key=lambda item: item[0])
    raw_stake = bankroll * min(max_stake_fraction, kelly_fraction * full_kelly)
    stake = _round_to_unit(raw_stake, unit)
    stake = max(0, min(stake, bankroll))
    if stake <= 0:
        return _zero_allocation(), None, "Kelly下注额低于最小单位，观望"
    return {outcome: stake if outcome == best else 0 for outcome in OUTCOMES}, best, (
        f"kelly {kelly_fraction:g}x：买入{OUTCOME_LABELS[best]}，edge {_fmt_edge(edge)}，full Kelly {full_kelly * 100:.1f}%"
    )


def review_match(
    row: Mapping[str, object],
    model,
    bankroll: int = 100,
    unit: int = 1,
    min_edge: float = 0.05,
    strategy: str = "favorite-flat",
    kelly_fraction: float = 0.25,
    max_stake_fraction: float = 1.0,
    calibrator: Callable[[Sequence[float]], Sequence[float]] = predict.calibrate,
) -> dict:
    """Review one SP row against calibrated model probabilities and a staking strategy."""
    strategy = str(strategy or "favorite-flat")
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy}")

    home = str(row["home"]).strip()
    away = str(row["away"]).strip()
    unresolved = _is_tbd(home) or _is_tbd(away)
    if not unresolved and (home not in INDEX or away not in INDEX):
        raise ValueError(f"unknown team(s): {home} vs {away}")

    neutral = bool(row["neutral"])
    sp = {"home": row.get("sp_home"), "draw": row.get("sp_draw"), "away": row.get("sp_away")}
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
        if has_complete_sp:
            if strategy == "prob-split":
                allocation = allocate_bankroll(probabilities, bankroll=bankroll, unit=unit)
                selected_outcome = max(OUTCOMES, key=lambda outcome: allocation[outcome])
                stake_status = "概率拆分复盘（非推荐下注策略）"
            elif strategy == "kelly":
                allocation, selected_outcome, stake_status = allocate_fractional_kelly(
                    probabilities,
                    sp,
                    bankroll=bankroll,
                    unit=unit,
                    min_edge=min_edge,
                    kelly_fraction=kelly_fraction,
                    max_stake_fraction=max_stake_fraction,
                )
            elif strategy == "edge-flat":
                allocation, selected_outcome, stake_status = allocate_best_edge(
                    edge,
                    bankroll=bankroll,
                    unit=unit,
                    min_edge=min_edge,
                    probabilities=probabilities,
                )
            else:
                allocation, selected_outcome, stake_status = allocate_model_favorite(
                    probabilities,
                    bankroll=bankroll,
                    unit=unit,
                    edge=edge,
                )
        else:
            allocation = _zero_allocation()
            selected_outcome = None
            stake_status = "待录入SP"

    stake_total = sum(allocation.values())
    actual = row.get("actual")
    actual = None if actual in ("", None) else str(actual).strip().upper()
    if actual and actual not in ACTUAL_TO_OUTCOME:
        raise ValueError(f"actual must be H/D/A or blank, got: {actual}")

    if actual:
        actual_outcome = ACTUAL_TO_OUTCOME[actual]
        actual_sp = sp[actual_outcome]
        pnl = (allocation[actual_outcome] * float(actual_sp) - stake_total) if stake_total and actual_sp is not None else 0.0
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
        "strategy": strategy,
        "actual": actual,
        "actual_outcome": actual_outcome,
        "actual_label": ACTUAL_LABELS[actual],
        "pnl": pnl,
        "status": status,
        "bankroll": int(bankroll),
        "unit": int(unit),
        "min_edge": float(min_edge),
        "kelly_fraction": float(kelly_fraction),
        "max_stake_fraction": float(max_stake_fraction),
    }


def _review_rows(
    rows: Sequence[Mapping[str, object]],
    model,
    bankroll: int,
    unit: int,
    min_edge: float,
    strategy: str,
    kelly_fraction: float,
    max_stake_fraction: float,
    calibrator: Callable[[Sequence[float]], Sequence[float]],
) -> list[dict]:
    return [
        review_match(
            row,
            model,
            bankroll=bankroll,
            unit=unit,
            min_edge=min_edge,
            strategy=strategy,
            kelly_fraction=kelly_fraction,
            max_stake_fraction=max_stake_fraction,
            calibrator=calibrator,
        )
        for row in rows
    ]


def build_strategy_comparison(
    rows: Sequence[Mapping[str, object]],
    model,
    strategies: Sequence[str] = COMPARISON_STRATEGIES,
    bankroll: int = 100,
    unit: int = 1,
    min_edge: float = 0.05,
    kelly_fraction: float = 0.25,
    max_stake_fraction: float = 1.0,
    calibrator: Callable[[Sequence[float]], Sequence[float]] = predict.calibrate,
) -> list[dict]:
    """Summarize settled P&L for several review-layer staking strategies."""
    comparison = []
    for strategy in strategies:
        matches = _review_rows(
            rows,
            model,
            bankroll=bankroll,
            unit=unit,
            min_edge=min_edge,
            strategy=strategy,
            kelly_fraction=kelly_fraction,
            max_stake_fraction=max_stake_fraction,
            calibrator=calibrator,
        )
        comparison.append(_summarize_strategy(strategy, matches))
    return comparison


def _summarize_strategy(strategy: str, matches: Sequence[Mapping[str, object]]) -> dict:
    settled = [m for m in matches if m["status"] == "settled"]
    staked_settled = [m for m in settled if int(m["stake_total"]) > 0]
    total_stake = sum(float(m["stake_total"]) for m in staked_settled)
    cumulative_pnl = sum(float(m["pnl"] or 0.0) for m in settled)
    profitable = [m for m in staked_settled if float(m["pnl"] or 0.0) > 0]
    pnl_values = [float(m["pnl"] or 0.0) for m in staked_settled]
    return {
        "strategy": strategy,
        "settled_count": len(settled),
        "staked_settled_count": len(staked_settled),
        "total_stake": total_stake,
        "cumulative_pnl": cumulative_pnl,
        "roi": (cumulative_pnl / total_stake) if total_stake else None,
        "profitable_rate": (len(profitable) / len(staked_settled)) if staked_settled else None,
        "max_profit": max(pnl_values) if pnl_values else None,
        "max_loss": min(pnl_values) if pnl_values else None,
    }


def build_review(
    input_path: str | Path,
    model,
    bankroll: int = 100,
    unit: int = 1,
    min_edge: float = 0.05,
    strategy: str = "favorite-flat",
    kelly_fraction: float = 0.25,
    max_stake_fraction: float = 1.0,
    today: str | date | None = None,
    calibrator: Callable[[Sequence[float]], Sequence[float]] = predict.calibrate,
) -> dict:
    """Build a structured China SP review for CLI and HTML rendering."""
    review_date = _parse_review_date(today)
    rows = read_china_sp_csv(input_path)
    matches = _review_rows(
        rows,
        model,
        bankroll=bankroll,
        unit=unit,
        min_edge=min_edge,
        strategy=strategy,
        kelly_fraction=kelly_fraction,
        max_stake_fraction=max_stake_fraction,
        calibrator=calibrator,
    )
    settled = sorted([m for m in matches if m["status"] == "settled"], key=lambda m: _match_date(m) or date.min, reverse=True)
    raw_pending = [m for m in matches if m["status"] == "pending"]
    current_prediction_date = _current_prediction_date(raw_pending, review_date)
    prediction_pending = [
        m
        for m in raw_pending
        if current_prediction_date is not None and _match_date(m) == current_prediction_date
        and _should_show_pending_card(m)
    ]
    awaiting_result = sorted([m for m in raw_pending if _should_await_result(m, review_date)], key=lambda m: _match_date(m) or date.min, reverse=True)
    future_pending = sorted(
        [m for m in raw_pending if _is_future_pending(m, current_prediction_date, review_date)],
        key=lambda m: _match_date(m) or date.max,
    )
    template_pending = [
        m for m in raw_pending
        if m not in prediction_pending and m not in awaiting_result and m not in future_pending
    ]
    staked_settled = [m for m in settled if int(m["stake_total"]) > 0]
    profitable = [m for m in staked_settled if float(m["pnl"]) > 0]
    cumulative_pnl = sum(float(m["pnl"] or 0.0) for m in settled)
    history_stake_total = sum(float(m["stake_total"]) for m in staked_settled)
    hit_rate = (len(profitable) / len(staked_settled)) if staked_settled else None
    roi = (cumulative_pnl / history_stake_total) if history_stake_total else None
    prediction_stake_total = sum(int(m["stake_total"]) for m in prediction_pending)
    for match in matches:
        match["needs_result"] = _should_await_result(match, review_date)
        match["is_today_pending"] = _is_today_pending(match, review_date)
        match["is_prediction_pending"] = match in prediction_pending
        match["is_future_pending"] = _is_future_pending(match, current_prediction_date, review_date)
    strategy_comparison = build_strategy_comparison(
        rows,
        model,
        bankroll=bankroll,
        unit=unit,
        min_edge=min_edge,
        kelly_fraction=kelly_fraction,
        max_stake_fraction=max_stake_fraction,
        calibrator=calibrator,
    )
    return {
        "input": str(input_path),
        "bankroll": int(bankroll),
        "unit": int(unit),
        "min_edge": float(min_edge),
        "strategy": strategy,
        "kelly_fraction": float(kelly_fraction),
        "max_stake_fraction": float(max_stake_fraction),
        "review_date": review_date.isoformat(),
        "current_prediction_date": (
            None if current_prediction_date is None else current_prediction_date.isoformat()
        ),
        "matches": matches,
        "today_pending": prediction_pending,
        "display_pending": prediction_pending,
        "prediction_pending": prediction_pending,
        "awaiting_result": awaiting_result,
        "future_pending": future_pending,
        "template_pending": template_pending,
        "settled": settled,
        "strategy_comparison": strategy_comparison,
        "summary": {
            "cumulative_pnl": cumulative_pnl,
            "settled_count": len(settled),
            "pending_count": len(prediction_pending),
            "today_pending_count": len(prediction_pending),
            "prediction_count": len(prediction_pending),
            "today_stake_total": prediction_stake_total,
            "prediction_stake_total": prediction_stake_total,
            "awaiting_result_count": len(awaiting_result),
            "future_count": len(future_pending),
            "raw_pending_count": len(raw_pending),
            "template_count": len(template_pending),
            "match_count": len(matches),
            "staked_count": len([m for m in matches if int(m["stake_total"]) > 0]),
            "staked_settled_count": len(staked_settled),
            "profitable_count": len(profitable),
            "hit_rate": hit_rate,
            "roi": roi,
            "history_stake_total": history_stake_total,
            "stage_count": _stage_count(matches),
        },
    }


def format_console_table(review: Mapping[str, object]) -> str:
    headers = ["赛段", "对阵", "模型胜/平/负%", "体彩SP胜/平/负", "分配胜/平/负", "实际", "盈亏", "策略"]
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
            match["stake_status"],
        ])
    widths = [max(_display_width(str(row[i])) for row in ([headers] + rows)) for i in range(len(headers))]
    line = "  ".join(_pad(headers[i], widths[i]) for i in range(len(headers)))
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = ["  ".join(_pad(str(row[i]), widths[i]) for i in range(len(headers))) for row in rows]
    summary = review["summary"]
    prediction_title = _prediction_title(review)
    footer = (
        f"合计：策略 {review.get('strategy', 'favorite-flat')}，"
        f"{prediction_title} {summary.get('prediction_count', summary.get('today_pending_count', 0))} 场，"
        f"预测区实际下注 {_fmt_console_currency(summary.get('prediction_stake_total', 0))}，"
        f"已下注结算 {summary.get('staked_settled_count', 0)}/{summary.get('settled_count', 0)}，"
        f"完赛待补赛果 {summary.get('awaiting_result_count', 0)} 场，"
        f"未来赛程 {summary.get('future_count', 0)} 场，"
        f"累计盈亏 {_fmt_console_currency(summary['cumulative_pnl'])}"
    )
    return _console_safe("\n".join(["中国体彩 SP 世界杯复盘", "", line, sep, *body, "", footer]))


def render_html(review: Mapping[str, object]) -> str:
    matches = list(review["matches"])
    prediction_pending = list(
        review.get("prediction_pending") or review.get("today_pending")
        or review.get("display_pending") or []
    )
    awaiting_result = sorted(list(review.get("awaiting_result") or []), key=lambda m: _match_date(m) or date.min, reverse=True)
    future_pending = sorted(list(review.get("future_pending") or []), key=lambda m: _match_date(m) or date.max)
    template_pending = list(review.get("template_pending") or [m for m in matches if m["status"] == "pending" and not _should_show_pending_card(m)])
    settled = sorted(list(review.get("settled") or [m for m in matches if m["status"] == "settled"]), key=lambda m: _match_date(m) or date.min, reverse=True)
    strategy_comparison = list(review.get("strategy_comparison") or [])
    summary = review["summary"]
    hit_rate = summary.get("hit_rate")
    roi = summary.get("roi")
    history_stats = []
    history_stats.append(f"已下注结算 {summary.get('staked_settled_count', 0)} / 已结算 {summary.get('settled_count', 0)}")
    if hit_rate is not None:
        history_stats.append(f"盈利下注率 {_fmt_pct(float(hit_rate))}")
    if roi is not None:
        history_stats.append(f"ROI {_fmt_pct(float(roi))}")
    history_stats.append(f"累计盈亏 {_fmt_signed_currency(summary['cumulative_pnl'])}")
    history_subtitle = "；".join(history_stats)
    pnl_class = "positive" if float(summary["cumulative_pnl"]) >= 0 else "negative"
    roi_text = "ROI —" if roi is None else f"ROI {_fmt_pct(float(roi))}"
    prediction_title = _prediction_title(review)
    prediction_note = _prediction_note(review)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>中国体彩 SP 世界杯预测</title>
  <style>
    :root {{ color-scheme: light; --bg:#f6f8fb; --card:#fff; --text:#1f2937; --muted:#6b7280; --line:#d9e0e8; --soft-line:#edf1f5; --blue:#2563eb; --blue-soft:#eff6ff; --green:#159957; --green-soft:#eaf8f0; --red:#dc2626; --red-soft:#fef2f2; --amber:#b45309; --amber-soft:#fff7ed; --shadow:0 10px 30px rgba(31,41,55,.08); }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; min-height:100vh; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; }}
    main {{ width:min(980px, calc(100% - 28px)); margin:0 auto; padding:22px 0 44px; }}
    .topbar {{ display:grid; grid-template-columns:minmax(0,1fr) auto; gap:16px; align-items:center; padding:14px 0 16px; }}
    h1 {{ margin:0 0 6px; font-size:clamp(24px,3.4vw,34px); line-height:1.16; }}
    .subtitle {{ margin:0; color:var(--muted); font-size:14px; line-height:1.6; }}
    .github-link {{ display:inline-flex; align-items:center; justify-content:center; min-height:40px; padding:0 14px; border:1px solid var(--line); border-radius:8px; color:var(--text); background:var(--card); text-decoration:none; font-weight:700; box-shadow:0 2px 10px rgba(31,41,55,.05); white-space:nowrap; }}
    .summary {{ display:grid; grid-template-columns:minmax(220px,1.8fr) repeat(5,minmax(0,1fr)); gap:10px; margin:8px 0 24px; }}
    .summary-item {{ min-height:76px; padding:13px 15px; border:1px solid var(--line); border-radius:8px; background:var(--card); box-shadow:var(--shadow); }}
    .summary-item span {{ display:block; color:var(--muted); font-size:12px; margin-bottom:7px; }}
    .summary-item strong {{ display:block; font-size:24px; line-height:1.1; }}
    .summary-item.pnl strong {{ font-size:32px; }} .summary-item.pnl.negative strong {{ color:var(--red); }} .summary-item.pnl.positive strong {{ color:var(--green); }}
    .summary-note {{ margin-top:8px; color:var(--muted); font-size:12px; line-height:1.45; }}
    .diagnosis {{ margin:0 0 18px; padding:12px 14px; border:1px solid #fed7aa; border-radius:8px; background:var(--amber-soft); color:#7c2d12; line-height:1.7; font-size:13px; }}
    .comparison-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:8px; background:var(--card); box-shadow:var(--shadow); }}
    .comparison-table {{ width:100%; min-width:760px; border-collapse:collapse; font-size:13px; }}
    .comparison-table th,.comparison-table td {{ padding:10px 12px; border-bottom:1px solid var(--soft-line); text-align:right; white-space:nowrap; }}
    .comparison-table th:first-child,.comparison-table td:first-child {{ text-align:left; }}
    .comparison-table tr:last-child td {{ border-bottom:0; }}
    .comparison-table th {{ color:var(--muted); background:#f8fafc; font-size:12px; font-weight:800; }}
    .comparison-table .active-strategy td:first-child {{ color:var(--blue); font-weight:900; }}
    .section-heading {{ display:flex; align-items:baseline; justify-content:space-between; gap:10px; margin:24px 0 12px; }}
    .section-heading h2 {{ margin:0; font-size:20px; }} .section-heading span {{ color:var(--muted); font-size:13px; }}
    .card-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
    .match-card {{ border:1px solid var(--line); border-radius:8px; background:var(--card); box-shadow:var(--shadow); padding:14px; }}
    .match-head {{ display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:start; margin-bottom:12px; }}
    .date {{ color:var(--muted); font-size:12px; margin-bottom:5px; }}
    .stage {{ display:inline-flex; align-items:center; min-height:22px; padding:0 7px; margin-left:6px; border-radius:999px; color:var(--amber); background:var(--amber-soft); border:1px solid #fed7aa; font-size:11px; font-weight:800; }}
    .match-title {{ margin:0; font-size:18px; line-height:1.25; overflow-wrap:anywhere; }}
    .status {{ display:inline-flex; align-items:center; min-height:28px; padding:0 9px; border-radius:999px; font-size:12px; font-weight:800; white-space:nowrap; }}
    .status.staked {{ color:var(--blue); background:var(--blue-soft); border:1px solid #c7ddff; }} .status.waiting {{ color:var(--amber); background:var(--amber-soft); border:1px solid #fed7aa; }} .status.positive {{ color:var(--green); background:var(--green-soft); border:1px solid #bfe8ce; }} .status.negative {{ color:var(--red); background:var(--red-soft); border:1px solid #fecaca; }}
    .bet-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }}
    .bet-column {{ min-width:0; padding:10px 9px; border:1px solid var(--soft-line); border-radius:8px; background:#fbfcfe; }} .bet-column.selected {{ background:var(--blue-soft); border-color:#bfdbfe; }} .bet-column.hit {{ background:var(--green-soft); border-color:#bfe8ce; }}
    .bet-label {{ display:flex; align-items:center; gap:6px; color:var(--muted); font-size:12px; line-height:1.2; margin-bottom:6px; }} .bet-team {{ color:var(--text); font-size:13px; font-weight:800; line-height:1.2; min-height:16px; margin-bottom:6px; overflow-wrap:anywhere; }} .marker {{ display:inline-grid; place-items:center; flex:0 0 22px; width:22px; height:22px; border-radius:999px; color:#fff; background:var(--blue); font-size:12px; font-weight:900; }} .amount {{ font-size:18px; line-height:1.1; font-weight:850; white-space:nowrap; }} .sp {{ margin-top:5px; color:var(--muted); font-size:13px; font-weight:700; }}
    .model-details {{ margin-top:12px; border-top:1px solid var(--soft-line); padding-top:10px; }} .model-details summary {{ cursor:pointer; color:var(--blue); font-size:13px; font-weight:800; list-style-position:inside; }} .detail-grid {{ display:grid; gap:8px; margin-top:10px; }} .detail-row {{ display:grid; grid-template-columns:78px repeat(3,minmax(0,1fr)); gap:6px; align-items:center; font-size:12px; }} .detail-label {{ color:var(--muted); font-weight:700; }} .detail-value {{ padding:6px; border-radius:6px; background:#f8fafc; text-align:right; white-space:nowrap; }} .detail-value b {{ display:block; color:var(--muted); font-size:10px; font-weight:700; text-align:left; margin-bottom:2px; }} .positive {{ color:var(--green); }} .negative {{ color:var(--red); }}
    .empty,.template-box {{ padding:20px; border:1px dashed var(--line); border-radius:8px; color:var(--muted); background:rgba(255,255,255,.65); }} .template-box summary {{ cursor:pointer; color:var(--blue); font-size:13px; font-weight:800; }} .template-list {{ margin-top:10px; display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }} .template-row {{ padding:9px 10px; border:1px solid var(--soft-line); border-radius:8px; background:#fff; color:var(--muted); font-size:12px; line-height:1.45; }} .template-row strong {{ display:block; color:var(--text); font-size:13px; margin-top:2px; overflow-wrap:anywhere; }}
    .disclaimer {{ margin-top:28px; padding-top:16px; border-top:1px solid var(--line); color:var(--muted); font-size:13px; line-height:1.7; }}
    @media (max-width:860px) {{ .summary {{ grid-template-columns:1fr 1fr; }} .summary-item.pnl {{ grid-column:1/-1; }} }} @media (max-width:760px) {{ main {{ width:min(100% - 20px,980px); }} .topbar {{ grid-template-columns:1fr; }} .github-link {{ justify-self:start; }} .card-grid {{ grid-template-columns:1fr; }} .template-list {{ grid-template-columns:1fr; }} }} @media (max-width:460px) {{ .summary {{ grid-template-columns:1fr; }} .bet-grid {{ grid-template-columns:1fr; }} .detail-row {{ grid-template-columns:1fr; }} .detail-value {{ text-align:left; }} }}
  </style>
</head>
<body>
  <main>
    <header class="topbar">
      <div>
        <h1>中国体彩 SP 世界杯预测</h1>
        <p class="subtitle">基于独立模型概率 + 中国体彩胜平负 SP 的复盘页面；默认 favorite-flat：每场完整 SP 固定买入模型第一选择，不构成投注建议</p>
      </div>
      <a class="github-link" href="{REPO_URL}">GitHub</a>
    </header>

    <section class="summary" aria-label="复盘汇总">
      <div class="summary-item pnl {pnl_class}"><span>累计盈亏</span><strong>{_fmt_signed_currency(summary['cumulative_pnl'])}</strong><div class="summary-note">{roi_text}<br>已下注结算 {summary.get('staked_settled_count', 0)} / 已结算 {summary.get('settled_count', 0)}</div></div>
      <div class="summary-item"><span>预测区比赛</span><strong>{summary.get('prediction_count', summary.get('pending_count', 0))}</strong></div>
      <div class="summary-item"><span>预测区实际下注</span><strong>{_fmt_currency(summary.get('prediction_stake_total', summary.get('today_stake_total', 0)))}</strong></div>
      <div class="summary-item"><span>完赛待补赛果</span><strong>{summary.get('awaiting_result_count', 0)}</strong></div>
      <div class="summary-item"><span>已结算复盘</span><strong>{summary['settled_count']}</strong></div>
      <div class="summary-item"><span>未来赛程</span><strong>{summary.get('future_count', 0)}</strong></div>
    </section>

    <div class="diagnosis">
      复盘说明：模型先独立给出 90 分钟主胜/平局/客胜概率，SP 只在预测之后用于比较、edge 和盈亏复盘。
      默认策略是 favorite-flat：每场完整 SP 固定用 100 元买入模型概率最高的一项；edge 仍然展示为赛后价值检查，但不让低概率高赔率冷门主导下注。
      这样既满足每场必须模拟下注，也避免把 100 元拆到三项造成结构性亏损；模型层不使用 SP，避免用小样本反向调参造成过拟合。
    </div>

    <section>
      <div class="section-heading"><h2>策略对比</h2><span>同一模型概率与 SP，只比较下注策略层；不反向训练模型</span></div>
      {_render_strategy_comparison(strategy_comparison, str(review.get("strategy", "favorite-flat")))}
    </section>

    <section>
      <div class="section-heading"><h2>{_html_escape(prediction_title)} {len(prediction_pending)}</h2><span>{_html_escape(prediction_note)}</span></div>
      {_render_card_grid(prediction_pending, empty_text="暂无已录入 SP 的待结算比赛。")}
    </section>

    <section>
      <div class="section-heading"><h2>历史复盘 {len(settled)}</h2><span>{_html_escape(history_subtitle)}</span></div>
      {_render_card_grid(settled, empty_text="暂无已结算比赛。")}
    </section>

    <section>
      <div class="section-heading"><h2>完赛待补赛果 {len(awaiting_result)}</h2><span>date &lt; today 且 90 分钟 actual 尚未填写；补 H/D/A 后进入历史复盘并结算盈亏</span></div>
      {_render_card_grid(awaiting_result, empty_text="暂无完赛待补赛果比赛。")}
    </section>

    <section>
      <div class="section-heading"><h2>未来赛程 {len(future_pending)}</h2><span>顶部只展示最近一个可预测比赛日；后续日期才进入未来赛程，默认折叠</span></div>
      {_render_collapsed_card_section(future_pending, empty_text="暂无已录入 SP 的未来赛程。", summary_text=f"展开查看 {len(future_pending)} 场未来赛程")}
    </section>

    <section>
      <div class="section-heading"><h2>待录入赛程模板 {len(template_pending)}</h2><span>未抓到或未录入 SP 的赛程统一折叠；公开 SP 行进入预测区</span></div>
      {_render_template_section(template_pending)}
    </section>

    <footer class="disclaimer">
      本页面仅用于模型复盘和学习，不构成投注建议。中国体彩 SP 可由用户手动录入，也可从公开展示页导入后复核；空白 SP 表示待录入。
      actual 必须使用 90 分钟含伤停补时、不含加时赛和点球的赛果。SP 和市场赔率只用于预测后的比较与复盘，不作为模型输入。
      模型概率不是保证，历史盈亏不能证明长期存在 edge。
    </footer>
  </main>
</body>
</html>
"""


def write_html(review: Mapping[str, object], output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html_text = "\n".join(line.rstrip() for line in render_html(review).splitlines()) + "\n"
    output_path.write_text(html_text, encoding="utf-8")
    return output_path


def _parse_row(raw: Mapping[str, str], line_no: int) -> dict:
    date_text = (raw.get("date") or "").strip()
    home = (raw.get("home") or "").strip()
    away = (raw.get("away") or "").strip()
    if not date_text:
        raise ValueError(f"line {line_no}: date is required")
    return {
        "date": date_text,
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


def _display_width(value: str) -> int:
    import unicodedata
    width = 0
    for char in value:
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _pad(value: str, width: int) -> str:
    return value + " " * (width - _display_width(value))


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
        return datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _prediction_title(review: Mapping[str, object]) -> str:
    prediction_date_text = review.get("current_prediction_date")
    review_date_text = review.get("review_date")
    if not prediction_date_text:
        return "今日预测"
    if prediction_date_text == review_date_text:
        return "今日预测"
    return f"下一比赛日预测：{prediction_date_text}"


def _prediction_note(review: Mapping[str, object]) -> str:
    prediction_date_text = review.get("current_prediction_date")
    if not prediction_date_text:
        return "展示最近一个有公开 SP 且尚未结算的可预测比赛日。"
    return (
        f"顶部展示最近一个可预测比赛日 {prediction_date_text}；"
        "后续日期进入未来赛程，每场完整 SP 默认买入模型第一选择。"
    )


def _match_date(match: Mapping[str, object]) -> date | None:
    try:
        return date.fromisoformat(str(match.get("date", "")))
    except ValueError:
        return None


def _current_prediction_date(matches: Sequence[Mapping[str, object]], review_date: date) -> date | None:
    dates = [
        match_date
        for match in matches
        if _should_show_pending_card(match)
        for match_date in [_match_date(match)]
        if match_date is not None and match_date >= review_date
    ]
    return min(dates) if dates else None


def _should_show_pending_card(match: Mapping[str, object]) -> bool:
    return match.get("status") == "pending" and not bool(match.get("unresolved")) and bool(match.get("has_any_sp"))


def _should_await_result(match: Mapping[str, object], review_date: date) -> bool:
    match_date = _match_date(match)
    return _should_show_pending_card(match) and match_date is not None and match_date < review_date


def _is_today_pending(match: Mapping[str, object], review_date: date) -> bool:
    match_date = _match_date(match)
    return _should_show_pending_card(match) and match_date == review_date


def _is_future_pending(
    match: Mapping[str, object],
    current_prediction_date: date | None,
    review_date: date,
) -> bool:
    match_date = _match_date(match)
    threshold = current_prediction_date or review_date
    return _should_show_pending_card(match) and match_date is not None and match_date > threshold


def _main_display_matches(review: Mapping[str, object]) -> list[Mapping[str, object]]:
    matches = list(review.get("matches", []))
    today_pending = list(review.get("today_pending") or review.get("display_pending") or [])
    settled = list(review.get("settled") or [m for m in matches if m.get("status") == "settled"])
    awaiting = list(review.get("awaiting_result") or [])
    return [*today_pending, *settled, *awaiting]


def _render_card_grid(matches: Iterable[Mapping[str, object]], empty_text: str) -> str:
    matches = list(matches)
    if not matches:
        return f'<div class="empty">{_html_escape(empty_text)}</div>'
    cards = "\n".join(_render_card(match) for match in matches)
    return f'<div class="card-grid">{cards}</div>'


def _render_collapsed_card_section(matches: Iterable[Mapping[str, object]], empty_text: str, summary_text: str) -> str:
    matches = list(matches)
    if not matches:
        return f'<div class="empty">{_html_escape(empty_text)}</div>'
    cards = "\n".join(_render_card(match) for match in matches)
    return f'<details class="template-box"><summary>{_html_escape(summary_text)}</summary><div class="card-grid">{cards}</div></details>'


def _render_template_section(matches: Iterable[Mapping[str, object]]) -> str:
    matches = list(matches)
    if not matches:
        return '<div class="empty">暂无待录入模板。</div>'
    rows = "\n".join(_render_template_row(match) for match in matches)
    return f'<details class="template-box"><summary>展开查看 {len(matches)} 场待录入模板</summary><div class="template-list">{rows}</div></details>'


def _render_strategy_comparison(comparison: Sequence[Mapping[str, object]], active: str) -> str:
    if not comparison:
        return '<div class="empty">暂无策略对比数据。</div>'
    rows = "\n".join(_render_strategy_row(row, active) for row in comparison)
    return f"""
      <div class="comparison-wrap">
        <table class="comparison-table">
          <thead>
            <tr>
              <th>策略</th>
              <th>已下注结算</th>
              <th>总投入</th>
              <th>累计盈亏</th>
              <th>ROI</th>
              <th>盈利下注率</th>
              <th>最大盈利</th>
              <th>最大亏损</th>
            </tr>
          </thead>
          <tbody>
            {rows}
          </tbody>
        </table>
      </div>
"""


def _render_strategy_row(row: Mapping[str, object], active: str) -> str:
    strategy = str(row["strategy"])
    cls = ' class="active-strategy"' if strategy == active else ""
    roi = row.get("roi")
    profitable_rate = row.get("profitable_rate")
    pnl = row.get("cumulative_pnl")
    max_profit = row.get("max_profit")
    max_loss = row.get("max_loss")
    return f"""
            <tr{cls}>
              <td>{_html_escape(strategy)}</td>
              <td>{int(row.get("staked_settled_count", 0))} / {int(row.get("settled_count", 0))}</td>
              <td>{_fmt_currency(float(row.get("total_stake", 0.0)))}</td>
              <td class="{_value_class(float(pnl or 0.0))}">{_fmt_signed_currency(float(pnl or 0.0))}</td>
              <td>{_fmt_pct(None if roi is None else float(roi))}</td>
              <td>{_fmt_pct(None if profitable_rate is None else float(profitable_rate))}</td>
              <td class="{_value_class(None if max_profit is None else float(max_profit))}">{_fmt_signed_currency(None if max_profit is None else float(max_profit))}</td>
              <td class="{_value_class(None if max_loss is None else float(max_loss))}">{_fmt_signed_currency(None if max_loss is None else float(max_loss))}</td>
            </tr>"""


def _render_template_row(match: Mapping[str, object]) -> str:
    return f'<div class="template-row">{_html_escape(match["date"])} · {_html_escape(match["stage"])}<strong>{_html_escape(_match_title(match))}</strong></div>'


def _render_card(match: Mapping[str, object]) -> str:
    if match["status"] == "pending":
        if match["unresolved"]:
            status = '<span class="status waiting">对阵待定</span>'
        elif match["stake_status"] == "待录入SP":
            status = '<span class="status waiting">待录入SP</span>'
        elif match.get("needs_result"):
            status = '<span class="status waiting">待赛果</span>'
        elif int(match["stake_total"]) > 0 and match.get("is_today_pending"):
            status = '<span class="status staked">今日已模拟下注</span>'
        elif match.get("is_today_pending"):
            status = '<span class="status waiting">今日观望</span>'
        elif int(match["stake_total"]) > 0:
            status = '<span class="status staked">待开奖</span>'
        else:
            status = '<span class="status waiting">观望</span>'
    else:
        if int(match["stake_total"]) <= 0:
            status = f'<span class="status waiting">实际 {match["actual_label"]}，观望未下注</span>'
        else:
            pnl = float(match["pnl"])
            status = f'<span class="status {_value_class(pnl)}">实际 {match["actual_label"]} {_fmt_signed_currency(pnl)}</span>'

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
    if match.get("selected_outcome") == outcome:
        classes.append("selected")
    if match["actual_outcome"] == outcome:
        classes.append("hit")
    class_attr = "" if not classes else " " + " ".join(classes)

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
              <div class="sp">{_fmt_sp(match['sp'][outcome])}</div>
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


def _render_detail_row(label: str, values: Mapping[str, float | None] | None, formatter: Callable[[float | None], str], signed: bool = False) -> str:
    cells = []
    for outcome in OUTCOMES:
        value = None if values is None else values[outcome]
        cls = f" {_value_class(value)}" if signed and value is not None else ""
        cells.append(f'<div class="detail-value{cls}"><b>{OUTCOME_LABELS[outcome]}</b>{formatter(value)}</div>')
    return f'<div class="detail-row"><div class="detail-label">{label}</div>{"".join(cells)}</div>'
