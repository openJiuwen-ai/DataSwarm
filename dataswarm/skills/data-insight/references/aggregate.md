# aggregate：自定义代码中的分组聚合helper

用于已有证据行的分组sum/count/mean/median/min/max；不是data-insight的algorithm名称，通过compute.code或compute-results.code中的`helpers['aggregate']`调用。

## 输入、参数与结果

取数前保留分组列、度量列及实际粒度，确认范围与可加性。调用`helpers['aggregate'](rows, by, measures)`：rows为完整行字典列表，by为分组列数组，measures为`{输出列:(输入列,方法)}`。返回`{columns,rows}`表格，可设为output。

按by实际值分组，度量None和空字符串被忽略。count计非空；其他方法将非空度量转float。某组全空时sum/count为0，其他方法为None；这与table_ops默认全空sum为null不同，按业务含义选择，不暗中补零。

```python
output = helpers['aggregate'](inputs[params['input_id']], params['group_by'],
                              {'total': (params['value_field'], 'sum')})
```

分析内声明evidence_refs及reason，以params提供input_id、group_by和value_field；inputs按证据ID索引完整CSV字符串单元格。若用函数形式，定义run(inputs,params,helpers)返回上述表格。只消费声明输入，不自行查库或访问外部文件；输出需有界且可JSON序列化。输入不适用时修参数或计算，缺必要数据才交回取数层。
