# 中国体彩 SP 世界杯复盘 / China Sports Lottery SP World Cup Review

[Live Demo / 实时网页](https://greenhand011.github.io/worldcup-forecast-china-sp/) |
[Source / 源码](https://github.com/greenhand011/worldcup-forecast-china-sp)

![中国体彩 SP 世界杯复盘封面](docs/assets/cover-fans.png)

## 项目简介 / Overview

这是基于 `playmobil/worldcup-forecast` 改造的本地复盘项目。原模型负责预测 90 分钟胜/平/负概率；中国体彩竞彩足球胜平负 SP 由用户手动录入，只用于赛前比较、edge 计算和赛后复盘，不作为模型输入。

This project extends `playmobil/worldcup-forecast` with a China Sports Lottery SP review page. The model estimates 90-minute home/draw/away probabilities, while China Sports Lottery SP values are manually entered and used only for review and expected value analysis, not as model inputs.

当前版本把 2026 世界杯 104 场赛事做成手工录入模板：72 场小组赛、16 场 1/16 决赛（32 强）、8 场 1/8 决赛、4 场 1/4 决赛、2 场半决赛、三四名决赛和决赛。淘汰赛对阵未确定时用 `TBD` 标记。

The current version provides a 104-match 2026 World Cup template: 72 group-stage matches, 16 round-of-32 matches, 8 round-of-16 matches, 4 quarterfinals, 2 semifinals, the third-place match, and the final. Unknown knockout matchups are marked as `TBD`.

## 功能 / Features

- 读取手动录入或公开页面导入的中国体彩胜平负 SP CSV。 / Read manually entered or publicly imported China Sports Lottery 1X2 SP values from CSV.
- 计算模型概率、公允赔率、edge、模拟买入和盈亏。 / Calculate model probabilities, fair odds, edge, simulated stake, and P&L.
- 生成浅色双列卡片布局的静态 HTML 复盘页面。 / Generate a static light two-column card review page.
- 支持 GitHub Pages 在线展示。 / Support online publishing through GitHub Pages.
- 不包含自动下注、自动登录、购买彩票或绕过网站限制的功能。 / No automated betting, login, lottery purchase, or bypassing website restrictions.

## 亏损原因与修正 / Loss Diagnosis And Fix

旧页面中的亏损来自演示 SP、未补齐赛果和策略层展示方式，并不是真实中国体彩长期结论；这些结果不能用于反向调参。当前项目不使用 15 场或几十场小样本回头训练模型，避免过拟合。

The old page loss came from demo SP values, incomplete results, and staking-layer display choices. It is not evidence for refitting the model, and this project does not tune model parameters on a tiny historical sample.

当前修正：

- `data/china_sp_review.csv` 默认 SP 和赛果留空，明确只是手工录入模板，不再伪装成真实体彩数据。
- 默认策略是 `favorite-flat`：每场完整 SP 比赛固定模拟 100 元，只买模型概率最高的一项。
- SP 缺失、对阵未定或赛果未确认时，页面显示“待录入SP / 对阵待定 / 待赛果复核”，不结算盈亏。
- SP 只用于预测后比较、edge 显示和盈亏复盘，不作为模型输入，也不用于反向调参。
- 不使用少量历史/demo 结果调模型参数，避免过拟合。
- `prob-split` 概率拆分仍保留为对照模式，但不再作为默认推荐策略。

Current fix:

- `data/china_sp_review.csv` is a manual-entry template with blank SP and blank results by default.
- The default strategy is `favorite-flat`: each complete-SP match uses one fixed 100-yuan simulated stake on the model's highest-probability outcome.
- Missing SP, unresolved matchups, or missing final results are shown as waiting/review-needed and do not create settled P&L.
- SP is used only after prediction for edge display and P&L review; it is not a model input and is not used to refit parameters.
- The model is not tuned on a tiny demo history, which helps avoid overfitting.
- `prob-split` is still available as a comparison/review mode, but it is not the default recommendation.

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

导入公开 SP 展示数据 / Import public SP display data:

```bash
wcforecast china-sp-fetch
wcforecast china-sp-review
```

`china-sp-fetch` 当前读取公开可访问的竞彩足球胜平负展示页，只导入 `nspf` 不让球胜平负三项 SP，并过滤世界杯比赛。它不会登录、不会下单、不会购买彩票；抓取到的 SP 仍建议按官方渠道复核。

按日期范围回扫公开 SP：

```bash
wcforecast china-sp-fetch --start-date 2026-06-28 --end-date 2026-07-08
```

导入已核验的 90 分钟赛果：

```bash
wcforecast china-sp-import-results --results data/china_sp_results.csv
wcforecast china-sp-review
```

`china-sp-fetch` reads publicly accessible 1X2 SP display data, imports only non-handicap `nspf` home/draw/away SP rows, and filters World Cup matches. It does not log in, place bets, or purchase lottery tickets; fetched SP values should still be checked against official channels.

默认生成：

```bash
wcforecast china-sp-review
```

等价于：

```bash
wcforecast china-sp-review --strategy favorite-flat --bankroll 100 --unit 1
```

对照旧策略：

```bash
wcforecast china-sp-review --strategy prob-split
```

价值策略：

```bash
wcforecast china-sp-review --strategy edge-flat --min-edge 0.05
```

Kelly 对照：

```bash
wcforecast china-sp-review --strategy kelly --min-edge 0.05 --kelly-fraction 0.25
```

页面展示逻辑 / Page sections:

- `今日预测`: `date == today` 且 `actual` 为空的比赛；未开赛、正在踢、已完赛但未录入 90 分钟比分都在这里，也只有这些比赛计入“今日实际下注”。 / Matches where `date == today` and `actual` is blank; only these count toward today's actual simulated stake.
- `完赛待补赛果`: `date < today` 且 `actual` 为空的比赛；只表示缺少已核验的 90 分钟 H/D/A。 / Past matches without verified 90-minute H/D/A results.
- `历史复盘`: `actual = H/D/A` 后自动结算盈亏、命中率和 ROI；淘汰赛如果 90 分钟打平，哪怕加时或点球分出胜负，也应填 `D`。 / Settled review after `actual = H/D/A`; knockout matches tied after 90 minutes should be `D` even if extra time or penalties decide advancement.
- `未来赛程`: `date > today` 且 `actual` 为空的比赛；默认折叠，不计入今日实际下注、完赛待补或历史复盘。 / Future matches are folded by default and excluded from today/history stats.
- 页面队名使用中文展示，模型内部仍使用英文队名识别。 / Team names are displayed in Chinese while the model still uses English identifiers internally.

## CSV 数据格式 / CSV Format

编辑 `data/china_sp_review.csv`，每行一场比赛。以 `#` 开头的行是注释，会被程序忽略。

Edit `data/china_sp_review.csv`, one match per row. Lines starting with `#` are ignored.

```csv
date,stage,home,away,neutral,sp_home,sp_draw,sp_away,actual
TBD,小组赛 A组 第1轮,Mexico,South Africa,false,,,,
TBD,1/8决赛 第1场,TBD,TBD,true,,,,
```

字段说明 / Fields:

- `date`: 比赛日期；未确认可写 `TBD`。 / Match date; use `TBD` if not confirmed.
- `stage`: 赛段，例如 `小组赛 A组 第1轮`、`1/8决赛 第1场`、`决赛`。 / Stage label for display only.
- `home`: 项目识别的英文主队名；未定写 `TBD`。 / Home team recognized by the project; use `TBD` if unresolved.
- `away`: 项目识别的英文客队名；未定写 `TBD`。 / Away team recognized by the project; use `TBD` if unresolved.
- `neutral`: `true` 或 `false`；主办国主场场次可设为 `false`。 / Whether the match is neutral.
- `sp_home`: 中国体彩主胜 SP。 / China Sports Lottery home-win SP.
- `sp_draw`: 中国体彩平局 SP。 / China Sports Lottery draw SP.
- `sp_away`: 中国体彩主负/客胜 SP。 / China Sports Lottery away-win SP.
- `actual`: `H` / `D` / `A`；未开奖留空；必须是 90 分钟 + 伤停补时结果，不含加时赛和点球。 / Final 90-minute result including stoppage time, excluding extra time and penalties; leave blank before settlement.

`actual = H/D/A`: `H = home win`, `D = draw`, `A = away win`.

如果你要手动发给我历史复盘数据，请按下面格式给： / If you provide historical review data manually, use this format:

```csv
date,home,away,home_score_90,away_score_90,actual
2026-07-06,Brazil,Norway,0,2,A
```

`actual` 可以留空，只要给 `home_score_90` 和 `away_score_90`，程序会自动推导 H/D/A。 / `actual` may be blank if `home_score_90` and `away_score_90` are provided.

结果 CSV 必须使用 90 分钟 + 伤停补时赛果，不含加时赛和点球。淘汰赛 90 分钟平局就填 `D`，哪怕最后通过加时或点球晋级。 / The result CSV must use the 90-minute score including stoppage time, excluding extra time and penalties. In knockout matches, a draw after 90 minutes is `D` even if extra time or penalties decide advancement.

## 策略说明 / Strategy Modes

默认策略是 `favorite-flat`：

- 每场完整 SP 比赛必须模拟下注。
- 每场固定 100 元，默认最小单位 1 元。
- 只买模型概率最高的一项。
- 适合当前项目“每场都做预测与复盘”的需求，也避免纯 edge 策略追逐低概率高赔率冷门。

`prob-split` 是旧的概率拆分复盘：

- 每场 100 元按模型概率拆到主胜/平局/客胜三项。
- 只用于对照，不作为推荐策略。
- 因为命中项返还可能小于三项总投入，所以命中也可能亏。

`edge-flat` 是价值筛选对照：

- 只在正 edge 且满足稳健条件时下注。
- 可以观望，因此不适合“每场必须下注”的需求。
- 适合作为策略层比较，不用于训练模型。

`kelly` 是 fractional Kelly 高级对照：

- 基于 edge 和赔率计算下注比例。
- 默认 `--kelly-fraction 0.25`。
- 只作为策略层参考，默认主页面仍使用 `favorite-flat`。

The default strategy is `favorite-flat`: every complete-SP match receives one fixed 100-yuan simulated stake on the model favorite. `prob-split`, `edge-flat`, and `kelly` are comparison modes only; none of them feed SP or odds into the model.

## 方法说明 / Method

复盘使用原项目已有的赛前独立模型：冻结的 FIFA 快照、Transfermarkt 阵容身价、无泄漏 Elo，以及 Klement 风格结构先验（GDP、人口、气候、主场优势、足球文化）。市场赔率和中国体彩 SP 只用于预测后的比较和复盘，绝不作为模型输入。

The review uses the original market-independent pre-match model: frozen FIFA snapshot, Transfermarkt squad values, no-leakage Elo, and Klement-style structural priors such as GDP, population, climate, home advantage, and football culture. Market odds and China Sports Lottery SP are used only after prediction for comparison and review, never as model inputs.

```text
fair_odds = 1 / probability
edge = probability * SP - 1
pnl = stake_on_actual * sp_actual - total_stake
```

默认命令每场完整 SP 比赛固定模拟 `100` 元，最小单位 `1` 元，并只买模型概率最高的一项：

```bash
wcforecast china-sp-review --strategy favorite-flat --bankroll 100 --unit 1
```

The default command uses a fixed simulated `100` yuan stake per complete-SP match and buys only the model's highest-probability outcome:

```bash
wcforecast china-sp-review --strategy favorite-flat --bankroll 100 --unit 1
```

## 网页 / Pages

本地打开：

```text
docs/china-sp-review.html
```

GitHub Pages:

[https://greenhand011.github.io/worldcup-forecast-china-sp/](https://greenhand011.github.io/worldcup-forecast-china-sp/)

## 免责声明 / Disclaimer

本项目仅用于模型学习、复盘和数据分析，不构成投注建议。中国体彩 SP 需要用户从合法渠道手动录入。模型概率不是保证，历史盈亏不能证明长期存在 edge。请遵守所在地法律法规，理性对待风险。

This project is for model learning, review, and data analysis only. It is not betting advice. China Sports Lottery SP values must be entered manually from legal sources. Model probabilities are not guarantees, and historical P&L does not prove a persistent edge. Please follow local laws and regulations and treat risk responsibly.

## 致谢 / Credits

本项目基于 `playmobil/worldcup-forecast` 的结构化 + 贝叶斯世界杯预测模型进行改造。原模型逻辑、训练流程和市场独立性原则保持不变；本仓库新增的是中国体彩 SP 手动复盘展示层。

This project is adapted from the structural + Bayesian World Cup forecaster in `playmobil/worldcup-forecast`. The original model logic, training flow, and market-independence principle are preserved; this repository adds a manual China Sports Lottery SP review layer.
