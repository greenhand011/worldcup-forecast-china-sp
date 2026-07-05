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


def review_for(actual):
    return china_sp.review_match(sample_row(actual), FakeModel(), calibrator=identity)


def test_probability_allocation_sums_to_bankroll():
    allocation = china_sp.allocate_bankroll({"home": 0.334, "draw": 0.333, "away": 0.333})
    assert sum(allocation.values()) == 10000
    assert all(amount % 100 == 0 for amount in allocation.values())


def test_best_edge_allocation_uses_one_flat_100_yuan_stake():
    allocation, selected, status = china_sp.allocate_best_edge(
        {"home": 0.0, "draw": 0.2, "away": 0.0}
    )
    assert allocation == {"home": 0, "draw": 100, "away": 0}
    assert selected == "draw"
    assert status == "模拟买入"


def test_best_edge_allocation_observes_when_edge_is_not_positive():
    allocation, selected, status = china_sp.allocate_best_edge(
        {"home": -0.1, "draw": 0.0, "away": -0.2}
    )
    assert allocation == {"home": 0, "draw": 0, "away": 0}
    assert selected is None
    assert status == "edge未过阈值，观望"


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


def test_edge_is_probability_times_sp_minus_one():
    reviewed = china_sp.review_match(
        sample_row(None, sp_home=2.4),
        FakeModel(),
        calibrator=identity,
    )
    assert reviewed["edge"]["home"] == pytest.approx(0.5 * 2.4 - 1.0)


def test_blank_actual_is_pending_without_pnl():
    reviewed = review_for(None)
    assert reviewed["status"] == "pending"
    assert reviewed["actual_label"] == "待开奖"
    assert reviewed["pnl"] is None


def test_actual_non_blank_is_settled_history():
    reviewed = review_for("D")
    assert reviewed["status"] == "settled"
    assert reviewed["actual_outcome"] == "draw"
    assert reviewed["actual_label"] == "平局"
    assert reviewed["stage"] == "1/8决赛 第1场"


def test_missing_sp_does_not_create_stake():
    reviewed = china_sp.review_match(
        sample_row(None, sp_home=None, sp_draw=None, sp_away=None),
        FakeModel(),
        calibrator=identity,
    )
    assert reviewed["stake_status"] == "待录入SP"
    assert reviewed["allocation"] == {"home": 0, "draw": 0, "away": 0}
    assert reviewed["edge"] == {"home": None, "draw": None, "away": None}


def test_tbd_knockout_match_is_pending_and_unresolved():
    reviewed = china_sp.review_match(
        sample_row(None, home="TBD", away="TBD", stage="决赛", sp_home=None, sp_draw=None, sp_away=None),
        FakeModel(),
        calibrator=identity,
    )
    assert reviewed["status"] == "pending"
    assert reviewed["unresolved"] is True
    assert reviewed["probabilities"] is None
    assert reviewed["stake_status"] == "对阵待定"


def test_read_china_sp_csv_basic_with_comments_and_blank_sp(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "# template only, not official SP\n"
        "date,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "TBD,小组赛 A组 第1轮,Brazil,Norway,true,,,,\n",
        encoding="utf-8",
    )
    rows = china_sp.read_china_sp_csv(path)
    assert rows == [
        {
            "date": "TBD",
            "stage": "小组赛 A组 第1轮",
            "home": "Brazil",
            "away": "Norway",
            "neutral": True,
            "sp_home": None,
            "sp_draw": None,
            "sp_away": None,
            "actual": None,
        }
    ]


def test_build_review_splits_pending_and_settled(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "date,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "TBD,小组赛 A组 第1轮,Brazil,Norway,true,2.00,4.00,5.00,\n"
        "TBD,1/8决赛 第1场,Brazil,Norway,true,2.00,4.00,5.00,H\n",
        encoding="utf-8",
    )
    review = china_sp.build_review(path, FakeModel(), calibrator=identity)
    assert review["summary"]["pending_count"] == 1
    assert review["summary"]["settled_count"] == 1
    assert review["summary"]["stage_count"] == {"小组赛 A组 第1轮": 1, "1/8决赛 第1场": 1}
    assert [m["status"] for m in review["matches"]] == ["pending", "settled"]


def test_render_html_contains_redesigned_sections():
    review = {
        "summary": {
            "cumulative_pnl": 200.0,
            "settled_count": 1,
            "pending_count": 1,
            "match_count": 2,
            "staked_count": 2,
            "staked_settled_count": 1,
            "profitable_count": 1,
            "hit_rate": 1.0,
            "stage_count": {"1/8决赛 第1场": 2},
        },
        "matches": [review_for(None), review_for("D")],
    }
    html = china_sp.render_html(review)
    for text in ["未来预测", "历史复盘", "主胜下注", "平局下注", "客胜下注", "查看模型细节", "1/8决赛 第1场"]:
        assert text in html


def test_known_fixture_without_sp_stays_in_folded_template_section(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "date,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "TBD,小组赛 A组 第1轮,Mexico,South Africa,false,,,,\n"
        "2026-07-05,1/8决赛 第1场,Brazil,Norway,true,2.00,4.00,5.00,\n",
        encoding="utf-8",
    )
    review = china_sp.build_review(path, FakeModel(), calibrator=identity)
    assert review["summary"]["raw_pending_count"] == 2
    assert review["summary"]["pending_count"] == 1
    assert review["summary"]["template_count"] == 1
    assert len(review["display_pending"]) == 1
    assert len(review["template_pending"]) == 1


def test_tbd_fixture_stays_in_folded_template_section(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "date,stage,home,away,neutral,sp_home,sp_draw,sp_sp_away,actual\n".replace("sp_sp_away", "sp_away")
        + "TBD,1/4决赛 第1场,TBD,TBD,true,,,,\n"
        + "2026-07-05,1/8决赛 第1场,Brazil,Norway,true,2.00,4.00,5.00,\n",
        encoding="utf-8",
    )
    review = china_sp.build_review(path, FakeModel(), calibrator=identity)
    html = china_sp.render_html(review)
    assert "未来预测 1" in html
    assert "待录入赛程模板 1" in html
    assert "展开查看 1 场待录入模板" in html


def test_blank_sp_fixture_is_folded_instead_of_empty_main_card(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "date,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "TBD,小组赛 A组 第1轮,Brazil,Norway,true,,,,\n",
        encoding="utf-8",
    )
    review = china_sp.build_review(path, FakeModel(), calibrator=identity)
    html = china_sp.render_html(review)
    assert "未来预测 0" in html
    assert "待录入赛程模板 1" in html
    assert "Brazil vs Norway" in html
