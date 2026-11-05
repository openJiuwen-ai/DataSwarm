---
name: data-insight
description: 数据分析与统计洞察**计算层**，输入是 CSV 路径，只做统计计算——**不取数、不连库、
  不出图、不写报告**。能力：指标变动拆解（哪个维度的哪些成员推动或拖累了变化；按业务公式
  GMV=访客数×转化率×客单价 拆因子效应）、走势与离群（环比、整体方向、星期周期、离群点）、
  驱动相关性（可控制干扰变量，识别「表面相关、实为第三个变量带动」的情况）。
  当用户问"为什么涨了/跌了""是哪个地区/渠道的问题""趋势如何""最近有没有异常"
  "什么影响了X""X和Y有关系吗"时使用。取数由调用方完成（编排层经 nl2sql 构造 SQL 落到
  CSV，或用户直接提供 CSV 文件）——**本技能不包含数据库/数仓取数流程，不写死任何库名**。
version: 2.0.0
tags: [analysis, breakdown, trend, correlation, insight]
allowed_tools: [bash, read_file]
---

# data-insight

**路径解析：先调用 `skill_tool(skill_name="data-insight")` 拿到 `skill_directory`，下文所有 `{skill_directory}` 都用它替换。**
不要用 `~` —— agent 的 bash 在 Windows 上不展开波浪号。

统计计算层。**只做计算，不取数、不出图、不写报告。**

## 数据从哪来（上游给 CSV，本技能不取数）

除 `formula_breakdown` 外，所有 op 都读一个 CSV。CSV 由调用方提供，**本技能不写死任何库名**：

- **编排层调用**（推荐）：`dw-analysis` 先用 `nl2sql` 构造 SQL、从目标库（当前工作空间绑定的业务库）取数落地 CSV，把返回的 `export.file`（CSV 文件路径）原样传给 `--data`。
- **直接调用**：调用方手上有现成 CSV 文件，把路径给 `--data` 即可；没有 CSV 时先走取数，不要向本技能要数据库连接。

本技能**不连库、不拼 SQL、不让用户提供数据库地址**。CSV 不存在或为空时返回
`invalid_request` / `too_few_rows`，`error` 字段会写明原因，据此补取数即可。

## 调用形态

```bash
python {skill_directory}/scripts/analyzer.py <op> --data <csv> --params <json>
```

- 第一个位置参数是 op 名；`--data` 是 CSV 路径；`--params` 是全部业务参数组成的 JSON。
- 只有这三项。`metric_field` 这类业务参数都写进 `--params`，不要写成单独的命令行参数。
- 命令一律用**绝对路径**，与 jiuwenswarm 其他内置技能一致。

算子依赖 pandas / numpy / scipy。若报 `ModuleNotFoundError`，先安装：

```bash
pip install -r {skill_directory}/requirements.txt
```

### 执行环境是 Windows cmd（拼命令前必读）

`bash` 工具在 Windows 上由 **cmd.exe** 执行，不是 bash。拼命令遵守：

- 每条命令写**一行**；先把 `{skill_directory}` 替换成 `skill_tool(skill_name="data-insight")` 返回的绝对路径再执行。
- 禁止 bash 语法：`VAR="..."` 变量赋值、`$VAR` 引用、`/tmp/...` 路径、行尾 `\` 续行、`#` 注释行——cmd 不认，会直接报 `'...' is not recognized`。
- `--data` 与 `--params` 的值**不要加引号包裹**；JSON 写紧凑单行、路径不含空格即可。

## 六个 op

| op | 做什么 | 必填参数 | 可选参数 |
|---|---|---|---|
| `trend_profile` | 环比、整体方向、星期周期 | `time_field`, `metric_field` | — |
| `outlier_scan` | 离群点（去周期后的鲁棒偏离） | `metric_field` | `time_field`, `skip_flag_field`, `z_cutoff`(默认 3.0), `seasonality`, `robust` |
| `dim_breakdown` | 单维拆解，**上升成员与下降成员分两榜** | `metric_field`, `dim_field` | `time_field`, 区间参数, `metric_kind` |
| `dim_rank` | 多维对比，找出成员分化最明显的维度 | `metric_field`, `dim_fields[]` | `time_field`, 区间参数 |
| `formula_breakdown` | 按业务公式拆因子效应（默认精确 Shapley，无需 CSV） | `baseline{}`, `current{}`, `expression` | `algorithm` |
| `driver_correlation` | 驱动相关性 + 控制干扰变量后的检验 | `metric_field`, `driver_fields[]` | `control_fields[]`, `algorithm`（spearman 默认 / pearson / kendall）, `alpha`(默认 0.05), `with_matrix`(默认 true) |

### 区间参数（dim_breakdown / dim_rank）

传了 `time_field` 后，三选一：

- `baseline_window` + `current_window`：显式两段，如 `["2026-06-15","2026-07-12"]`。**两段天数不同时自动按日均比较。**
- `split_mode: "halves"`（默认）：按时间中点二分
- `split_mode: "last_two"`：只比最后两个时间点

不传 `time_field` 时按行号前后二分。

### 离群检测怎么调（默认参数会误报）

日级业务数据通常有星期周期和促销日，**只传 `metric_field` 会把周末和促销日报成离群**。
把「这是什么日子」告诉算子：

```cmd
python {skill_directory}/scripts/analyzer.py outlier_scan --data {csv_abs_path} --params {"metric_field":"gmv","time_field":"ds","skip_flag_field":"promo_flag","z_cutoff":3.0}
```

- `time_field`：给了才会自动识别 7 天周期并先做分解（结果写在 `seasonal_adjustment`）
- `skip_flag_field`：0/1 标记列。标 1 的日子仍参与去趋势，但不参与基线估计，也不会被报成离群。
  没有这一列就在**取数 SQL** 里造一个
- `z_cutoff`：调精确与召回的旋钮。实测 180 天数据、3 个真异常：
  `3.0` → 召回 100% / 精确 60%，`5.0` → 召回 33% / 精确 100%。默认偏向召回

结果按 `z`（偏离倍数）降序。3~4 倍的点属于待排查，不要直接断言为事故；
`outlier_rate_pct` 超过 10% 时 `self_check` 会给出警告，通常说明周期没有去干净。

**本 op 只检测单点离群。**「某天起持续下移」属于水平位移，要用 `dim_breakdown` 按维度拆解后看。
无检出时 `guidance` 字段会提示这一点。

### 走势方向怎么判

`trend_profile` 比较首尾两个窗口（各 `window_size` 个点）的均值，得到 `net_change_pct`。
`|net_change_pct|` 不超过 `flat_band_pct` 时 `movement` 为 `flat`，否则为 `rising` / `falling`。
`flat_band_pct` 最小 3%，序列越嘈杂越宽（`noise_band_pct` 为噪声给出的部分）。

## 典型调用

六行依次为：走势 / 多维对比（推荐入口）/ 单维展开 / 公式拆解（无 CSV）/ 离群检测 / 驱动相关性。
用哪条跑哪条：把 `{skill_directory}` 换成实际路径、`{csv_abs_path}` 换成上游 CSV 绝对路径，单行执行，删掉其余。

```cmd
python {skill_directory}/scripts/analyzer.py trend_profile --data {csv_abs_path} --params {"time_field":"ds","metric_field":"gmv"}

python {skill_directory}/scripts/analyzer.py dim_rank --data {csv_abs_path} --params {"metric_field":"gmv","dim_fields":["region","channel","device"],"time_field":"ds","baseline_window":["2026-06-15","2026-07-12"],"current_window":["2026-07-16","2026-08-12"]}

python {skill_directory}/scripts/analyzer.py dim_breakdown --data {csv_abs_path} --params {"metric_field":"gmv","dim_field":"region","time_field":"ds"}

python {skill_directory}/scripts/analyzer.py formula_breakdown --params {"baseline":{"visitors":86000,"conv_rate":0.031,"aov":158},"current":{"visitors":82000,"conv_rate":0.022,"aov":160},"expression":"visitors*conv_rate*aov"}

python {skill_directory}/scripts/analyzer.py outlier_scan --data {csv_abs_path} --params {"metric_field":"gmv","time_field":"ds","skip_flag_field":"promo_flag"}

python {skill_directory}/scripts/analyzer.py driver_correlation --data {csv_abs_path} --params {"metric_field":"gmv","driver_fields":["ad_spend","search_impressions","coupon_rate"],"control_fields":["promo_flag","holiday_flag"]}
```

> CSV 从哪来：调用方参考输入中的取数结果（`dw-analysis` 编排时是 nl2sql 返回的 `export.file`），
> 原样填入 `--data`。本技能不产 CSV。

## 三条容易踩的坑

**① `time_field` 必须能解析成日期**

`2026-W07`、`2026Q1` 这类周/季标签**解析不了**，会返回 `invalid_request`。
要按周或按月分析，在**取数 SQL** 里产出**该周期的起始日期**：

```sql
-- 可以：周一的日期
SELECT date(ds, 'weekday 0', '-6 days') AS week_start, region, SUM(amount) gmv
FROM orders GROUP BY week_start, region

-- 不行：strftime('%Y-W%W', ds) 产出 '2026-W07'
```

**② 数字全部由算子算，不要自己心算**

需要中间量（某段区间的合计、日均）就再发一条**取数 SQL**。算子返回的每个数字都有 `self_check` 复核。

**③ 落地 CSV 被截断会改变拆解结论**

实测 5350 行的维度表被截到 5000 行后，净/毛比从 0.076 变成 0.387，排名跟着变。
取数时在 **SQL 里聚合收敛行数**（例如只按「天 + 单个维度」聚合）。
CSV 行数与预期量级不符时，回到取数层改 SQL，不要硬扩 limit。

## 对比区间怎么选

1. **先看走势再定区间。** 用 `trend_profile` 找到拐点/峰值，再拿拐点前后做对比。
2. **按业务周期对齐。** 日级数据有星期效应，区间长度取 7 的倍数。
3. **区间按业务理由选**（「峰值期 vs 最近」「上月 vs 本月」），不要试多组后挑数值最显著的那组。

两段天数不同时算子按日均比较，叙述里要说明对比的是**日均**而非总量。

## 输入粒度要求

CSV 应已是目标列、目标粒度。派生比率列（`a/b`）、按周/月重采样、`WHERE` 过滤、`JOIN`
都在**取数层**完成，例如要按月看转化率，取数 SQL 直接产出 `月份, 转化率` 两列。
收到此类需求时算子返回 `invalid_request`。

## 读结果的四条规矩

**⓪ 本技能只做计算，不取数**
取数由上游完成（编排层经 `nl2sql` 落地 CSV，或用户直接给 CSV 文件），把 CSV 路径传给本技能即可。
**本技能不自带数据库/数仓取数流程，不写死库名**，也不会向用户索要数据库连接。

**① 先看 `self_check.confidence`**
`low` 时**不要播报具体数字**，改为提示结果可能不可靠，原因在 `self_check.errors`。

**② 拆解类先看 `guidance` 字段**
它会说明能否下结论、该用哪个字段叙述。
`net_share_reliable: false` 时**不要使用 `net_share_pct`**（涨跌相抵时它会出现 +964% 这类值且符号可能反转），
改用 `gross_share_pct`（占变动总量的份额）。

**③ 离散度低就不要硬给主因**
`divergence_level` 为「低」表示各成员变化率高度同步，波动来自整体而非该维度。
如实说「未找到集中的解释」。

**④ 相关性必须转述 `control_test`**
某驱动列显著、但 `control_test.holds_after_control: false`，说明关联更可能来自控制变量。
这种情况必须说清楚。`coef_ci95` 是系数的 95% 置信区间，样本小时区间会很宽。
返回里的 `disclaimer` 字段要原样带上。

## 措辞规范

做拆解或相关性叙述前先读：

```bash
cat {skill_directory}/references/narration.md
```

核心一条：不写「根因是 X」，只写「X 占本次变动的 N%，建议优先核查」。

## 指标类型

`dim_breakdown` 的 `metric_kind` 参数：`additive`（默认，如订单量/GMV）、
`ratio`（转化率等率型）、`distinct`（UV/DAU 等去重指标）。

**去重指标必须显式传 `"metric_kind":"distinct"`**——各维度去重之和远大于总量
（例见 `references/metric-types.md`），不声明会让自洽校验持续误报。

判定规则：

```bash
cat {skill_directory}/references/metric-types.md
```
