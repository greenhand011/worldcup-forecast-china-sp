import pytest

from wcforecast import china_sp


class FakeModel:
    def match_probs(self, home, away, home_advantage=0.0):
        return (0.5, 0.3, 0.2)


def identity(probs):
    return probs


def sample_row(actual=None):
    return {
        "date": "2026-07-05",
        "stage": "淘汰赛",
        "home": "Brazil",
        "away": "Norway",
        "neutral": True,
        "sp_home": 2.0,
        "sp_draw": 4.0,
        "sp_away": 5.0,
        "actual": actual,
    }


def review_for(actual):
    return china_sp.review_match(sample_row(actual), FakeModel(), calibrator=identity)


def test_probability_allocation_sums_to_bankroll():
    allocation = china_sp.allocate_bankroll({"home": 0.334, "draw": 0.333, "away": 0.333})
    assert sum(allocation.values()) == 10000
    assert all(amount % 100 == 0 for amount in allocation.values())


def test_best_edge_allocation_uses_one_flat_100_yuan_stake():
    allocation = china_sp.allocate_best_edge({"home": 0.0, "draw": 0.2, "away": 0.0})
    assert allocation == {"home": 0, "draw": 100, "away": 0}
    assert sum(allocation.values()) == 100


@pytest.mark.parametrize(
    ("actual", "expected_pnl"),
    [
        ("H", -100.0),
        ("D", 300.0),
        ("A", -100.0),
    ],
)
def test_pnl_for_each_actual_outcome(actual, expected_pnl):
    reviewed = review_for(actual)
    assert reviewed["allocation"] == {"home": 0, "draw": 100, "away": 0}
    assert reviewed["stake_total"] == 100
    assert reviewed["selected_outcome"] == "draw"
    assert reviewed["pnl"] == expected_pnl


def test_unsettled_blank_actual_is_pending():
    reviewed = review_for(None)
    assert reviewed["status"] == "pending"
    assert reviewed["actual_label"] == "待开奖"
    assert reviewed["pnl"] is None


def test_actual_non_blank_is_settled_history():
    reviewed = review_for("D")
    assert reviewed["status"] == "settled"
    assert reviewed["actual_outcome"] == "draw"
    assert reviewed["actual_label"] == "平局"
    assert reviewed["stage"] == "淘汰赛"


def test_edge_is_probability_times_sp_minus_one():
    reviewed = china_sp.review_match(
        {**sample_row(None), "sp_home": 2.4},
        FakeModel(),
        calibrator=identity,
    )
    assert reviewed["edge"]["home"] == pytest.approx(0.5 * 2.4 - 1.0)


def test_read_china_sp_csv_basic_with_comments(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "# demo only, not official SP\n"
        "date,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "2026-07-05,淘汰赛,Brazil,Norway,true,1.83,3.77,4.88,\n",
        encoding="utf-8",
    )
    rows = china_sp.read_china_sp_csv(path)
    assert rows == [
        {
            "date": "2026-07-05",
            "stage": "淘汰赛",
            "home": "Brazil",
            "away": "Norway",
            "neutral": True,
            "sp_home": 1.83,
            "sp_draw": 3.77,
            "sp_away": 4.88,
            "actual": None,
        }
    ]


def test_build_review_splits_pending_and_settled(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "date,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "2026-07-05,淘汰赛,Brazil,Norway,true,2.00,4.00,5.00,\n"
        "2026-07-06,小组赛,Brazil,Norway,true,2.00,4.00,5.00,H\n",
        encoding="utf-8",
    )
    review = china_sp.build_review(path, FakeModel(), calibrator=identity)
    assert review["summary"]["pending_count"] == 1
    assert review["summary"]["settled_count"] == 1
    assert review["summary"]["stage_count"] == {"淘汰赛": 1, "小组赛": 1}
    assert [m["status"] for m in review["matches"]] == ["pending", "settled"]


def test_render_html_contains_redesigned_sections():
    review = {
        "summary": {
            "cumulative_pnl": 2000.0,
            "settled_count": 1,
            "pending_count": 1,
            "match_count": 2,
            "profitable_count": 1,
            "hit_rate": 1.0,
            "stage_count": {"淘汰赛": 2},
        },
        "matches": [review_for(None), review_for("D")],
    }
    html = china_sp.render_html(review)
    for text in ["未来预测", "历史复盘", "主胜下注", "平局下注", "客胜下注", "查看模型细节", "淘汰赛"]:
        assert text in html
