# dim_breakdown：单维变化拆解

## 输入与参数

CSV含维度列dim_field与数值列metric_field；比较两期时保留time_field和完整成员范围，先确认度量的实际粒度、可加性与用户比较区间。不能以TOPN成员代表全体贡献。去重用metric_kind=distinct，比率用ratio，默认additive仅在实际可加时适用；定义不明确时查[指标类型与周期聚合](metric-types.md)，不通过猜distinct跳过校验。

| 参数 | 用法 |
|---|---|
| metric_field / dim_field | 必填，实际度量与维度列名 |
| time_field | 可选，含两期的可解析时间列 |
| baseline_window / current_window | 显式两段[起,止]日期；按实际业务选择 |
| split_mode | 无显式区间时，halves默认按时间中点二分，last_two取最后两个时间点 |
| metric_kind | additive / ratio / distinct，控制可加性校验 |

不传time_field时按行号前后二分，仅在行顺序确有相应语义时使用。显式区间两端均包含；explicit与halves按各段实际出现的不同时间值数量归一，不补缺失日，last_two直接比两次观测。因此日级完整输入时是日均，月级时是每月观测均值，不能一律声称比较总量或自然日均。用户有明确区间时沿用，不试多组后挑最显著结果。

```bash
python "{skill_directory}/scripts/analyzer.py" dim_breakdown --data "/absolute/input.csv" --params '{"metric_field":"amount","dim_field":"region","time_field":"day","baseline_window":["2026-06-01","2026-06-30"],"current_window":["2026-07-01","2026-07-31"],"metric_kind":"additive"}'
```

分析内algorithm为dim_breakdown，params同上，evidence_refs选完整CSV；已有数据足够时先本地处理粒度，不为格式重查。

## 输出与解读

返回members、drivers、detractors、net_delta、gross_delta、net_share_reliable、divergence及self_check。推动和拖累分别报告，实际数量由结果决定。

- delta为成员变化量。gross_share_pct为`delta / Σ|delta| × 100`的有符号份额，称占绝对变化总量，不冒充净变化占比。
- net_share_pct为`delta / net_delta × 100`，仅net_share_reliable=true且可加性成立时引用，结合整体方向解释。净变化接近零时分开说明涨跌，不用不稳定净占比。
- 例如+60与-40的净增为20，毛份额为+60%/-40%，净贡献为300%/-200%；两类分母不同。
- divergence低只表示成员变化率分化小，不证明原因来自整体因素。贡献定位数值来源，不证明因果；重叠维度不相加为解释率，不能由最大份额推导未解释比例。
- ratio/distinct跳过部分闭合检查不等于变成可加量。检查self_check.errors及局限，不可复算的结果不引用，低置信推断仍可保留核验事实。
