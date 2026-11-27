# driver_correlation：驱动相关性与控制检验

## 输入与参数

CSV每行代表同一观测单位，指标、驱动与控制列在该粒度对齐。保留实际样本范围、缺失处理及必要控制变量，不将多实体、多频率或时间错位数据直接比较。数值列不可全为常量，分析前先确认数据可用。

| 参数 | 用法 |
|---|---|
| metric_field | 必填，指标列 |
| driver_fields | 必填，候选驱动列数组 |
| control_fields | 可选，实际有依据的控制列数组 |
| algorithm | spearman默认，或pearson/kendall |
| alpha | 默认0.05，统计检验阈值 |
| with_matrix | 默认true，符合条件时返回矩阵 |

```bash
python "{skill_directory}/scripts/analyzer.py" driver_correlation --data "/absolute/input.csv" --params '{"metric_field":"response","driver_fields":["driver"],"control_fields":["control"],"algorithm":"spearman"}'
```

分析内algorithm为driver_correlation，params同上；evidence_refs选择完整CSV，多输入时input_evidence_ref指定所用表。

## 输出与解读

返回各驱动coef、p、is_significant、magnitude、sign、coef_ci95及可选control_test。报告实际方法、样本范围和可用区间，不编造缺失输出。

- coef表示关联方向和强度，不能换算成业务效应倍数；显著不等于业务重要性或因果。
- 有控制变量且初始关联显著时才计算control_test；为空不能声称已控制混杂。
- 控制后仍显著只支持在这些控制与方法下存在关联，不排除未观测混杂。控制后不显著表示当前证据不足，不能断定原关联一定由控制变量造成。
- 系数无定义不等于没有关联；宽区间、样本不足及多项比较应按实际方法和输出披露，不能凭候选排名断言主因。

先看self_check.errors、guidance及disclaimer，不可复算的计算不引用；低置信推断保留局限，仍可报告已核验事实。业务建议需其他证据支持，统计相关不能直接转为因果行动效果。
