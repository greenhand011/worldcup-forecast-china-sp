"""Fetch public China Sports Lottery SP display data for review.

This module only reads publicly visible 1X2 SP display data and writes it into
the local review CSV. It never logs in, places bets, purchases lottery tickets,
or feeds SP values back into the forecasting model.
"""
from __future__ import annotations

import csv
import html
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from requests import RequestException

DEFAULT_SOURCE_URL = "https://trade.500.com/jczq/"

CHINESE_TEAM_MAP = {
    "阿尔及利亚": "Algeria",
    "阿根廷": "Argentina",
    "澳大利亚": "Australia",
    "奥地利": "Austria",
    "比利时": "Belgium",
    "波黑": "Bosnia and Herzegovina",
    "巴西": "Brazil",
    "加拿大": "Canada",
    "佛得角": "Cape Verde",
    "哥伦比亚": "Colombia",
    "克罗地亚": "Croatia",
    "库拉索": "Curaçao",
    "捷克": "Czech Republic",
    "刚果民主共和国": "DR Congo",
    "刚果（金）": "DR Congo",
    "民主刚果": "DR Congo",
    "埃及": "Egypt",
    "英格兰": "England",
    "厄瓜多尔": "Ecuador",
    "法国": "France",
    "德国": "Germany",
    "加纳": "Ghana",
    "海地": "Haiti",
    "伊朗": "Iran",
    "伊拉克": "Iraq",
    "科特迪瓦": "Ivory Coast",
    "日本": "Japan",
    "约旦": "Jordan",
    "墨西哥": "Mexico",
    "摩洛哥": "Morocco",
    "荷兰": "Netherlands",
    "新西兰": "New Zealand",
    "挪威": "Norway",
    "巴拿马": "Panama",
    "巴拉圭": "Paraguay",
    "葡萄牙": "Portugal",
    "卡塔尔": "Qatar",
    "沙特": "Saudi Arabia",
    "沙特阿拉伯": "Saudi Arabia",
    "苏格兰": "Scotland",
    "塞内加尔": "Senegal",
    "南非": "South Africa",
    "韩国": "South Korea",
    "西班牙": "Spain",
    "瑞典": "Sweden",
    "瑞士": "Switzerland",
    "突尼斯": "Tunisia",
    "土耳其": "Turkey",
    "乌拉圭": "Uruguay",
    "美国": "United States",
    "乌兹别克": "Uzbekistan",
    "乌兹别克斯坦": "Uzbekistan",
}

TEAM_ALIASES = {
    "USA": "United States",
    "U.S.A.": "United States",
    "US": "United States",
    "U.S.": "United States",
}

CSV_FIELDS = ("date", "stage", "home", "away", "neutral", "sp_home", "sp_draw", "sp_away", "actual")
RESULT_FIELDS = ("date", "home", "away", "home_score_90", "away_score_90", "actual")


@dataclass(frozen=True)
class FetchedSPMatch:
    date: str
    time: str
    stage: str
    home: str
    away: str
    sp_home: float
    sp_draw: float
    sp_away: float
    match_num: str
    source: str

    def to_csv_row(self) -> dict[str, str]:
        return {
            "date": self.date,
            "stage": self.stage,
            "home": self.home,
            "away": self.away,
            "neutral": "true",
            "sp_home": f"{self.sp_home:.2f}",
            "sp_draw": f"{self.sp_draw:.2f}",
            "sp_away": f"{self.sp_away:.2f}",
            "actual": "",
        }


def fetch_public_sp(source_url: str = DEFAULT_SOURCE_URL, timeout: int = 20) -> list[FetchedSPMatch]:
    """Fetch publicly visible World Cup non-handicap 1X2 SP rows."""
    last_error: RequestException | None = None
    for _ in range(3):
        try:
            response = requests.get(source_url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            return parse_public_sp_html(response.text, source_url=source_url)
        except RequestException as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return []


def fetch_public_sp_range(
    start_date: str | date,
    end_date: str | date,
    source_url: str = DEFAULT_SOURCE_URL,
    timeout: int = 20,
) -> list[FetchedSPMatch]:
    """Fetch publicly visible World Cup SP rows over an inclusive date range."""
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if end < start:
        raise ValueError("end date must be on or after start date")

    rows: list[FetchedSPMatch] = []
    current = start
    while current <= end:
        day_url = _url_with_date(source_url, current)
        try:
            rows.extend(fetch_public_sp(day_url, timeout=timeout))
        except RequestException:
            pass
        current += timedelta(days=1)
    return _dedupe_matches(rows)


def parse_public_sp_html(page: str, source_url: str = DEFAULT_SOURCE_URL) -> list[FetchedSPMatch]:
    """Parse a public football SP page and keep only World Cup nspf rows."""
    rows: list[FetchedSPMatch] = []
    for tr in re.findall(r'<tr class="[^"]*\bbet-tb-tr\b[^"]*".*?</tr>', page, flags=re.S):
        if _attr(tr, "data-simpleleague") != "世界杯":
            continue
        odds = _parse_nspf_odds(tr)
        if odds is None:
            continue
        home_cn = _attr(tr, "data-homesxname")
        away_cn = _attr(tr, "data-awaysxname")
        try:
            home = _map_team(home_cn)
            away = _map_team(away_cn)
        except ValueError:
            continue
        date = _attr(tr, "data-matchdate")
        rows.append(
            FetchedSPMatch(
                date=date,
                time=_attr(tr, "data-matchtime"),
                stage=_stage_for_date(date),
                home=home,
                away=away,
                sp_home=odds[0],
                sp_draw=odds[1],
                sp_away=odds[2],
                match_num=_attr(tr, "data-matchnum"),
                source=source_url,
            )
        )
    return rows


def merge_public_sp_into_csv(
    csv_path: str | Path,
    fetched: Iterable[FetchedSPMatch],
    source_url: str = DEFAULT_SOURCE_URL,
    fetched_at: datetime | None = None,
) -> int:
    """Merge fetched SP rows into the review CSV and return row count."""
    csv_path = Path(csv_path)
    fetched_rows = list(fetched)
    if not fetched_rows:
        return 0

    comments, rows = _read_review_csv(csv_path)
    keyed = {
        _row_key(row): row
        for row in rows
        if _row_key(row) not in {("", "", "")}
    }
    for match in fetched_rows:
        keyed[_row_key(match.to_csv_row())] = match.to_csv_row()

    source_comment = _source_comment(source_url, fetched_at or datetime.now())
    comments = [line for line in comments if "公开SP来源" not in line]
    comments.append(source_comment)

    fetched_keys = {_row_key(match.to_csv_row()) for match in fetched_rows}
    fetched_csv_rows = [keyed[key] for key in sorted(fetched_keys)]
    other_rows = [row for row in rows if _row_key(row) not in fetched_keys]
    all_rows = fetched_csv_rows + other_rows

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        for comment in comments:
            f.write(comment.rstrip() + "\n")
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(all_rows)
    return len(fetched_rows)


def merge_results_into_csv(csv_path: str | Path, results_path: str | Path) -> int:
    """Merge user-provided 90-minute results into the review CSV."""
    csv_path = Path(csv_path)
    results = _read_results_csv(Path(results_path))
    if not results:
        return 0

    comments, rows = _read_review_csv(csv_path)
    results_by_key = {
        _row_key(result): result
        for result in results
        if _row_key(result) not in {("", "", "")}
    }
    updated = 0
    for row in rows:
        result = results_by_key.get(_row_key(row))
        if result is None:
            continue
        actual = result.get("actual") or _actual_from_scores(result)
        if actual:
            row["actual"] = actual
            updated += 1

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        for comment in comments:
            f.write(comment.rstrip() + "\n")
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return updated


def _attr(fragment: str, name: str) -> str:
    match = re.search(fr'{name}="([^"]*)"', fragment)
    return html.unescape(match.group(1)) if match else ""


def _parse_nspf_odds(tr: str) -> tuple[float, float, float] | None:
    row = re.search(r'<div class="betbtn-row itm-rangB1">(.*?)</div>', tr, flags=re.S)
    if not row:
        return None
    found = dict(re.findall(r'data-type="nspf"[^>]*data-value="([310])"[^>]*data-sp="([0-9.]+)"', row.group(1)))
    if not {"3", "1", "0"}.issubset(found):
        return None
    return float(found["3"]), float(found["1"]), float(found["0"])


def _map_team(name: str) -> str:
    if name not in CHINESE_TEAM_MAP:
        raise ValueError(f"unmapped Chinese team name from SP source: {name}")
    return CHINESE_TEAM_MAP[name]


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _url_with_date(source_url: str, day: date) -> str:
    parts = urlsplit(source_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["date"] = day.isoformat()
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _dedupe_matches(rows: Iterable[FetchedSPMatch]) -> list[FetchedSPMatch]:
    keyed: dict[tuple[str, str, str], FetchedSPMatch] = {}
    for row in rows:
        keyed[(row.date, row.home, row.away)] = row
    return list(keyed.values())


def _stage_for_date(date_text: str) -> str:
    try:
        date = datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError:
        return "赛段待确认"
    if date.month == 6:
        return "小组赛（公开SP）"
    if date.month == 7 and date.day <= 8:
        return "1/8决赛（公开SP）"
    if date.month == 7 and date.day <= 11:
        return "1/4决赛（公开SP）"
    if date.month == 7 and date.day <= 15:
        return "半决赛（公开SP）"
    if date.month == 7 and date.day == 18:
        return "三四名决赛（公开SP）"
    if date.month == 7 and date.day >= 19:
        return "决赛（公开SP）"
    return "赛段待确认"


def _read_review_csv(csv_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    text = csv_path.read_text(encoding="utf-8-sig").splitlines()
    comments = [line for line in text if line.strip().startswith("#")]
    data_lines = [line for line in text if line.strip() and not line.strip().startswith("#")]
    if not data_lines:
        return comments, []
    reader = csv.DictReader(data_lines)
    rows = []
    for row in reader:
        rows.append({field: row.get(field, "") for field in CSV_FIELDS})
    return comments, rows


def _read_results_csv(path: Path) -> list[dict[str, str]]:
    data_lines = [
        line
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not data_lines:
        return []
    reader = csv.DictReader(data_lines)
    missing = [field for field in ("date", "home", "away") if field not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"result CSV is missing required field(s): {', '.join(missing)}")
    rows = []
    for raw in reader:
        row = {field: (raw.get(field) or "").strip() for field in RESULT_FIELDS}
        if not any(row.values()):
            continue
        row["home"] = _normalize_team(row["home"])
        row["away"] = _normalize_team(row["away"])
        actual = row.get("actual", "").upper()
        if actual and actual not in {"H", "D", "A"}:
            raise ValueError(f"actual must be H/D/A or blank for {row['home']} vs {row['away']}")
        row["actual"] = actual
        rows.append(row)
    return rows


def _normalize_team(team: str) -> str:
    return TEAM_ALIASES.get(team, CHINESE_TEAM_MAP.get(team, team))


def _actual_from_scores(row: dict[str, str]) -> str:
    home_score = row.get("home_score_90", "")
    away_score = row.get("away_score_90", "")
    if home_score == "" or away_score == "":
        return ""
    home_goals = int(home_score)
    away_goals = int(away_score)
    if home_goals > away_goals:
        return "H"
    if home_goals < away_goals:
        return "A"
    return "D"


def _row_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (row.get("date", ""), row.get("home", ""), row.get("away", ""))


def _source_comment(source_url: str, fetched_at: datetime) -> str:
    timestamp = fetched_at.strftime("%Y-%m-%d %H:%M:%S")
    return f"# 公开SP来源：{source_url}；抓取时间：{timestamp}；请赛前/赛后自行复核官方渠道。"
