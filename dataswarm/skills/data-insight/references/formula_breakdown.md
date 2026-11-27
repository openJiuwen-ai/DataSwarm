# formula_breakdown：业务公式因子分解

## 输入与参数

需要已确认的业务恒等式expression及两期全部因子值；先核对各因子单位、范围与分子分母衔接。精确Shapley支持最多8个因子。算法分配算术效应，不自动识别因果。

两种输入模式二选一：

- 直接值：baseline/current为因子名到真实数值的对象，expression使用这些名称；独立CLI使用此模式。
- 分析证据绑定：input_evidence_ref选已声明CSV，period_field指定期间列，baseline_period/current_period指定两期值，factors为公式因子名到实际列的映射，expression为公式。每个期间须恰好一行，多实体先显式filter/aggregate；不抄预览数字为常量。

可选algorithm为shapley、one_at_a_time或delta_sum，默认精确Shapley；按实际方法解释，不将其他分解法称为Shapley。

```json
{"input_evidence_ref":"实际证据ID","period_field":"期间","baseline_period":"上期","current_period":"本期","factors":{"quantity":"数量","unit_value":"均值"},"expression":"quantity*unit_value"}
```

以上作为compute/compute-results的params，algorithm为formula_breakdown；evidence_refs包含输入，reason说明方法。

```bash
python "{skill_directory}/scripts/analyzer.py" formula_breakdown --params '{"baseline":{"quantity":10,"unit_value":5},"current":{"quantity":12,"unit_value":6},"expression":"quantity*unit_value"}'
```

## 输出与解读

返回baseline_result/current_result、net_delta、terms、leading_term、份额与self_check。effect为有符号因子效应，按原始单位解释；效应闭合只说明数值一致，不证明业务机制。

gross_share_pct为`effect / Σ|effect| × 100`，称占绝对变化总量的份额；net_share_pct为effect/净变化，仅在net_share_reliable=true时使用。净变化接近零或涨跌相抵时分别说明效应，不引用不稳定净占比。例：效应+60/-40，净增20，毛份额+60%/-40%，净份额300%/-200%，不得混称。

不将重叠维度凑成解释率，不由最大因子份额推导未解释比例。检查self_check.errors及方法限制，不可复算或恒等式不成立的结果不引用；推断置信度低时可保留核验事实，不能据此编造成因。
