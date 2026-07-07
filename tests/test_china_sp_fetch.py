from datetime import datetime

from wcforecast import china_sp_fetch


SAMPLE_HTML = """
<table>
  <tr class="bet-tb-tr" data-homesxname="葡萄牙" data-awaysxname="西班牙"
      data-matchdate="2026-07-07" data-matchtime="03:00"
      data-simpleleague="世界杯" data-matchnum="周一093">
    <td class="td td-betbtn">
      <div class="betbtn-row itm-rangB1">
        <p class="betbtn" data-type="nspf" data-value="3" data-sp="3.83"><span>3.83</span></p>
        <p class="betbtn" data-type="nspf" data-value="1" data-sp="3.30"><span>3.30</span></p>
        <p class="betbtn" data-type="nspf" data-value="0" data-sp="1.77"><span>1.77</span></p>
      </div>
      <div class="betbtn-row itm-rangB2">
        <p class="betbtn" data-type="spf" data-value="3" data-sp="1.82"><span>1.82</span></p>
        <p class="betbtn" data-type="spf" data-value="1" data-sp="3.48"><span>3.48</span></p>
        <p class="betbtn" data-type="spf" data-value="0" data-sp="3.42"><span>3.42</span></p>
      </div>
    </td>
  </tr>
  <tr class="bet-tb-tr bet-tb-end" data-homesxname="巴西" data-awaysxname="挪威"
      data-matchdate="2026-07-06" data-matchtime="04:00"
      data-simpleleague="世界杯" data-matchnum="周日091">
    <td class="td td-betbtn">
      <div class="betbtn-row itm-rangB1">
        <p class="betbtn" data-type="nspf" data-value="3" data-sp="1.55"><span>1.55</span></p>
        <p class="betbtn" data-type="nspf" data-value="1" data-sp="3.68"><span>3.68</span></p>
        <p class="betbtn" data-type="nspf" data-value="0" data-sp="4.70"><span>4.70</span></p>
      </div>
    </td>
  </tr>
  <tr class="bet-tb-tr" data-homesxname="赫根" data-awaysxname="佐加顿斯"
      data-matchdate="2026-07-07" data-matchtime="01:00"
      data-simpleleague="瑞超" data-matchnum="周一201">
    <td><div class="betbtn-row itm-rangB1">
      <p class="betbtn" data-type="nspf" data-value="3" data-sp="2.28"><span>2.28</span></p>
    </div></td>
  </tr>
</table>
"""


def test_parse_public_sp_html_keeps_world_cup_nspf_only():
    rows = china_sp_fetch.parse_public_sp_html(SAMPLE_HTML)
    assert len(rows) == 2
    match = rows[0]
    assert match.home == "Portugal"
    assert match.away == "Spain"
    assert match.stage == "1/8决赛（公开SP）"
    assert (match.sp_home, match.sp_draw, match.sp_away) == (3.83, 3.30, 1.77)
    assert rows[1].home == "Brazil"
    assert rows[1].away == "Norway"
    assert (rows[1].sp_home, rows[1].sp_draw, rows[1].sp_away) == (1.55, 3.68, 4.70)


def test_merge_public_sp_into_csv_prepends_fetched_rows(tmp_path):
    csv_path = tmp_path / "china_sp_review.csv"
    csv_path.write_text(
        "# template\n"
        "date,match_id,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "TBD,FINAL,决赛,TBD,TBD,true,,,,\n",
        encoding="utf-8",
    )
    fetched = china_sp_fetch.parse_public_sp_html(SAMPLE_HTML, source_url="https://example.test/")
    count = china_sp_fetch.merge_public_sp_into_csv(
        csv_path,
        fetched,
        source_url="https://example.test/",
        fetched_at=datetime(2026, 7, 6, 12, 0, 0),
    )
    text = csv_path.read_text(encoding="utf-8")
    assert count == 2
    assert "公开SP来源：https://example.test/" in text
    assert "2026-07-07,,1/8决赛（公开SP）,Portugal,Spain,true,3.83,3.30,1.77," in text
    assert "2026-07-06,,1/8决赛（公开SP）,Brazil,Norway,true,1.55,3.68,4.70," in text
    assert "TBD,FINAL,决赛,TBD,TBD,true,,,," in text


def test_merge_results_into_csv_uses_90_minute_score(tmp_path):
    review_path = tmp_path / "china_sp_review.csv"
    review_path.write_text(
        "# template\n"
        "date,match_id,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "2026-07-04,R16-1,1/8决赛（公开SP）,Canada,Morocco,true,5.78,3.62,1.47,\n",
        encoding="utf-8",
    )
    results_path = tmp_path / "china_sp_results.csv"
    results_path.write_text(
        "# 90 minute result, excluding extra time and penalties\n"
        "date,home,away,home_score_90,away_score_90,actual\n"
        "2026-07-04,Canada,Morocco,0,3,\n",
        encoding="utf-8",
    )
    count = china_sp_fetch.merge_results_into_csv(review_path, results_path)
    text = review_path.read_text(encoding="utf-8")
    assert count == 1
    assert "2026-07-04,R16-1,1/8决赛（公开SP）,Canada,Morocco,true,5.78,3.62,1.47,A" in text


def test_merge_results_into_csv_accepts_direct_actual(tmp_path):
    review_path = tmp_path / "china_sp_review.csv"
    review_path.write_text(
        "date,match_id,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "2026-07-04,R16-1,1/8决赛（公开SP）,Canada,Morocco,true,5.78,3.62,1.47,\n",
        encoding="utf-8",
    )
    results_path = tmp_path / "china_sp_results.csv"
    results_path.write_text(
        "date,home,away,home_score_90,away_score_90,actual\n"
        "2026-07-04,Canada,Morocco,,,D\n",
        encoding="utf-8",
    )
    count = china_sp_fetch.merge_results_into_csv(review_path, results_path)
    assert count == 1
    assert review_path.read_text(encoding="utf-8").strip().endswith(",D")


def test_merge_results_derives_90_minute_draw_as_d(tmp_path):
    review_path = tmp_path / "china_sp_review.csv"
    review_path.write_text(
        "date,match_id,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "2026-07-04,R16-1,1/8决赛（公开SP）,Australia,Egypt,true,3.50,3.30,3.20,\n",
        encoding="utf-8",
    )
    results_path = tmp_path / "china_sp_results.csv"
    results_path.write_text(
        "date,home,away,home_score_90,away_score_90,actual\n"
        "2026-07-04,Australia,Egypt,1,1,\n",
        encoding="utf-8",
    )
    count = china_sp_fetch.merge_results_into_csv(review_path, results_path)
    assert count == 1
    assert review_path.read_text(encoding="utf-8").strip().endswith(",D")


def test_merge_results_normalizes_usa_alias(tmp_path):
    review_path = tmp_path / "china_sp_review.csv"
    review_path.write_text(
        "date,match_id,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "2026-07-02,R16-1,1/8决赛（公开SP）,United States,Bosnia and Herzegovina,true,1.22,4.91,9.40,\n",
        encoding="utf-8",
    )
    results_path = tmp_path / "china_sp_results.csv"
    results_path.write_text(
        "date,home,away,home_score_90,away_score_90,actual\n"
        "2026-07-02,USA,Bosnia and Herzegovina,2,0,\n",
        encoding="utf-8",
    )
    count = china_sp_fetch.merge_results_into_csv(review_path, results_path)
    assert count == 1
    assert review_path.read_text(encoding="utf-8").strip().endswith(",H")


def test_merge_results_preserves_duplicate_tbd_templates(tmp_path):
    review_path = tmp_path / "china_sp_review.csv"
    review_path.write_text(
        "date,match_id,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual\n"
        "2026-07-05,R16-1,1/8决赛（公开SP）,Canada,Morocco,true,5.78,3.62,1.47,\n"
        "TBD,,1/8决赛 第1场,TBD,TBD,true,,,,\n"
        "TBD,,1/8决赛 第2场,TBD,TBD,true,,,,\n",
        encoding="utf-8",
    )
    results_path = tmp_path / "china_sp_results.csv"
    results_path.write_text(
        "date,home,away,home_score_90,away_score_90,actual\n"
        "2026-07-05,Canada,Morocco,0,3,\n",
        encoding="utf-8",
    )
    count = china_sp_fetch.merge_results_into_csv(review_path, results_path)
    text = review_path.read_text(encoding="utf-8")
    assert count == 1
    assert "2026-07-05,R16-1,1/8决赛（公开SP）,Canada,Morocco,true,5.78,3.62,1.47,A" in text
    assert "TBD,,1/8决赛 第1场,TBD,TBD,true,,,," in text
    assert "TBD,,1/8决赛 第2场,TBD,TBD,true,,,," in text
