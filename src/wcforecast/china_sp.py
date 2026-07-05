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
OUTCOME_LABELS = {"home": "涓昏儨", "draw": "骞冲眬", "away": "瀹㈣儨"}
BET_LABELS = {"home": "涓昏儨涓嬫敞", "draw": "骞冲眬涓嬫敞", "away": "瀹㈣儨涓嬫敞"}
OUTCOME_MARKERS = {"home": "涓?, "draw": "骞?, "away": "瀹?}
ACTUAL_TO_OUTCOME = {"H": "home", "D": "draw", "A": "away"}
ACTUAL_LABELS = {"H": "涓昏儨", "D": "骞冲眬", "A": "瀹㈣儨", None: "寰呭紑濂?}
DEFAULT_STAGE = "鏈爣娉ㄨ禌娈?
TBD_NAMES = {"", "TBD", "寰呭畾", "寰呭畾鐞冮槦", "鏈畾", "To be decided"}
REPO_URL = "https://github.com/greenhand011/worldcup-forecast-china-sp"
STRATEGIES = {"favorite-flat", "edge-flat", "prob-split", "kelly"}

TEAM_ZH = {
    "Algeria": "闃垮皵鍙婂埄浜?,
    "Argentina": "闃挎牴寤?,
    "Australia": "婢冲ぇ鍒╀簹",
    "Austria": "濂ュ湴鍒?,
    "Belgium": "姣斿埄鏃?,
    "Bosnia and Herzegovina": "娉㈤粦",
    "Brazil": "宸磋タ",
    "Canada": "鍔犳嬁澶?,
    "Cape Verde": "浣涘緱瑙?,
    "Colombia": "鍝ヤ鸡姣斾簹",
    "Croatia": "鍏嬬綏鍦颁簹",
    "Cura莽ao": "搴撴媺绱?,
    "Czech Republic": "鎹峰厠",
    "DR Congo": "姘戜富鍒氭灉",
    "Ecuador": "鍘勭摐澶氬皵",
    "Egypt": "鍩冨強",
    "England": "鑻辨牸鍏?,
    "France": "娉曞浗",
    "Germany": "寰峰浗",
    "Ghana": "鍔犵撼",
    "Haiti": "娴峰湴",
    "Iran": "浼婃湕",
    "Iraq": "浼婃媺鍏?,
    "Ivory Coast": "绉戠壒杩摝",
    "Japan": "鏃ユ湰",
    "Jordan": "绾︽棪",
    "Mexico": "澧ㄨタ鍝?,
    "Morocco": "鎽╂礇鍝?,
    "Netherlands": "鑽峰叞",
    "New Zealand": "鏂拌タ鍏?,
    "Norway": "鎸▉",
    "Panama": "宸存嬁椹?,
    "Paraguay": "宸存媺鍦?,
    "Portugal": "钁¤悇鐗?,
    "Qatar": "鍗″灏?,
    "Saudi Arabia": "娌欑壒",
    "Scotland": "鑻忔牸鍏?,
    "Senegal": "濉炲唴鍔犲皵",
    "South Africa": "鍗楅潪",
    "South Korea": "闊╁浗",
    "Spain": "瑗跨彮鐗?,
    "Sweden": "鐟炲吀",
    "Switzerland": "鐟炲＋",
    "Tunisia": "绐佸凹鏂?,
    "Turkey": "鍦熻€冲叾",
    "United States": "缇庡浗",
    "Uruguay": "涔屾媺鍦?,
    "Uzbekistan": "涔屽吂鍒厠鏂潶",
    "TBD": "寰呭畾",
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
        edge_text = f"锛宔dge {_fmt_edge(float(edge[favorite]))}"
    return {outcome: stake if outcome == favorite else 0 for outcome in OUTCOMES}, favorite, (
        f"favorite-flat锛氫拱鍏ユā鍨嬬涓€閫夋嫨{OUTCOME_LABELS[favorite]}锛?
        f"妯″瀷姒傜巼 {_fmt_pct(float(probabilities[favorite]))}{edge_text}"
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
        return _zero_allocation(), None, "寰呭綍鍏P"

    best = max(OUTCOMES, key=lambda outcome: float(edge[outcome]))
    best_edge = float(edge[best])
    if best_edge <= float(min_edge):
        return _zero_allocation(), None, f"鏃犳edge锛岃鏈涳紙鏈€浣?{OUTCOME_LABELS[best]} {_fmt_edge(best_edge)}锛?

    if probabilities is not None:
        model_favorite = max(OUTCOMES, key=lambda outcome: float(probabilities[outcome]))
        best_probability = float(probabilities[best])
        if require_model_favorite and best != model_favorite:
            return _zero_allocation(), None, (
                f"瑙傛湜锛氭渶浣砮dge涓簕OUTCOME_LABELS[best]} {_fmt_edge(best_edge)}锛?
                f"浣嗘ā鍨嬬涓€閫夋嫨鏄瘂OUTCOME_LABELS[model_favorite]}"
            )
        if best_probability < float(min_probability):
            return _zero_allocation(), None, (
                f"瑙傛湜锛歿OUTCOME_LABELS[best]} edge {_fmt_edge(best_edge)}锛?
                f"浣嗘ā鍨嬫鐜囦粎 {_fmt_pct(best_probability)}锛屼綆浜庣ǔ鍋ラ槇鍊?{_fmt_pct(min_probability)}"
            )

    stake = _round_to_unit(bankroll, unit)
    stake = min(stake, bankroll)
    if stake <= 0:
        return _zero_allocation(), None, "edge杩囬槇鍊间絾涓嬫敞棰濅綆浜庢渶灏忓崟浣嶏紝瑙傛湜"
    return {outcome: stake if outcome == best else 0 for outcome in OUTCOMES}, best, (
        f"edge-flat锛氫拱鍏OUTCOME_LABELS[best]}锛宔dge {_fmt_edge(best_edge)}"
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
        return _zero_allocation(), None, "寰呭綍鍏P"

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
        return _zero_allocation(), None, f"鏃犳edge锛岃鏈涳紙鏈€浣?{OUTCOME_LABELS[best]} {_fmt_edge(best_edge)}锛?

    edge, best, full_kelly = max(candidates, key=lambda item: item[0])
    raw_stake = bankroll * min(max_stake_fraction, kelly_fraction * full_kelly)
    stake = _round_to_unit(raw_stake, unit)
    stake = max(0, min(stake, bankroll))
    if stake <= 0:
        return _zero_allocation(), None, "Kelly涓嬫敞棰濅綆浜庢渶灏忓崟浣嶏紝瑙傛湜"
    return {outcome: stake if outcome == best else 0 for outcome in OUTCOMES}, best, (
        f"kelly {kelly_fraction:g}x锛氫拱鍏OUTCOME_LABELS[best]}锛宔dge {_fmt_edge(edge)}锛宖ull Kelly {full_kelly * 100:.1f}%"
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
        stake_status = "瀵归樀寰呭畾"
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
                stake_status = "姒傜巼鎷嗗垎澶嶇洏锛堥潪鎺ㄨ崘涓嬫敞绛栫暐锛?
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
            stake_status = "寰呭綍鍏P"

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
    matches = [
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
    settled = sorted([m for m in matches if m["status"] == "settled"], key=lambda m: _match_date(m) or date.min, reverse=True)
    raw_pending = [m for m in matches if m["status"] == "pending"]
    today_pending = sorted([m for m in raw_pending if _is_today_pending(m, review_date)], key=lambda m: _match_date(m) or date.min, reverse=True)
    awaiting_result = sorted([m for m in raw_pending if _should_await_result(m, review_date)], key=lambda m: _match_date(m) or date.min, reverse=True)
    future_pending = sorted([m for m in raw_pending if _is_future_pending(m, review_date)], key=lambda m: _match_date(m) or date.max)
    template_pending = [
        m for m in raw_pending
        if m not in today_pending and m not in awaiting_result and m not in future_pending
    ]
    staked_settled = [m for m in settled if int(m["stake_total"]) > 0]
    profitable = [m for m in staked_settled if float(m["pnl"]) > 0]
    cumulative_pnl = sum(float(m["pnl"] or 0.0) for m in settled)
    history_stake_total = sum(float(m["stake_total"]) for m in staked_settled)
    hit_rate = (len(profitable) / len(staked_settled)) if staked_settled else None
    roi = (cumulative_pnl / history_stake_total) if history_stake_total else None
    today_stake_total = sum(int(m["stake_total"]) for m in today_pending)
    for match in matches:
        match["needs_result"] = _should_await_result(match, review_date)
        match["is_today_pending"] = _is_today_pending(match, review_date)
        match["is_future_pending"] = _is_future_pending(match, review_date)
    return {
        "input": str(input_path),
        "bankroll": int(bankroll),
        "unit": int(unit),
        "min_edge": float(min_edge),
        "strategy": strategy,
        "kelly_fraction": float(kelly_fraction),
        "max_stake_fraction": float(max_stake_fraction),
        "matches": matches,
        "today_pending": today_pending,
        "display_pending": today_pending,
        "awaiting_result": awaiting_result,
        "future_pending": future_pending,
        "template_pending": template_pending,
        "settled": settled,
        "summary": {
            "cumulative_pnl": cumulative_pnl,
            "settled_count": len(settled),
            "pending_count": len(today_pending),
            "today_pending_count": len(today_pending),
            "today_stake_total": today_stake_total,
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
    headers = ["璧涙", "瀵归樀", "妯″瀷鑳?骞?璐?", "浣撳僵SP鑳?骞?璐?, "鍒嗛厤鑳?骞?璐?, "瀹為檯", "鐩堜簭", "绛栫暐"]
    rows = []
    for match in _main_display_matches(review):
        rows.append([
            match["stage"],
            _match_title(match),
            _fmt_pct_triplet(match["probabilities"]),
            _fmt_sp_triplet(match["sp"]),
            _fmt_alloc_triplet(match["allocation"]),
            match["actual_label"],
            "寰呭紑濂? if match["pnl"] is None else _fmt_console_currency(match["pnl"]),
            match["stake_status"],
        ])
    widths = [max(_display_width(str(row[i])) for row in ([headers] + rows)) for i in range(len(headers))]
    line = "  ".join(_pad(headers[i], widths[i]) for i in range(len(headers)))
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = ["  ".join(_pad(str(row[i]), widths[i]) for i in range(len(headers))) for row in rows]
    summary = review["summary"]
    footer = (
        f"鍚堣锛氱瓥鐣?{review.get('strategy', 'favorite-flat')}锛?
        f"浠婃棩棰勬祴 {summary.get('today_pending_count', 0)} 鍦猴紝"
        f"浠婃棩瀹為檯涓嬫敞 {_fmt_console_currency(summary.get('today_stake_total', 0))}锛?
        f"宸蹭笅娉ㄧ粨绠?{summary.get('staked_settled_count', 0)}/{summary.get('settled_count', 0)}锛?
        f"瀹岃禌寰呰ˉ璧涙灉 {summary.get('awaiting_result_count', 0)} 鍦猴紝"
        f"鏈潵璧涚▼ {summary.get('future_count', 0)} 鍦猴紝"
        f"绱鐩堜簭 {_fmt_console_currency(summary['cumulative_pnl'])}"
    )
    return _console_safe("\n".join(["涓浗浣撳僵 SP 涓栫晫鏉鐩?, "", line, sep, *body, "", footer]))


def render_html(review: Mapping[str, object]) -> str:
    matches = list(review["matches"])
    today_pending = list(review.get("today_pending") or review.get("display_pending") or [])
    awaiting_result = sorted(list(review.get("awaiting_result") or []), key=lambda m: _match_date(m) or date.min, reverse=True)
    future_pending = sorted(list(review.get("future_pending") or []), key=lambda m: _match_date(m) or date.max)
    template_pending = list(review.get("template_pending") or [m for m in matches if m["status"] == "pending" and not _should_show_pending_card(m)])
    settled = sorted(list(review.get("settled") or [m for m in matches if m["status"] == "settled"]), key=lambda m: _match_date(m) or date.min, reverse=True)
    summary = review["summary"]
    hit_rate = summary.get("hit_rate")
    roi = summary.get("roi")
    history_stats = []
    history_stats.append(f"宸蹭笅娉ㄧ粨绠?{summary.get('staked_settled_count', 0)} / 宸茬粨绠?{summary.get('settled_count', 0)}")
    if hit_rate is not None:
        history_stats.append(f"鐩堝埄涓嬫敞鐜?{_fmt_pct(float(hit_rate))}")
    if roi is not None:
        history_stats.append(f"ROI {_fmt_pct(float(roi))}")
    history_stats.append(f"绱鐩堜簭 {_fmt_signed_currency(summary['cumulative_pnl'])}")
    history_subtitle = "锛?.join(history_stats)
    pnl_class = "positive" if float(summary["cumulative_pnl"]) >= 0 else "negative"
    roi_text = "ROI 鈥? if roi is None else f"ROI {_fmt_pct(float(roi))}"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>涓浗浣撳僵 SP 涓栫晫鏉娴?/title>
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
        <h1>涓浗浣撳僵 SP 涓栫晫鏉娴?/h1>
        <p class="subtitle">鍩轰簬鐙珛妯″瀷姒傜巼 + 涓浗浣撳僵鑳滃钩璐?SP 鐨勫鐩橀〉闈紱榛樿 favorite-flat锛氭瘡鍦哄畬鏁?SP 鍥哄畾涔板叆妯″瀷绗竴閫夋嫨锛屼笉鏋勬垚鎶曟敞寤鸿</p>
      </div>
      <a class="github-link" href="{REPO_URL}">GitHub</a>
    </header>

    <section class="summary" aria-label="澶嶇洏姹囨€?>
      <div class="summary-item pnl {pnl_class}"><span>绱鐩堜簭</span><strong>{_fmt_signed_currency(summary['cumulative_pnl'])}</strong><div class="summary-note">{roi_text}<br>宸蹭笅娉ㄧ粨绠?{summary.get('staked_settled_count', 0)} / 宸茬粨绠?{summary.get('settled_count', 0)}</div></div>
      <div class="summary-item"><span>浠婃棩棰勬祴</span><strong>{summary.get('today_pending_count', summary.get('pending_count', 0))}</strong></div>
      <div class="summary-item"><span>浠婃棩瀹為檯涓嬫敞</span><strong>{_fmt_currency(summary.get('today_stake_total', 0))}</strong></div>
      <div class="summary-item"><span>瀹岃禌寰呰ˉ璧涙灉</span><strong>{summary.get('awaiting_result_count', 0)}</strong></div>
      <div class="summary-item"><span>宸茬粨绠楀鐩?/span><strong>{summary['settled_count']}</strong></div>
      <div class="summary-item"><span>鏈潵璧涚▼</span><strong>{summary.get('future_count', 0)}</strong></div>
    </section>

    <div class="diagnosis">
      澶嶇洏璇存槑锛氭ā鍨嬪厛鐙珛缁欏嚭 90 鍒嗛挓涓昏儨/骞冲眬/瀹㈣儨姒傜巼锛孲P 鍙湪棰勬祴涔嬪悗鐢ㄤ簬姣旇緝銆乪dge 鍜岀泩浜忓鐩樸€?      榛樿绛栫暐鏄?favorite-flat锛氭瘡鍦哄畬鏁?SP 鍥哄畾鐢?100 鍏冧拱鍏ユā鍨嬫鐜囨渶楂樼殑涓€椤癸紱edge 浠嶇劧灞曠ず涓鸿禌鍚庝环鍊兼鏌ワ紝浣嗕笉璁╀綆姒傜巼楂樿禂鐜囧喎闂ㄤ富瀵间笅娉ㄣ€?      杩欐牱鏃㈡弧瓒虫瘡鍦哄繀椤绘ā鎷熶笅娉紝涔熼伩鍏嶆妸 100 鍏冩媶鍒颁笁椤归€犳垚缁撴瀯鎬т簭鎹燂紱妯″瀷灞備笉浣跨敤 SP锛岄伩鍏嶇敤灏忔牱鏈弽鍚戣皟鍙傞€犳垚杩囨嫙鍚堛€?    </div>

    <section>
      <div class="section-heading"><h2>浠婃棩棰勬祴 {len(today_pending)}</h2><span>date = today 涓?actual 涓虹┖锛涙瘡鍦哄畬鏁?SP 榛樿涔板叆妯″瀷绗竴閫夋嫨</span></div>
      {_render_card_grid(today_pending, empty_text="浠婃棩鏆傛棤宸插綍鍏?SP 鐨勫緟缁撶畻姣旇禌銆?)}
    </section>

    <section>
      <div class="section-heading"><h2>鍘嗗彶澶嶇洏 {len(settled)}</h2><span>{_html_escape(history_subtitle)}</span></div>
      {_render_card_grid(settled, empty_text="鏆傛棤宸茬粨绠楁瘮璧涖€?)}
    </section>

    <section>
      <div class="section-heading"><h2>瀹岃禌寰呰ˉ璧涙灉 {len(awaiting_result)}</h2><span>date &lt; today 涓?90 鍒嗛挓 actual 灏氭湭濉啓锛涜ˉ H/D/A 鍚庤繘鍏ュ巻鍙插鐩樺苟缁撶畻鐩堜簭</span></div>
      {_render_card_grid(awaiting_result, empty_text="鏆傛棤瀹岃禌寰呰ˉ璧涙灉姣旇禌銆?)}
    </section>

    <section>
      <div class="section-heading"><h2>鏈潵璧涚▼ {len(future_pending)}</h2><span>date &gt; today 涓?actual 涓虹┖锛涢粯璁ゆ姌鍙狅紝涓嶈鍏ヤ粖鏃ュ疄闄呬笅娉?/span></div>
      {_render_collapsed_card_section(future_pending, empty_text="鏆傛棤宸插綍鍏?SP 鐨勬湭鏉ヨ禌绋嬨€?, summary_text=f"灞曞紑鏌ョ湅 {len(future_pending)} 鍦烘湭鏉ヨ禌绋?)}
    </section>

    <section>
      <div class="section-heading"><h2>寰呭綍鍏ヨ禌绋嬫ā鏉?{len(template_pending)}</h2><span>鏈姄鍒版垨鏈綍鍏?SP 鐨勮禌绋嬬粺涓€鎶樺彔锛涘叕寮€ SP 琛岃繘鍏ラ娴嬪尯</span></div>
      {_render_template_section(template_pending)}
    </section>

    <footer class="disclaimer">
      鏈〉闈粎鐢ㄤ簬妯″瀷澶嶇洏鍜屽涔狅紝涓嶆瀯鎴愭姇娉ㄥ缓璁€備腑鍥戒綋褰?SP 鍙敱鐢ㄦ埛鎵嬪姩褰曞叆锛屼篃鍙粠鍏紑灞曠ず椤靛鍏ュ悗澶嶆牳锛涚┖鐧?SP 琛ㄧず寰呭綍鍏ャ€?      actual 蹇呴』浣跨敤 90 鍒嗛挓鍚激鍋滆ˉ鏃躲€佷笉鍚姞鏃惰禌鍜岀偣鐞冪殑璧涙灉銆係P 鍜屽競鍦鸿禂鐜囧彧鐢ㄤ簬棰勬祴鍚庣殑姣旇緝涓庡鐩橈紝涓嶄綔涓烘ā鍨嬭緭鍏ャ€?      妯″瀷姒傜巼涓嶆槸淇濊瘉锛屽巻鍙茬泩浜忎笉鑳借瘉鏄庨暱鏈熷瓨鍦?edge銆?    </footer>
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
    return "鈥? if value is None else f"{value * 100:.1f}%"


def _fmt_edge(value: float | None) -> str:
    if value is None:
        return "寰呭綍鍏P"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.1f}%"


def _fmt_currency(value: float | None) -> str:
    return "鈥? if value is None else f"楼{int(round(value)):,.0f}"


def _fmt_signed_currency(value: float | None) -> str:
    if value is None:
        return "寰呭紑濂?
    value = 0.0 if abs(float(value)) < 0.05 else float(value)
    sign = "+" if value >= 0 else "-"
    return f"{sign}楼{abs(value):,.1f}"


def _fmt_console_currency(value: float | None) -> str:
    if value is None:
        return "寰呭紑濂?
    value = 0.0 if abs(float(value)) < 0.05 else float(value)
    sign = "+" if value >= 0 else "-"
    return f"{sign}{abs(value):,.1f} 鍏?


def _fmt_sp(value: float | None) -> str:
    return "寰呭綍鍏P" if value is None else f"@{value:.2f}"


def _fmt_odds(value: float | None) -> str:
    return "鈥? if value is None else f"{value:.2f}"


def _fmt_triplet(values: Mapping[str, float | None] | None, formatter: Callable[[float | None], str]) -> str:
    if values is None:
        return "鈥?鈥?鈥?
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
    return "骞冲眬"


def _parse_review_date(value: str | date | None) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _match_date(match: Mapping[str, object]) -> date | None:
    try:
        return date.fromisoformat(str(match.get("date", "")))
    except ValueError:
        return None


def _should_show_pending_card(match: Mapping[str, object]) -> bool:
    return match.get("status") == "pending" and not bool(match.get("unresolved")) and bool(match.get("has_any_sp"))


def _should_await_result(match: Mapping[str, object], review_date: date) -> bool:
    match_date = _match_date(match)
    return _should_show_pending_card(match) and match_date is not None and match_date < review_date


def _is_today_pending(match: Mapping[str, object], review_date: date) -> bool:
    match_date = _match_date(match)
    return _should_show_pending_card(match) and match_date == review_date


def _is_future_pending(match: Mapping[str, object], review_date: date) -> bool:
    match_date = _match_date(match)
    return _should_show_pending_card(match) and match_date is not None and match_date > review_date


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
        return '<div class="empty">鏆傛棤寰呭綍鍏ユā鏉裤€?/div>'
    rows = "\n".join(_render_template_row(match) for match in matches)
    return f'<details class="template-box"><summary>灞曞紑鏌ョ湅 {len(matches)} 鍦哄緟褰曞叆妯℃澘</summary><div class="template-list">{rows}</div></details>'


def _render_template_row(match: Mapping[str, object]) -> str:
    return f'<div class="template-row">{_html_escape(match["date"])} 路 {_html_escape(match["stage"])}<strong>{_html_escape(_match_title(match))}</strong></div>'


def _render_card(match: Mapping[str, object]) -> str:
    if match["status"] == "pending":
        if match["unresolved"]:
            status = '<span class="status waiting">瀵归樀寰呭畾</span>'
        elif match["stake_status"] == "寰呭綍鍏P":
            status = '<span class="status waiting">寰呭綍鍏P</span>'
        elif match.get("needs_result"):
            status = '<span class="status waiting">寰呰禌鏋?/span>'
        elif int(match["stake_total"]) > 0 and match.get("is_today_pending"):
            status = '<span class="status staked">浠婃棩宸叉ā鎷熶笅娉?/span>'
        elif match.get("is_today_pending"):
            status = '<span class="status waiting">浠婃棩瑙傛湜</span>'
        elif int(match["stake_total"]) > 0:
            status = '<span class="status staked">寰呭紑濂?/span>'
        else:
            status = '<span class="status waiting">瑙傛湜</span>'
    else:
        if int(match["stake_total"]) <= 0:
            status = f'<span class="status waiting">瀹為檯 {match["actual_label"]}锛岃鏈涙湭涓嬫敞</span>'
        else:
            pnl = float(match["pnl"])
            status = f'<span class="status {_value_class(pnl)}">瀹為檯 {match["actual_label"]} {_fmt_signed_currency(pnl)}</span>'

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
        label = f"{OUTCOME_LABELS[outcome]}姒傜巼"
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
            <summary>鏌ョ湅妯″瀷缁嗚妭</summary>
            <div class="detail-grid">
              {_render_detail_row("妯″瀷姒傜巼", match["probabilities"], _fmt_pct)}
              {_render_detail_row("鍏厑璧旂巼", match["fair_odds"], _fmt_odds)}
              {_render_detail_row("Edge", match["edge"], _fmt_edge, signed=True)}
              <div class="detail-row"><div class="detail-label">绛栫暐</div><div class="detail-value" style="grid-column: span 3; text-align:left;">{_html_escape(match["stake_status"])}</div></div>
            </div>
          </details>"""


def _render_detail_row(label: str, values: Mapping[str, float | None] | None, formatter: Callable[[float | None], str], signed: bool = False) -> str:
    cells = []
    for outcome in OUTCOMES:
        value = None if values is None else values[outcome]
        cls = f" {_value_class(value)}" if signed and value is not None else ""
        cells.append(f'<div class="detail-value{cls}"><b>{OUTCOME_LABELS[outcome]}</b>{formatter(value)}</div>')
    return f'<div class="detail-row"><div class="detail-label">{label}</div>{"".join(cells)}</div>'

