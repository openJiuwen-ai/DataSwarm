# custom_code：自定义证据计算

用于现成算子不适配或组合成本更高的计算。方法不限清单，可使用已安装Python库。仅在分析compute/compute-results中用code调用，与algorithm二选一；custom_code是说明主题名，不是algorithm可执行名称。

## 输入与输出

evidence_refs声明全部输入，reason说明方法，params提供参数。inputs始终为`{evidence_id: list[dict[列名,字符串单元格]]}`，即完整CSV行，单输入也用`inputs[实际证据ID]`读取。取数前根据所需方法确定列、粒度、关联键、期间与范围，按需转换类型和处理缺失，不抄预览数字为常量。

设置`output={"columns":[...],"rows":[...]}`，或`{产物名:{"columns":[...],"rows":[...]}}`生成多表；也可定义`run(inputs, params, helpers)`返回相同结构。输出须有界、可JSON序列化，不含NaN/Infinity，精度保留到展示。

```python
rows = inputs[params['input_id']]
output = {'columns': ['total'], 'rows': [[sum(float(row[params['field']]) for row in rows)]]}
```

## 执行边界

只处理声明输入和临时目录，不读取外部文件、连接数据库/网络或启动子进程；已安装库加载可用。执行器校验输入hash与血缘，失败后pending_computation保留代码、参数和输入供局部修正，不因计算失败重查原数据。

可选helpers包含aggregate和distribution，只有用到时读取对应独立文件，不默认调用。输出登记前核对口径、范围、可加性与计算结果，代码运行成功不证明业务正确。
