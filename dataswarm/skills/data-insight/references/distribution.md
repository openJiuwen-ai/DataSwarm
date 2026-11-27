# distribution：自定义代码中的分布与集中度helper

计算均值、中位数、四分位、Top份额和HHI。它不是data-insight的algorithm名称，通过compute.code或compute-results.code中的helper调用。

## 输入与参数

调用`helpers['distribution'](values, top_n=3)`。values为非空非负数值序列，先处理缺失和无效数值；top_n为正整数。取数前明确每一项代表的实体与完整范围，不仅保留TOPN；Top份额和HHI需要全体分母。

## 输出与解读

返回count/sum/mean/median/q1/q3/top_share/hhi；四分位采用inclusive方法，单值四分位均为该值。top_share为最大top_n项之和除以全体和，hhi为各项份额平方和，都是0到1比例；总和为0时两者为None。统计不证明成因，不把截断子集声称为全量。

helper返回统计字典，登记证据须显式转成表格：

```python
stats = helpers['distribution']([float(row[params['field']]) for row in inputs[params['input_id']]],
                                top_n=params.get('top_n', 3))
output = {'columns': ['metric', 'value'], 'rows': [[key, value] for key, value in stats.items()]}
```

分析内以evidence_refs声明输入、reason说明方法，params提供input_id、field及可选top_n。inputs按证据ID索引完整CSV行，单元格为字符串；不得抄预览值。也可定义run(inputs,params,helpers)返回表格。只处理声明输入与临时产物，不查询数据库或读取外部文件；不含NaN/Infinity。
