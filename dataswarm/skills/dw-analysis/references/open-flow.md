# 开放分析计划（mode=open 时读；closed 计划见 fetch-flow.md）

> 仅当 `open_analysis_enabled: true` 且问题是**宽报告问题**（只给主题、未点名具体
> 取数范围）时使用。核心所问拆 ≤3 个 core，其余铺开面一律 optional facet。
> 开关状态：init 返回的 `open_analysis_enabled` 字段与 context 返回的
> `open_analysis.enabled` 字段同源。

## core 成分的指标路径（三步判定，服务端 plan 校验强制）

1. context `materials` 里有 `kind: metric` 素材 → core 成分**必须**把该指标写进
   `selected_metrics` 并在 `material_refs` 引用 `metric:<key>`（权威编译 G1）
2. materials 无 metric 素材 → 先用问题里的业务量词（销售额/订单量等）做一次
   `context --mode recall_more` 再 plan
3. recall_more 后仍无 metric → 允许手写 SQL（G3），但 `material_refs` 只引用
   该成分 SQL 真实用到的列素材（禁止引用无关列凑校验），并在 `uncovered`
   声明「空间未发布指标，走 G3」

**时间列独立通道**：趋势/时间序列成分的时间列写 `time`（dimension+grain=
day/week/month），**不要**写进 `dimensions`——时间走编译的独立通道，不受指标
维度白名单约束（把时间列当分组维度会被 `dimension_not_allowed` 拒）。

## 计划字段

```json
{"mode":"open", "source_id":"<当前数据源ID>",
 "facets":[{"facet_id":"f_region", "label":"区域结构", "required":false,
   "priority":50, "component_ids":["c_region"],
   "material_refs":["<materials 中该维度的稳定 ID>"]}],
 "components":[
   {"component_id":"c_total", "text":"核心总量", "role":"core", "required":true,
    "priority":100, "selected_metrics":["<已召回指标>"],
    "material_refs":["metric:<已召回指标>"], "output":"总量及其单位"},
   {"component_id":"c_trend", "text":"核心时间走势", "role":"core", "required":true,
    "priority":90, "selected_metrics":["<已召回指标>"],
    "material_refs":["metric:<已召回指标>"], "output":"按月及其单位"},
   {"component_id":"c_region", "text":"区域拆解", "role":"facet", "required":false,
    "facet_id":"f_region", "priority":50, "selected_metrics":["<已召回指标>"],
    "dimensions":["<维度列>"], "material_refs":["metric:<已召回指标>","<维度稳定 ID>"],
    "output":"按区域及其单位"}],
 "coverage":{"user_intents":["核心所问"],"assumptions":[]}, "uncovered":[]}
```

> ⛔ **`material_refs` 只能从 context 响应里逐字复制**：取 `packs.materials[].material_id`
> （或 `packs.metrics_candidates[].metric_key`），**不要自己拼**。自拼形态
> （如 `column:<表>:<列>`、`dimension:col:mysql:…`）一律报「不在候选池」。
> 候选池里没有该列 = 该列不在本次召回范围：
> 改用池内的列，或在 `uncovered` 声明「未覆盖」。

## 计划纪律

- 各成分补齐原格式的 time/filters/depends_on；核心所问全部 required（≤4 个），
  总盘与走势优先；再从候选素材选择 1–4 个有意义的 optional 侧面（趋势、结构、
  排名、对比、相关指标只是起笔建议）
- 不足 3 个成分时，在 uncovered 说明素材缺口，**不编指标**
- 不同成分须回答不同问题，**禁止复制同一查询凑数量**
- 先调 `next --analysis-id <id>` 获取下一波成分，required 完成后才执行 optional
- 可选侧面证据不足用 `skip --analysis-id <id> --component-id <cid> --reason <原因>`，
  **不为 optional 口径打断用户**；核心缺业务口径仍必须澄清
- 执行与检查复用 fetch-flow 的 2.3 流程（open 模式下每波只跑无依赖同优先级层，
  下一波由 next 返回；execute 前会重验 selected/time 与计划一致）
