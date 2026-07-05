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
    assert sum(allocation.values()) == 100
    assert all(amount % 1 == 0 for amount in allocation.values())


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
        ("H", 0.0),
        ("D", 20.0),
        ("A", 0.0),
    ],
)
def test_pnl_for_each_actual_outcome(actual, expected_pnl):
    reviewed = review_for(actual)
    assert reviewed["allocation"] == {"home": 50, "draw": 30, "away": 20}
    assert reviewed["stake_total"] == 100
    assert reviewed["selected_outcome"] == "home"
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
        "2026-07-06,今日赛,Brazil,Norway,true,2.00,4.00,5.00,\n"
        "2026-07-07,未来赛,Brazil,Norway,true,2.00,4.00,5.00,\n"
        "2026-07-04,过往赛,Brazil,Norway,true,2.00,4.00,5.00,\n"
        "2026-07-03,历史赛,Brazil,Norway,true,2.00,4.00,5.00,H\n",
        encoding="utf-8",
    )
    review = china_sp.build_review(path, FakeModel(), calibrator=identity, today="2026-07-06")
    assert review["summary"]["today_pending_count"] == 1
    assert review["summary"]["today_stake_total"] == 100
    assert review["summary"]["awaiting_result_count"] == 1
    assert review["summary"]["future_count"] == 1
    assert review["summary"]["settled_count"] == 1
    assert len(review["today_pending"]) == 1
    assert len(review["awaiting_result"]) == 1
    assert len(review["future_pending"]) == 1
    assert len(review["settled"]) == 1
    assert review["today_pending"][0]["date"] == "2026-07-06"
    assert review["awaiting_result"][0]["date"] == "2026-07-04"
    assert review["future_pending"][0]["date"] == "2026-07-07"


def test_render_html_contains_redesigned_sections():
    review = {
        "summary": {
            "cumulative_pnl": 200.0,
            "settled_count": 1,
            "pending_count": 1,
            "today_pending_count": 1,
            "today_stake_total": 100,
            "awaiting_result_count": 0,
            "future_count": 0,
            "match_count": 2,
            "staked_count": 2,
            "staked_settled_count": 1,
            "profitable_count": 1,
            "hit_rate": 1.0,
            "roi": 2.0,
            "stage_count": {"1/8决赛 第1场": 2},
        },
        "today_pending": [review_for(None)],
        "awaiting_result": [],
        "future_pending": [],
        "settled": [review_for("D")],
        "matches": [review_for(None), review_for("D")],
    }
    html = china_sp.render_html(review)
    for text in ["今日预测", "完赛待补赛果", "历史复盘", "未来赛程", "主胜下注", "平局下注", "客胜下注", "查看模型细节", "1/8决赛 第1场"]:
        assert text in html


def test_known_fixture_without_sp_stays_in_folded_template_section(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "date,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "TBD,小组赛 A组 第1轮,Mexico,South Africa,false,,,,\n"
        "2026-07-05,1/8决赛 第1场,Brazil,Norway,true,2.00,4.00,5.00,\n",
        encoding="utf-8",
    )
    review = china_sp.build_review(path, FakeModel(), calibrator=identity, today="2026-07-01")
    assert review["summary"]["raw_pending_count"] == 2
    assert review["summary"]["today_pending_count"] == 0
    assert review["summary"]["future_count"] == 1
    assert review["summary"]["template_count"] == 1
    assert len(review["today_pending"]) == 0
    assert len(review["future_pending"]) == 1
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
    assert "今日预测 0" in html
    assert "未来赛程 0" in html
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
    assert "今日预测 0" in html
    assert "待录入赛程模板 1" in html
    assert "巴西 vs 挪威" in html


def test_sp_match_on_review_date_is_today_prediction(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "date,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "2026-07-06,1/8决赛（公开SP）,Brazil,Norway,true,1.55,3.68,4.70,\n",
        encoding="utf-8",
    )
    review = china_sp.build_review(path, FakeModel(), calibrator=identity, today="2026-07-06")
    assert review["summary"]["today_pending_count"] == 1
    assert review["summary"]["today_stake_total"] == 100
    assert review["summary"]["awaiting_result_count"] == 0
    assert review["summary"]["future_count"] == 0
    html = china_sp.render_html(review)
    assert "今日预测 1" in html
    assert "今日待结算" in html
    assert "巴西 vs 挪威" in html


def test_date_partition_rules_for_today_past_future_and_settled(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "date,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "2026-07-04,过往赛,Brazil,Norway,true,1.55,3.68,4.70,\n"
        "2026-07-06,今日赛,Mexico,England,true,3.20,2.86,2.14,\n"
        "2026-07-07,未来赛,Portugal,Spain,true,3.83,3.30,1.77,\n"
        "2026-07-01,历史赛,Canada,Morocco,true,5.78,3.62,1.47,A\n",
        encoding="utf-8",
    )
    review = china_sp.build_review(path, FakeModel(), calibrator=identity, today="2026-07-06")
    assert [m["date"] for m in review["awaiting_result"]] == ["2026-07-04"]
    assert [m["date"] for m in review["today_pending"]] == ["2026-07-06"]
    assert [m["date"] for m in review["future_pending"]] == ["2026-07-07"]
    assert [m["date"] for m in review["settled"]] == ["2026-07-01"]
    assert review["summary"]["today_stake_total"] == 100
    assert review["summary"]["awaiting_result_count"] == 1
    assert review["summary"]["future_count"] == 1
