---
name: data-insight
description: 对已有 CSV 做通用表格处理（聚合、排序、TOPN、累计、表达式）及走势、离群、维度/公式分解与相关性计算。返回计算及自检，不取数、不连库、不写报告。
version: 2.0.0
tags: [analysis, breakdown, trend, correlation, insight]
allowed_tools: [bash, read_file]
---

# data-insight

独立调用输入为 CSV 绝对路径；`formula_breakdown` 可直接传因子参数，分析内也可绑定证据列。`dw-analysis` 通过compute/compute-results的evidence_refs选择已登记结果，独立nl2sql可用export.file，也可使用用户已有CSV。核验文件、所需列、范围与粒度；缺失、空文件或粒度不合要求时返回具体缺项，不自行查库、不自动启动分析。用户禁止补取时遵守该限制。分析内部调用继续当前任务，不回顶层重新路由。
算法函数是可选辅助能力，模型可按问题自行选择函数或编写脚本，不限此清单。保留源CSV、算法/参数和计算结果供复核；分析内引用源证据并说明计算方法，证据登记能力以工具实际契约为准。

首次取数已明确的过滤、关联、聚合和排名优先由适度的SQL完成。已有完整结果足够时用table_ops或现有算法复用，不为简单计算重查。保持单条SQL职责清楚，不为实现整个目标拼冗长、多层嵌套或混合粒度的查询；必要时分几条查询后本地组合，保留关联键、口径、范围及必要的一致快照。选择依据是正确性和总成本，不是SQL条数或字符数。

已有结果已含所需事实时直接引用，不为重新排版、凑一张汇总表或重复核验而重算排名、占比和累计。缺少的过滤、聚合、关联、宽长转换、前期值及列间运算优先用table_ops组合；公式因子贡献优先用formula_breakdown，可从证据绑定两期因子列，直接返回贡献金额、占比与自检。只算回答当前问题所需的内容，不默认追加所有统计量。现有操作不适配或组合成本更高时继续用自定义code，不为适配算子改变业务目标。代码从声明的inputs读取真实数据，不把预览行或金额复制为脚本常量；数值精度保留到最终展示，避免先舍入再累加。

## 调用与算子选择

分析worker初始只接收[能力与输入目录](references/computation-catalog.md)。选定方法后，在写SQL或准备CSV前读取下表对应的独立文件，或用describe_compute按名称获取同一文件；已加载说明随任务保留。一次只读取当前所需方法，不预读全部文件。

先用 `skill_tool(skill_name="data-insight")` 取得 `skill_directory`。命令工具显式 `shell_type: bash`，路径双引号包裹，JSON 用单引号保护：

```bash
python "{skill_directory}/scripts/analyzer.py" trend_profile --data "/absolute/data.csv" --params '{"time_field":"ds","metric_field":"gmv"}'
```

首个位置参数为 op；业务参数全部放 `--params`，不拆成 CLI 选项。当前不支持 `--params-file`。
需要跨轮复用或交付表格计算结果时，可加 `--output-dir "/absolute/outputs"`；返回完整输出CSV及输入hash、算法参数和输出hash，原始数据无需重复查询。

| 诉求 | op | 调用前读取 |
|---|---|---|
| 通用表格处理、多步多表 | `table_ops` | [table_ops.md](references/table_ops.md) |
| 走势与周期 | `trend_profile` | [trend_profile.md](references/trend_profile.md) |
| 单点离群 | `outlier_scan` | [outlier_scan.md](references/outlier_scan.md) |
| 多维分化比较 | `dim_rank` | [dim_rank.md](references/dim_rank.md) |
| 单维变化贡献 | `dim_breakdown` | [dim_breakdown.md](references/dim_breakdown.md) |
| 公式因子效应 | `formula_breakdown` | [formula_breakdown.md](references/formula_breakdown.md) |
| 相关性与控制检验 | `driver_correlation` | [driver_correlation.md](references/driver_correlation.md) |
| 自定义Python | `compute.code` | [custom_code.md](references/custom_code.md) |
| 代码中的分组聚合 | `helpers['aggregate']` | [aggregate.md](references/aggregate.md) |
| 代码中的分布与集中度 | `helpers['distribution']` | [distribution.md](references/distribution.md) |

按问题选择所需算法，不默认全部运行；分析方法与先后顺序由调用方根据证据决定。

## 数据与解读门禁

- CSV 必须是目标列、目标粒度；可由上游SQL或本地脚本准备过滤、关联、计算与重采样，保留准备方法。日期列要可解析，周/季标签改为周期起始日期。
- 截断 CSV 会改变拆解结果；行数与预期不符时回取数层聚合或分批，不把部分数据当全集。中间量由 SQL/算子算，不心算补齐。
- `dim_breakdown` 的去重指标显式传 `metric_kind=distinct`；率型传 `ratio` 并按分子分母或业务公式处理，不能把率当可加量。
- 读取返回的self_check与guidance，按所选算子文件中的输出解释和限制形成结论；各文件包含自身参数、输入格式、输出含义及示例，不需加载其他算子。

依赖 pandas / numpy / scipy。仅遇缺依赖时安装 `"{skill_directory}/requirements.txt"` 中的依赖。
