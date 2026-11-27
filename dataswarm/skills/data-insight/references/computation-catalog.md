# 证据计算能力目录

取数前按问题确认输入是否足够；列名由参数映射。选定方法后，可在写SQL或准备CSV前用describe_compute获取该方法契约。已有证据足够则复用，不为适配算子改变问题，不要求运行所有方法。

| 名称 | 用途与提前需要的输入 |
|---|---|
| [table_ops](table_ops.md) | 过滤、聚合、关联、排序、累计和宽长转换；保留所需字段、类型、关联键与实际粒度，多表输入须口径可比 |
| [trend_profile](trend_profile.md) | 走势与周期；可解析时间列和数值列，明确频率、覆盖与缺失期间，不只取TOPN时点 |
| [outlier_scan](outlier_scan.md) | 单点离群；数值列，周期相关时带时间列，有活动依据时可带真实标记，不等同持续位移检测 |
| [dim_breakdown](dim_breakdown.md) | 单维变化贡献；维度、度量及可区分两期的数据，明确比较期与可加性，完整成员不能被TOPN替代 |
| [dim_rank](dim_rank.md) | 比较维度分化；维度列、度量及比较期，保持观测粒度与覆盖一致 |
| [formula_breakdown](formula_breakdown.md) | 已确认公式的因子效应；两期全部因子值，证据绑定时每期恰好一行，多实体先筛选或聚合 |
| [driver_correlation](driver_correlation.md) | 相关及可选控制检验；同一观测单位对齐的指标、驱动和所需控制列，保留样本与缺失边界 |
| [custom_code](custom_code.md) | 自定义Python及已安装库；声明证据的完整CSV行，输出表格，不复制预览数值为常量，方法不限清单 |
| [aggregate](aggregate.md) | 自定义代码中的分组聚合helper；行字典、分组和度量列，明确可加性与空值处理 |
| [distribution](distribution.md) | 自定义代码中的分布/集中度helper；完整范围内的非负数值序列，Top份额和HHI依赖全体分母 |

计算只消费声明输入，不自行查库；补取仍走受控查询。算子用compute的algorithm/params调用，后两项helper在compute.code中调用。
