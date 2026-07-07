from pathlib import Path

from wcforecast import china_sp_fetch


ROOT = Path(__file__).resolve().parents[1]


def test_readme_describes_favorite_flat_as_default_strategy():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "默认策略是 `favorite-flat`" in text
    assert "按模型概率拆分到主胜/平局/客胜" not in text
    assert "wcforecast china-sp-review --strategy favorite-flat --bankroll 100 --unit 1" in text


def test_readme_result_csv_example_matches_importer_fields():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    header = ",".join(china_sp_fetch.RESULT_FIELDS)
    assert header in text
    assert "date,home,away,sp_home,sp_draw,sp_away,home_score_90,away_score_90,actual" not in text


def test_cli_china_sp_review_default_strategy_is_favorite_flat():
    text = (ROOT / "src" / "wcforecast" / "cli.py").read_text(encoding="utf-8")
    assert 'default="favorite-flat"' in text
    assert "choices=sorted(china_sp.STRATEGIES)" in text
