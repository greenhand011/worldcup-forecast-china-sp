# 中国体彩 SP 世界杯复盘 / China Sports Lottery SP World Cup Review

[Live Demo / 实时网页](https://greenhand011.github.io/worldcup-forecast-china-sp/) ·
[Source / 源码](https://github.com/greenhand011/worldcup-forecast-china-sp)

![China SP review preview](docs/assets/china-sp-review-preview.svg)

## 项目简介 / Overview

这是基于 `playmobil/worldcup-forecast` 改造的本地复盘项目。模型负责预测 90 分钟胜/平/负概率，
中国体彩 SP 由用户手动录入，只用于复盘和期望收益计算，不作为模型输入。

This project extends `playmobil/worldcup-forecast` with a China Sports Lottery SP review page.
The model estimates 90-minute home/draw/away probabilities, while China Sports Lottery SP values
are manually entered and used only for review and expected value analysis, not as model inputs.

## 中国体彩 SP 复盘

页面采用浅色双列卡片布局，分为“未来预测”和“历史复盘”两个区域：

- `actual` 留空的比赛会进入“未来预测 / 待开奖”。
- `actual = H/D/A` 的比赛会进入“历史复盘”，分别表示主胜、平局、客胜。
- 每张卡片优先展示主胜下注、平局下注、客胜下注的模拟金额和中国体彩 SP。
- 模型概率、公允赔率和 edge 放在“查看模型细节”折叠区域里，默认不挤占主卡片。

`data/china_sp_review.csv` 里已经放了多行示例 SP 和赛果，只是 demo 占位，不是官方中国体彩数据，
也不代表真实赛果或真实 SP。用户需要从合法渠道查看中国体彩竞彩足球胜平负 SP，然后手动替换 CSV。

运行：

```bash
wcforecast china-sp-review
```

打开本地页面：

```bash
docs/china-sp-review.html
```

GitHub Pages：

[https://greenhand011.github.io/worldcup-forecast-china-sp/](https://greenhand011.github.io/worldcup-forecast-china-sp/)

## 功能 / Features

- 读取手动录入的中国体彩胜平负 SP CSV。
  Read manually entered China Sports Lottery home/draw/away SP values from CSV.
- 计算模型概率、公允赔率、edge、模拟分配和盈亏。
  Calculate model probabilities, fair odds, edge, simulated allocations, and P&L.
- 生成静态 HTML 复盘页面。
  Generate a static HTML review page.
- 支持 GitHub Pages 在线展示。
  Support online publishing through GitHub Pages.
- 不包含自动下注、自动登录或购买彩票功能。
  No automated betting, login, or lottery purchase functionality is included.

## 快速开始 / Quick Start

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
wcforecast china-sp-review
start docs\china-sp-review.html
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
wcforecast china-sp-review
open docs/china-sp-review.html
```

## CSV 数据格式 / CSV Format

编辑 `data/china_sp_review.csv`，每行一场比赛。以 `#` 开头的行是注释，会被程序忽略：

```csv
# 示例数据：以下 SP 和赛果仅用于 demo 占位，不是官方中国体彩数据。
date,home,away,neutral,sp_home,sp_draw,sp_away,actual
2026-07-05,Brazil,Norway,true,1.83,3.77,4.88,
```

字段 / Fields:

- `date`: 比赛日期 / match date.
- `home`: 主队英文名，需匹配项目球队名 / home team name recognized by the project.
- `away`: 客队英文名，需匹配项目球队名 / away team name recognized by the project.
- `neutral`: `true` 或 `false` / whether the match is played at a neutral venue.
- `sp_home`: 中国体彩主胜 SP / China Sports Lottery home-win SP.
- `sp_draw`: 中国体彩平局 SP / China Sports Lottery draw SP.
- `sp_away`: 中国体彩主负/客胜 SP / China Sports Lottery away-win SP.
- `actual`: `H` / `D` / `A`，未开奖留空 / leave blank before settlement.

`actual = H/D/A`: `H = home win`, `D = draw`, `A = away win`.

## 方法说明 / Method

The review uses the project's existing calibrated 1X2 model probabilities. China Sports Lottery SP
values are applied only after prediction, for review and expected value display.

复盘使用项目原有的校准胜平负概率。中国体彩 SP 只在预测之后用于复盘展示和期望收益计算。

```text
fair_odds = 1 / probability
edge = probability * SP - 1
pnl = allocation_on_actual * sp_actual - bankroll
```

默认每场模拟本金为 `10000` 元，最小分配单位为 `100` 元。金额按模型概率分配到主胜、平局、
客胜三项，四舍五入后如有差额，加到概率最高的一项。

By default, each match uses a simulated bankroll of `10000` yuan and a minimum allocation unit of
`100` yuan. The bankroll is allocated across home/draw/away by model probability; any rounding
residual is assigned to the most likely outcome.

## 免责声明 / Disclaimer

本项目仅用于模型学习、复盘和数据分析，不构成投注建议。中国体彩 SP 需要用户手动录入。
模型概率不是保证，历史盈亏不能证明长期存在 edge。请遵守所在地法律法规，理性对待风险。

This project is for model learning, review, and data analysis only. It is not betting advice.
China Sports Lottery SP values must be entered manually. Model probabilities are not guarantees,
and historical P&L does not prove a persistent edge. Please follow local laws and regulations and
treat risk responsibly.

## 致谢 / Credits

本项目基于 `playmobil/worldcup-forecast` 的结构化 + 贝叶斯世界杯预测模型进行改造。
原模型逻辑、训练流程和市场独立性原则保持不变；本仓库新增的是中国体彩 SP 手动复盘展示层。

This project is adapted from the structural + Bayesian World Cup forecaster in
`playmobil/worldcup-forecast`. The original model logic, training flow, and market-independence
principle are preserved; this repository adds a manual China Sports Lottery SP review layer.
