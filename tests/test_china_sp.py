import pytest

from wcforecast import china_sp


class FakeModel:
    def match_probs(self, home, away, home_advantage=0.0):
        return (0.5, 0.3, 0.2)


def identity(probs):
    return probs


def sample_row(actual=None, **overrides):
    row = {
        "date": "2026-07-05",
        "stage": "1/8决赛 第1场",
        "home": "Brazil",
        "away": "Norway",
        "neutral": True,
        "sp_home": 2.0,
        "sp_draw": 4.0,
        "sp_away": 5.0,
        "actual": actual,
    }
    row.update(overrides)
    return row


def review_for(actual, **kwargs):
    return china_sp.review_match(sample_row(actual, **kwargs.pop("row_overrides", {})), FakeModel(), calibrator=identity, **kwargs)


def test_probability_allocation_sums_to_bankroll():
    allocation = china_sp.allocate_bankroll({"home": 0.334, "draw": 0.333, "away": 0.333})
    assert sum(allocation.values()) == 100
    assert all(amount % 1 == 0 for amount in allocation.values())


def test_prob_split_is_available_but_not_default():
    reviewed = review_for("D", strategy="prob-split")
    assert reviewed["allocation"] == {"home": 50, "draw": 30, "away": 20}
    assert reviewed["pnl"] == pytest.approx(20.0)
    assert "概率拆分" in reviewed["stake_status"]


def test_favorite_flat_is_default_and_must_bet_model_favorite():
    reviewed = review_for("H", row_overrides={"sp_home": 1.8, "sp_draw": 3.0, "sp_away": 4.0})
    assert reviewed["allocation"] == {"home": 100, "draw": 0, "away": 0}
    assert reviewed["stake_total"] == 100
    assert reviewed["selected_outcome"] == "home"
    assert reviewed["pnl"] == pytest.approx(80.0)
    assert "favorite-flat" in reviewed["stake_status"]


def test_edge_flat_observes_when_no_edge_exceeds_threshold():
    reviewed = review_for(None, strategy="edge-flat", row_overrides={"sp_home": 1.8, "sp_draw": 3.0, "sp_away": 4.0})
    assert reviewed["allocation"] == {"home": 0, "draw": 0, "away": 0}
    assert reviewed["stake_total"] == 0
    assert reviewed["selected_outcome"] is None
    assert "观望" in reviewed["stake_status"]


def test_edge_flat_bets_one_flat_stake_on_best_positive_edge():
    reviewed = review_for(None, strategy="edge-flat", row_overrides={"sp_home": 2.4, "sp_draw": 3.0, "sp_away": 4.0})
    assert reviewed["allocation"] == {"home": 100, "draw": 0, "away": 0}
    assert reviewed["selected_outcome"] == "home"
    assert reviewed["stake_total"] == 100


def test_pnl_for_edge_flat_win_and_loss():
    win = review_for("H", strategy="edge-flat", row_overrides={"sp_home": 2.4, "sp_draw": 3.0, "sp_away": 4.0})
    loss = review_for("D", strategy="edge-flat", row_overrides={"sp_home": 2.4, "sp_draw": 3.0, "sp_away": 4.0})
    assert win["pnl"] == pytest.approx(140.0)
    assert loss["pnl"] == pytest.approx(-100.0)


def test_observed_match_with_no_stake_has_zero_pnl_and_no_roi_capital(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "date,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "2026-07-03,历史赛,Brazil,Norway,true,1.80,3.00,4.00,H\n",
        encoding="utf-8",
    )
    review = china_sp.build_review(path, FakeModel(), strategy="edge-flat", calibrator=identity, today="2026-07-06")
    assert review["settled"][0]["stake_total"] == 0
    assert review["settled"][0]["pnl"] == 0
    assert review["summary"]["history_stake_total"] == 0
    assert review["summary"]["roi"] is None


def test_fractional_kelly_uses_fractional_positive_edge_only():
    allocation, selected, status = china_sp.allocate_fractional_kelly(
        {"home": 0.5, "draw": 0.3, "away": 0.2},
        {"home": 2.4, "draw": 3.0, "away": 4.0},
        bankroll=100,
        unit=1,
        min_edge=0.05,
        kelly_fraction=0.25,
    )
    assert selected == "home"
    assert 0 < allocation["home"] < 100
    assert allocation["draw"] == 0
    assert allocation["away"] == 0
    assert "kelly" in status


def test_build_review_splits_today_history_awaiting_future(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "date,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "2026-07-06,今日赛,Brazil,Norway,true,2.40,3.00,4.00,\n"
        "2026-07-07,未来赛,Brazil,Norway,true,2.40,3.00,4.00,\n"
        "2026-07-04,过往赛,Brazil,Norway,true,2.40,3.00,4.00,\n"
        "2026-07-03,历史赛,Brazil,Norway,true,2.40,3.00,4.00,H\n",
        encoding="utf-8",
    )
    review = china_sp.build_review(path, FakeModel(), calibrator=identity, today="2026-07-06")
    assert [m["date"] for m in review["today_pending"]] == ["2026-07-06"]
    assert [m["date"] for m in review["awaiting_result"]] == ["2026-07-04"]
    assert [m["date"] for m in review["future_pending"]] == ["2026-07-07"]
    assert [m["date"] for m in review["settled"]] == ["2026-07-03"]
    assert review["summary"]["today_stake_total"] == 100
    assert review["summary"]["staked_settled_count"] == 1


def test_strategy_comparison_summarizes_review_layer_only(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "date,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "2026-07-03,历史赛,Brazil,Norway,true,2.40,3.00,4.00,H\n"
        "2026-07-04,历史赛,Brazil,Norway,true,1.80,3.00,4.00,D\n",
        encoding="utf-8",
    )
    review = china_sp.build_review(path, FakeModel(), calibrator=identity, today="2026-07-06")
    comparison = {row["strategy"]: row for row in review["strategy_comparison"]}
    assert set(comparison) == {"favorite-flat", "prob-split", "edge-flat", "kelly"}
    assert comparison["favorite-flat"]["staked_settled_count"] == 2
    assert comparison["favorite-flat"]["total_stake"] == pytest.approx(200.0)
    assert comparison["edge-flat"]["staked_settled_count"] < comparison["favorite-flat"]["staked_settled_count"]


def test_render_html_has_new_summary_and_section_order():
    review = {
        "summary": {
            "cumulative_pnl": -100.0,
            "settled_count": 1,
            "pending_count": 1,
            "today_pending_count": 1,
            "today_stake_total": 100,
            "awaiting_result_count": 0,
            "future_count": 0,
            "match_count": 2,
            "staked_count": 2,
            "staked_settled_count": 1,
            "profitable_count": 0,
            "hit_rate": 0.0,
            "roi": -1.0,
            "history_stake_total": 100.0,
            "stage_count": {"1/8决赛 第1场": 2},
        },
        "strategy": "favorite-flat",
        "today_pending": [review_for(None, row_overrides={"sp_home": 2.4})],
        "awaiting_result": [],
        "future_pending": [],
        "settled": [review_for("D", row_overrides={"sp_home": 2.4})],
        "matches": [review_for(None, row_overrides={"sp_home": 2.4}), review_for("D", row_overrides={"sp_home": 2.4})],
        "strategy_comparison": [
            {
                "strategy": "favorite-flat",
                "settled_count": 1,
                "staked_settled_count": 1,
                "total_stake": 100.0,
                "cumulative_pnl": -100.0,
                "roi": -1.0,
                "profitable_rate": 0.0,
                "max_profit": -100.0,
                "max_loss": -100.0,
            }
        ],
    }
    html = china_sp.render_html(review)
    assert "累计盈亏" in html
    assert "策略对比" in html
    assert "favorite-flat" in html
    assert "今日实际下注" in html
    assert "盈利下注率" in html
    assert html.index("<h2>历史复盘") < html.index("<h2>完赛待补赛果")


def test_tbd_fixture_stays_in_folded_template_section(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "date,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "TBD,1/4决赛 第1场,TBD,TBD,true,,,,\n"
        "2026-07-05,1/8决赛 第1场,Brazil,Norway,true,2.00,4.00,5.00,\n",
        encoding="utf-8",
    )
    review = china_sp.build_review(path, FakeModel(), calibrator=identity, today="2026-07-01")
    html = china_sp.render_html(review)
    assert "待录入赛程模板 1" in html
    assert "展开查看 1 场待录入模板" in html
