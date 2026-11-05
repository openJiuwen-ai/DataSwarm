# 指标类型判定

传给 `dim_breakdown` 的 `metric_kind` 决定跑哪些自洽校验。**判错会导致持续误报或漏报。**

---

## 三类指标（可加性维度）

| 类型 | 判据 | 例子 | 各维度之和 = 总量？ |
|---|---|---|---|
| **additive**（默认） | 对行求和有意义 | 订单量、GMV、PV、金额、次数 | ✅ 成立 |
| **ratio** | 分子分母都是可加量 | 转化率、成功率、客单价、人均 | ❌ 不成立 |
| **distinct** | 含去重 | UV、DAU、去重用户数、活跃商家数 | ❌ 不成立 |

一句话判定：**「把各地区的值加起来，等于全国吗？」**

---

## 例：去重指标各维度之和大于总量

实测数据（77,941 行订单的演示库）：

```
全国 distinct user  = 11,975
各地区之和          = 41,313      比全国多 29,338
```

一个用户在广东和北京都下过单，在省维度被算两次、在全国只算一次。
因此对 UV、DAU 这类指标要传 `"metric_kind":"distinct"`，加和类断言会自动跳过。

---

## 各类型跑哪些校验

| 校验 | additive | ratio | distinct |
|---|---|---|---|
| 份额闭合（Σ\|gross_share_pct\| = 100%） | ✅ | ❌ 跳过 | ❌ 跳过 |
| 净变动闭合 | ✅ | ❌ 跳过 | ❌ 跳过 |
| 净占比闭合（`net_share_reliable` 为 true 时） | ✅ | ❌ 跳过 | ❌ 跳过 |
| 每个成员的变动可重算 | ✅ | ✅ | ✅ |
| 最大偏离不小于离散度 | ✅ | ✅ | ✅ |

---

## 率型指标的处理

率型指标按维度拆「率的变化」没有可加性，按下面两条路径之一处理。

**① 拆成两个可加指标分别拆解**

```sql
-- 在取数层分别取分子和分母
--（data-insight 不取数；此 SQL 由上游取数执行，落地成含 orders / visits 两列的 CSV）
SELECT ds, region, COUNT(*) orders, COUNT(DISTINCT session_id) visits
FROM events GROUP BY ds, region
```

然后对 `orders` 和 `visits` 各跑一次 `dim_breakdown`，比较两侧的分化情况。

**② 用公式拆解**

率型指标通常能写成乘法公式，这时用 `formula_breakdown`：

```
GMV = 访客数(visitors) × 转化率(conv_rate) × 客单价(aov)
```

```bash
python analyzer.py formula_breakdown \
  --params '{"baseline":{"visitors":86000,"conv_rate":0.031,"aov":158},
             "current":{"visitors":82000,"conv_rate":0.022,"aov":160},
             "expression":"visitors * conv_rate * aov"}'
```

它会给出降幅里有多少来自流量、多少来自转化、多少来自客单价，
这是按维度拆解给不出的。默认用精确 Shapley 分解，各因子 `effect` 之和等于净变动。

---

## 常见误判

| 指标 | 容易误判为 | 实际 | 说明 |
|---|---|---|---|
| 客单价 | additive | **ratio** | = GMV / 订单数 |
| 人均时长 | additive | **ratio** | = 总时长 / 用户数 |
| 活跃用户数 | additive | **distinct** | 跨维度会重复计数 |
| 订单金额 | ratio | **additive** | 每单一个金额，直接可加 |
| 商品数 | distinct | 看定义 | SKU 去重数是 distinct；销量是 additive |

拿不准时按 **distinct** 处理。

---

## 周期聚合方式（与可加性正交的另一个维度）

「本月 GMV」「本月 DAU」「本月 MAU」是三种不同的聚合——**拟 SQL 前先判聚合型**：

| 聚合类型 | 判据 | 例子 | 周期内取法 |
|---|---|---|---|
| **累加型** | 事件量，跨期可加 | GMV、订单量、PV、销售额 | 周期内求和（SUM） |
| **水平型** | 每日状态值，跨期不可加 | DAU、转化率、点击率 | 周期内均值（AVG） |
| **规模型** | 存量快照，取时点 | MAU、累计用户数、库存余额 | 周期末快照（末日值或周期整体去重） |

判定口诀：**「把每天的数加起来有意义吗？」**——GMV 加起来是月销售额（累加）；
DAU 加起来没有业务含义（水平，取日均）；MAU 本身就是月口径（规模，取快照）。

「本月 DAU」写成 SUM(dau) 会得出「日均 3 万的 App 本月活跃 90 万」这类数。
取数 SQL 的聚合函数必须与聚合型一致，拿不准时先看指标定义里的口径描述。
