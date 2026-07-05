"""Command-line interface: ``wcforecast {forecast,predict,validate,odds,china-sp-review}``."""
from __future__ import annotations

import argparse

from . import china_sp, china_sp_fetch, data, markets, predict
from . import model as model_mod
from . import ratings, simulate
from .teams import HOSTS, INDEX

CUTOFF_2026 = "2026-06-11"
CACHE_MODEL = data.CACHE / "model_2026.pkl"


def _build_2026_model(refit: bool = False):
    """Fit (or load) the 2026 forward model: FIFA+squad anchor, recency-weighted, DC."""
    results = data.load_results()
    fifa = data.load_fifa_snapshot()
    squad = data.load_squad_values()
    s = ratings.structural_index(CUTOFF_2026, results, fifa=fifa, squad=squad)
    if CACHE_MODEL.exists() and not refit:
        return model_mod.PoissonModel.load(CACHE_MODEL), s
    matches = data.load_matches(start="2006-01-01", cutoff=CUTOFF_2026)
    weights = ratings.recency_weights(matches, CUTOFF_2026)
    print(f"[fit] training on {len(matches)} matches (this takes ~30s)...")
    m = model_mod.fit(matches, s, weights=weights, dixon_coles=True)
    data.CACHE.mkdir(parents=True, exist_ok=True)
    m.save(CACHE_MODEL)
    return m, s


def cmd_forecast(args):
    m, s = _build_2026_model(args.refit)
    main = simulate.champion_probabilities(m, s, n_sims=args.sims)
    post = m.idata.posterior
    ka, kd = float(post["ka"].mean()), float(post["kd"].mean())
    mu, ha = float(post["mu"].mean()), float(post["home_adv"].mean())
    ind = simulate.monte_carlo(ka * s, kd * s, mu, ha, s, n_sims=args.sims, seed=11)
    ind_map = dict(zip(ind["team"], ind["champion"]))
    print(f"\n2026 World Cup — champion probability (Monte Carlo, {args.sims:,} sims)\n")
    print(f"  {'team':24}{'accuracy %':>12}{'independent %':>15}")
    print("  " + "-" * 51)
    for _, row in main.head(args.top).iterrows():
        print(f"  {row['team']:24}{row['champion']:>12.1f}{ind_map.get(row['team'], 0):>15.1f}")
    print("\n  accuracy = FIFA+squad anchor + data; independent = structural prior only "
          "(market-free).\n  See docs/FINDINGS.md for what is and isn't validated.")


def cmd_predict(args):
    m, _ = _build_2026_model(args.refit)
    home, away = args.home, args.away
    if home not in INDEX or away not in INDEX:
        raise SystemExit(f"unknown team(s); valid names e.g.: {', '.join(list(INDEX)[:6])} ...")
    hnn = 0.0 if args.neutral else (1.0 if home in HOSTS else 0.0)
    raw = m.match_probs(home, away, home_advantage=hnn)
    cal = predict.calibrate(raw)
    venue = "neutral" if hnn == 0.0 else f"{home} at home"
    print(f"\n{home} vs {away}  ({venue})")
    print(f"  {'':10}{'home':>8}{'draw':>8}{'away':>8}")
    print(f"  {'raw':10}{raw[0]:>8.2f}{raw[1]:>8.2f}{raw[2]:>8.2f}")
    print(f"  {'calibrated':10}{cal[0]:>8.2f}{cal[1]:>8.2f}{cal[2]:>8.2f}")


def cmd_validate(args):
    from . import validate
    df = validate.scorecard()
    print("\nOut-of-sample scorecard (locked test window 2024-01 → 2026-06)\n")
    print(df.to_string(index=False))
    print("\nLower log_loss/rps/brier = better. 'vs_elo' shows the paired-bootstrap 95% CI.")


def cmd_odds(args):
    games = markets.bookmaker_games(regions=args.regions)
    print(f"\nBookmaker consensus 1X2 (de-vigged) — {len(games)} upcoming matches\n")
    print(f"  {'kickoff (UTC)':20}{'match':40}{'books':>6}{'  home/draw/away':>18}")
    print("  " + "-" * 84)
    for g in games:
        ph, pd_, pa = g["p1x2"]
        ko = (g["kickoff"] or "").replace("T", " ").replace(":00Z", "")
        print(f"  {ko:20}{g['home_team'] + ' v ' + g['away_team']:40}{g['n_books']:>6}"
              f"   {ph:.2f}/{pd_:.2f}/{pa:.2f}")


def cmd_china_sp_review(args):
    m, _ = _build_2026_model(refit=False)
    review = china_sp.build_review(
        args.input,
        m,
        bankroll=args.bankroll,
        unit=args.unit,
        min_edge=args.min_edge,
        strategy=args.strategy,
        kelly_fraction=args.kelly_fraction,
        max_stake_fraction=args.max_stake_fraction,
    )
    output = china_sp.write_html(review, args.output)
    print()
    print(china_sp.format_console_table(review))
    print(f"\n已生成静态网页：{output}")


def cmd_china_sp_fetch(args):
    if args.start_date or args.end_date:
        if not (args.start_date and args.end_date):
            raise SystemExit("--start-date and --end-date must be used together")
        fetched = china_sp_fetch.fetch_public_sp_range(args.start_date, args.end_date, args.source_url)
    else:
        fetched = china_sp_fetch.fetch_public_sp(args.source_url)
    if not fetched:
        print("未从公开页面找到世界杯胜平负 SP。")
        return
    for match in fetched:
        print(
            f"{match.date} {match.time} {match.stage} "
            f"{match.home} vs {match.away} "
            f"SP {match.sp_home:.2f}/{match.sp_draw:.2f}/{match.sp_away:.2f}"
        )
    if args.dry_run:
        print(f"\n预览完成：找到 {len(fetched)} 场；未写入 CSV。")
        return
    count = china_sp_fetch.merge_public_sp_into_csv(args.input, fetched, source_url=args.source_url)
    print(f"\n已写入 {count} 场公开 SP 到 {args.input}。")


def cmd_china_sp_import_results(args):
    count = china_sp_fetch.merge_results_into_csv(args.input, args.results)
    print(f"已导入 {count} 场 90 分钟赛果到 {args.input}。")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wcforecast",
                                 description="Structural + Bayesian World Cup forecaster.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("forecast", help="champion probabilities (dual-track)")
    f.add_argument("--sims", type=int, default=20000)
    f.add_argument("--top", type=int, default=16)
    f.add_argument("--refit", action="store_true", help="refit instead of using the cache")
    f.set_defaults(func=cmd_forecast)

    p = sub.add_parser("predict", help="single-match 1X2 probabilities")
    p.add_argument("home")
    p.add_argument("away")
    p.add_argument("--neutral", action="store_true", help="force a neutral venue")
    p.add_argument("--refit", action="store_true")
    p.set_defaults(func=cmd_predict)

    v = sub.add_parser("validate", help="out-of-sample scorecard with significance")
    v.set_defaults(func=cmd_validate)

    o = sub.add_parser("odds", help="live bookmaker consensus odds (needs ODDS_API_KEY)")
    o.add_argument("--regions", default="eu,uk")
    o.set_defaults(func=cmd_odds)

    c = sub.add_parser("china-sp-review", help="中国体彩胜平负 SP 复盘网页")
    c.add_argument("--input", default="data/china_sp_review.csv")
    c.add_argument("--output", default="docs/china-sp-review.html")
    c.add_argument("--bankroll", type=int, default=100)
    c.add_argument("--unit", type=int, default=1)
    c.add_argument("--strategy", choices=sorted(china_sp.STRATEGIES), default="favorite-flat",
                   help="review staking layer: favorite-flat(default), edge-flat, prob-split, or kelly")
    c.add_argument("--min-edge", type=float, default=0.05,
                   help="minimum positive edge used by edge-flat/kelly strategies")
    c.add_argument("--kelly-fraction", type=float, default=0.25,
                   help="fractional Kelly multiplier used only with --strategy kelly")
    c.add_argument("--max-stake-fraction", type=float, default=1.0,
                   help="cap Kelly stake as a fraction of bankroll")
    c.set_defaults(func=cmd_china_sp_review)

    fetch = sub.add_parser("china-sp-fetch", help="fetch public China SP rows into the review CSV")
    fetch.add_argument("--input", default="data/china_sp_review.csv")
    fetch.add_argument("--source-url", default=china_sp_fetch.DEFAULT_SOURCE_URL)
    fetch.add_argument("--start-date", help="inclusive start date, e.g. 2026-06-28")
    fetch.add_argument("--end-date", help="inclusive end date, e.g. 2026-07-08")
    fetch.add_argument("--dry-run", action="store_true")
    fetch.set_defaults(func=cmd_china_sp_fetch)

    results = sub.add_parser("china-sp-import-results", help="import verified 90-minute H/D/A results")
    results.add_argument("--input", default="data/china_sp_review.csv")
    results.add_argument("--results", default="data/china_sp_results.csv")
    results.set_defaults(func=cmd_china_sp_import_results)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
