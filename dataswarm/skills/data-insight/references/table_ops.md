# table_ops：通用表格运算

## 输入与调用

用于已有完整CSV上的过滤、聚合、关联、排序、累计、表达式和宽长转换。不预设业务列名；保留所需字段、真实粒度、关联键与类型。CSV标识保留字符串，数值运算前cast为number，日期排序前cast为date；空字符串按null处理。

分析内调用`compute`或`compute-results`，传`algorithm="table_ops"`、`params`、所有输入的`evidence_refs`和`reason`。多输入用`params.input_evidence_ref`选起始表，`join.input`引用另一份已声明证据ID。独立CLI只接收`--data`单表：

```bash
python "{skill_directory}/scripts/analyzer.py" table_ops --data "/absolute/input.csv" --params '{"steps":[{"op":"select","columns":["group","amount"]}]}'
```

## steps参数

| steps[].op | 参数与语义 |
|---|---|
| cast | column、type=number/text/date、可选as；date可指定format；不能转换的值报错 |
| filter | column、operator=eq/ne/gt/ge/lt/le/in/not_in/is_null/not_null、value；in/not_in值为数组 |
| select | columns数组、可选rename映射；输出列不能重名 |
| aggregate | group_by默认[]为全表；measures:[{column,function,as,nulls?}]；function为sum/count/size/nunique/mean/median/min/max；size不需column，nunique可设include_null |
| sort | order_by:[{column,direction:asc/desc,nulls:first/last}]；默认升序、空值后置，并列保持输入顺序；需确定TOPN边界时增加排序键 |
| limit | n为非负整数，按当前顺序取前N；TOPN用sort+limit |
| cumulative | group_by、order_by、measures:[{column,as,nulls?}]；按显式顺序分组累计求和 |
| derive | as、expression运算树；叶节点{column:列名}/{constant:有限数值}；二元节点{op:add/subtract/multiply/divide,left:节点,right:节点}；{op:sum,column:列名,group_by?:[]}广播全表或分组总和 |
| join | input为另一份声明证据ID；on为同名组合键数组，或left_on/right_on为等长键数组；how=left(默认)/inner；columns可选右表保留列（关联键自动保留）；validate=many_to_one(默认)/one_to_one/one_to_many/many_to_many；suffixes默认["","_right"] |
| melt | id_vars标识列数组、value_vars展开列数组；var_name/value_name默认variable/value，不生成不存在的期间或类别 |
| pivot | index标识列数组、column分类标签列、value取值列；缺组合保留null；重复index+column先显式aggregate，不暗中求和 |
| shift | group_by、order_by:[{column,direction:asc/desc}]、measures:[{column,as}]、periods默认1；按明确顺序取各组前N条观测，首N条为null，组内排序键须能唯一确定顺序 |

每步可用emit声明唯一输出名，末步无emit时自动输出result；一次执行最多64步。返回命名tables及self_check，columns/rows可直接登记证据。错误给出step_index、error和available_columns，不返回部分表。

## 数据语义

nulls默认skip；sum全空为null，显式zero补零，error拒绝空值。count计非空，size计行数，nunique默认不计null。零分母返回null和warning；加减乘除不隐含百分比，乘100须显式表达。先算全体占比再limit，不能将TOPN合计当全集。可加性按实际口径判断。

截断证据须显式`params.input_scope="subset"`才可计算已有子集，输出继承局限；默认拒绝截断输入。

join只消费声明输入，不查库；键类型须一致，null键不匹配，基数不符合validate则反馈当前步骤。多对多须显式选many_to_many，展开仍受行数边界限制；通过关联检查不证明业务口径一致。

shift按观测次序移动，不补日历缺口，缺月时不能自动称环比。固定同期配对用显式键关联，不默认为前12行。宽长转换只在所需计算或图表确实需要时做。

## 组合示例

```json
{"steps":[{"op":"cast","column":"amount","type":"number"},{"op":"aggregate","group_by":["group"],"measures":[{"column":"amount","function":"sum","as":"total"}],"emit":"all_groups"},{"op":"sort","order_by":[{"column":"total","direction":"desc"}]},{"op":"limit","n":3,"emit":"top_groups"}]}
```
