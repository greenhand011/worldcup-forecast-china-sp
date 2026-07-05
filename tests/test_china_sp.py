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


@pytest.mark.parametrize(
    ("actual", "expected_pnl"),
    [
        ("H", 0.0),
        ("D", 2000.0),
        ("A", 0.0),
    ],
)
def test_pnl_for_each_actual_outcome(actual, expected_pnl):
    reviewed = review_for(actual)
    assert reviewed["allocation"] == {"home": 5000, "draw": 3000, "away": 2000}
    assert reviewed["pnl"] == expected_pnl


def test_unsettled_blank_actual_is_pending():
    reviewed = review_for(None)
    assert reviewed["status"] == "pending"
    assert reviewed["actual_label"] == "待开奖"
    assert reviewed["pnl"] is None


def test_edge_is_probability_times_sp_minus_one():
    reviewed = china_sp.review_match(
        {**sample_row(None), "sp_home": 2.4},
        FakeModel(),
        calibrator=identity,
    )
    assert reviewed["edge"]["home"] == pytest.approx(0.5 * 2.4 - 1.0)


def test_read_china_sp_csv_basic(tmp_path):
    path = tmp_path / "china_sp_review.csv"
    path.write_text(
        "date,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "2026-07-05,Brazil,Norway,true,1.83,3.77,4.88,\n",
        encoding="utf-8",
    )
    rows = china_sp.read_china_sp_csv(path)
    assert rows == [
        {
            "date": "2026-07-05",
            "home": "Brazil",
            "away": "Norway",
            "neutral": True,
            "sp_home": 1.83,
            "sp_draw": 3.77,
            "sp_away": 4.88,
            "actual": None,
        }
    ]
