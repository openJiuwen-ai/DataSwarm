# trend_profile：走势与周期

## 输入

CSV须含可解析的时间列和数值列，至少3个有效时点，按问题准备目标频率与粒度，保留完整时间范围，不只取TOPN时点。算子将同一时点的多行数值求和，非可加度量须先准备正确的每时点值。周/季标签先转周期起始日期，明确缺失日期、实体和不等距观测的处理。准备可由SQL或已有证据计算完成。

## 参数与调用

必填`time_field`、`metric_field`，均为实际列名。分析内传`algorithm="trend_profile"`和`params`，用evidence_refs选择完整输入；多证据时input_evidence_ref指定所用CSV。

```bash
python "{skill_directory}/scripts/analyzer.py" trend_profile --data "/absolute/input.csv" --params '{"time_field":"day","metric_field":"amount"}'
```

## 输出解读

返回逐点变化、首尾窗口方向、星期特征及self_check。`movement`是首尾两个窗口均值的判定，各窗口含window_size个点；`net_change_pct`描述该比较，不代替全部时点走势。

`|net_change_pct|`不超过flat_band_pct时为flat，否则rising/falling。flat_band_pct至少3%，噪声越大阈值可能越宽，noise_band_pct说明噪声部分。周期和趋势描述不证明成因，不因flat就忽略期间内变化。

检查self_check.errors、警告和数据覆盖。不可复算的结果不引用；推断置信度低时保留可核验事实并说明局限。百分比变化与百分点变化区分，不能将观测变化声称为因果。
