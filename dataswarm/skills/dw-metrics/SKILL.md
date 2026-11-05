---
name: dw-metrics
description: 指标语义层 V2。当用户提到"指标问答/销售额多少/看下GMV/客单价/指标趋势/有哪些指标/什么叫某指标/指标定义/导入指标/Ossie/发布指标/指标血缘依赖/metric/指标取数"等指标相关的查数、找指标、导入发布诉求时使用。指标问答走服务端确定性编译（权威口径）；裸表探索走 nl2sql。
layer: L3
allowed_tools: [bash, read_file]
---

# dw-metrics

数仓**指标语义层 V2**：Ossie 导入 → 修订/发布 → 语义投影 → 双通路取数。

> **API 化**：全部数据访问经 `dwcli`（`python -m jiuwenswarm.cli.dwcli`，默认
> 端口自动发现（`DWAGENT_BASE` 可覆盖）走 web API。**要求 web 服务
> 常驻**；不可达时脚本退出码 2 并提示连接失败。

## 边界（与相邻 skill 分工，MUST 遵守）

| 诉求 | 用哪个 |
|------|--------|
| 指标问答/趋势/按维度看指标（已建模） | **本 skill** |
| 导入 Ossie 指标模型 / 发布修订 / 确认口径映射 | **本 skill**（写操作，需用户明确要求） |
| 裸表探索式分析（没建模、临时取数） | `nl2sql` |
| 看板管理 | `dw-bi-dashboard` |

## ⛔ 硬性约束

| # | 约束 |
|---|------|
| M1 | 查询类子命令（list/get/context/releases/health/queries/query）只读；导入/发布/确认/反馈是写操作，**只在用户明确要求时执行** |
| M2 | 指标取数必须走 `query` 子命令（服务端编译），**禁止自写 SQL 绕过语义层** |
| M3 | query 结果落 CSV artifact，返回路径 + 行数，**绝不内联数据正文** |
| M4 | 服务端错误信息可回喂自修正：先 `get` 核对维度/粒度再重试，**最多 2 次** |
| M5 | 无治理指标覆盖的裸表诉求不在本 skill 执行：交给 `nl2sql`（其内部经 `/v2/explore` 受控探索），并向用户说明这是探索口径（非权威） |

## 路径解析

先调 `skill_tool(skill_name="dw-metrics")` 拿 `skill_directory` 替换下文路径。不要用 `~`。

## 命令

```bash
S={skill_directory}/scripts/metric_query.py

# 只读
python $S list                                    # 指标清单（metric_key/单位/是否已发布）
python $S get --key commerce.gmv                  # 详情：修订历史/口径/维度/粒度/依赖/待确认映射数
python $S context --key commerce.gmv              # 语义上下文：概念/分子分母/字段（需已发布）
python $S releases                                # 发布批次与投影状态
python $S health                                  # 语义层健康与能力
python $S queries --limit 20                      # 查询追溯（含 path/authoritative/错误码）

# 取数（path-mode CSV）
python $S query --key commerce.aov --group-by orders.region \
  --time-dimension orders.paid_at --start 2026-08-01 --end 2026-09-01 [--grain month] \
  [--filters "orders.region=east"]

# 写操作（需用户明确要求）
python $S import-ossie --file /path/to/metrics.ossie.yaml
python $S confirm --mapping-id map_xxx [--reject] --by user          # ratio 分子分母必经确认
python $S publish --revision-ids rev_a,rev_b --label "首次发布" --by user
python $S feedback --query-id mq_xxx --feedback verified --by user   # 权威 SQL 固化复用
```

## 工作流

**指标问答**：`list`/`get` 找到指标 → 从问题抽参数（时间范围/维度/粒度，必须在该指标声明范围内）→ `query` 取数 → 按 CSV 路径回答（主 Agent 自行从问题抽参数产 QuerySpec，服务端零 LLM 确定性编译）。指标体系覆盖不到的裸表诉求走 `nl2sql`（见 M5）。

**导入发布**：`import-ossie` → `get` 查看候选映射（`pending_concept_maps`）→ 用户确认后逐条 `confirm`（ratio 指标的分子/分母角色**必须确认**才进正式投影）→ `publish`（投影失败自动保旧版本）→ `context` 验证投影结果。导入响应的 `unresolved_concepts` 列出**术语库没对上的业务概念**（这类映射确认了也连不出图边），按需提示用户去术语库补术语或改用下面的绑定命令。

**术语绑定**（让分析提问能直达该指标口径）：
```bash
python $S terms --key commerce.gmv                                  # 看已绑术语
python $S bind-term --key commerce.gmv --term-id term_xxx           # 绑定（直接生效，无需 confirm）
python $S bind-term --key commerce.gmv --term-id term_xxx --unbind  # 解绑
```
- `--term-id` 是**术语 ID**（从 `dwcli glossary resolve` 或术语库页取），不是术语名
- 绑定后**发布**才对分析生效；已发布指标的绑定响应带 `reproject_required`（图边等重投影，取数不受影响）
- 响应 `warnings` 含 `term_multi_metric` 表示该术语已绑别的指标——此时分析无法直达，需用户二选一

**时间参数**：`--start` 含、`--end` 不含（开区间）；"上个月"换算成具体日期再传。

## 产出约定

- 元数据类（list/get/context/releases/health/queries）：JSON 直出
- query：`{"csv_path": ..., "row_count": N, "authoritative": bool, ...}`，数据只在 CSV

## 红线

指标定义（`dw_metric_*` 表）**只经 `/api/dataagent/metrics/v2/` API 读写**。严禁直连元数据库写 INSERT。Ossie 导入产物是 candidate 修订——**格式合法不等于口径正确**，发布前必须让用户确认候选映射。
