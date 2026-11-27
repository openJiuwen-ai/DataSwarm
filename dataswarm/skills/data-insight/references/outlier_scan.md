# outlier_scan：单点离群

## 输入

需要目标粒度下的数值列；涉及时间周期时提供可解析时间列，并明确频率和覆盖。有真实活动日历时可准备0/1标记列，不编造活动依据。数据有周期而不提供时间信息时可能将正常周期误判为离群。

## 参数与调用

| 参数 | 含义 |
|---|---|
| metric_field | 必填，实际数值列 |
| time_field | 可选，实际时间列；启用自动星期周期识别所需信息 |
| skip_flag_field | 可选0/1列；标1仍参与去趋势，不参与基线估计且不报离群 |
| z_cutoff | 默认3.0，低阈值提高召回也可能提高误报 |
| seasonality | 默认auto，按实际时间信息处理星期周期 |
| robust | 默认true，采用鲁棒偏离 |

```bash
python "{skill_directory}/scripts/analyzer.py" outlier_scan --data "/absolute/input.csv" --params '{"metric_field":"amount","time_field":"day","skip_flag_field":"event_flag","z_cutoff":3.0}'
```

分析内algorithm为outlier_scan，params同上，evidence_refs声明完整CSV；没有标记列时不填skip_flag_field。

## 输出解读

结果按z降序，返回候选点、seasonal_adjustment、outlier_rate_pct和self_check。z是相应基线下的标准化偏离，不是业务值倍数、事故概率或故障证明。高离群比例可能提示周期、基线或数据质量问题，依据具体警告核对。

本算子只检查单点离群。持续位移或结构变化需按问题选择分段比较、位移检测或维度拆解，未检出单点不等于不存在变化；正常波动也不强行编造原因。不可复算的结果不引用，低置信推断注明局限。
