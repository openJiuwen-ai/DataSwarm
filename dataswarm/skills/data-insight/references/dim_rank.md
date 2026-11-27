# dim_rank：多维变化分化比较

## 输入与参数

CSV须包含metric_field、dim_fields数组中的实际维度列；比较两期时提供可解析time_field、用户指定比较范围和完整成员。观测粒度、度量定义及数据覆盖保持一致，不只取TOPN行，不将维度分化排名当因果排名。

必填metric_field与dim_fields。可选time_field、baseline_window/current_window与split_mode：显式区间优先且两端均包含；halves按时间中点二分，last_two比最后两个时间点。不传time_field按行号前后二分，仅在实际行序适配时使用。explicit与halves均按各段实际出现的不同时间值数量归一，不补缺失日；日级完整输入可称日均，其他频率按相应观测单位说明，不能当成总量差。

```bash
python "{skill_directory}/scripts/analyzer.py" dim_rank --data "/absolute/input.csv" --params '{"metric_field":"amount","dim_fields":["region","channel"],"time_field":"day","baseline_window":["2026-06-01","2026-06-30"],"current_window":["2026-07-01","2026-07-31"]}'
```

分析内用algorithm=dim_rank，params同上，evidence_refs声明输入。已有完整结果足够时本地准备列和粒度；不为算子增加无关取数。可加性不明或跨组重叠时先查[指标类型与周期聚合](metric-types.md)，不要把去重数或比率当可加总量。

## 输出与解读

比较多个维度的成员变化率分化，返回维度排名、领先维度与self_check。分化高提示可核查方向，不证明该维度是主因；分化低也不能推出原因来自整体因素。结合成员绝对变化、基数与完整性判断价值，不默认逐个展开所有维度。

需要单维具体贡献时按该问题选dim_breakdown。若引用返回的毛份额，其分母为绝对变化总量；净份额仅在可靠且可加时使用，净变动接近零不能用不稳定净占比。重叠维度的贡献不相加为解释率。

先看self_check与guidance，无法复算的结果不引用；推断置信度低时保留已核验事实并披露局限，不能为维度排名编造成因。
