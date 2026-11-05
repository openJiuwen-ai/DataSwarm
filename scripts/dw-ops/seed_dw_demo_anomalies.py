# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""构造数仓演示异常数据——**真跑**实例，不手写运行记录。

2 个复杂工作流（各含 3 个真实 ETL 任务），模拟真实 Spark/Flink：
  - GMV离线批处理链路：订单宽表→GMV聚合→KPI看板（含人工审核节点）
  - 卖家绩效周报链路：绩效计算→GMV校验→周报生成（上游失败传导）

真实跑出来的异常（不手写）：
  - etl_daily_gmv 查不存在的表 → 真失败（task_health）
  - etl_kpi_dashboard 建了不跑 → 无运行记录（task_sla）
  - 工作流上游失败 → 下游 skipped（task_dag + task_rca）
  - etl_order_wide sla 设小 → 跑成功但超 SLA（task_sla 违规）
"""
import sys
import sqlite3
sys.path.insert(0, '.')
from jiuwenswarm.channels.web.orchestration import db as orch_db, workflow_engine

# 清旧
conn = sqlite3.connect(str(orch_db.WAREHOUSE_DB))
for t in ["dw_tasks", "dw_task_instances", "dw_task_runs", "dw_workflows", "dw_workflow_nodes"]:
    conn.execute(f"DELETE FROM {t}")
conn.commit()
conn.close()

print("=== 建任务 ===")

# 任务1：订单宽表构建（DWD 多表 JOIN，模拟 Spark 离线批）
# sla 设 0 分钟 → 真跑成功但 end_ts 必然 > sla_time（SLA 违规）
t1 = orch_db.create_task({
    "task_id": "etl_order_wide", "name": "订单宽表构建",
    "description": "模拟 Spark 离线批：JOIN orders+items+payments+customer，产出宽表",
    "executor_type": "sql",
    "code": (
        "SELECT o.order_id, o.customer_id, o.order_status, oi.product_id, oi.seller_id, "
        "oi.price, oi.freight_value, p.payment_value, c.customer_state "
        "FROM dwd_fact_orders o "
        "JOIN dwd_fact_order_items oi ON o.order_id=oi.order_id "
        "JOIN dwd_fact_payments p ON o.order_id=p.order_id "
        "JOIN dwd_dim_customer c ON o.customer_id=c.customer_id "
        "LIMIT 100"
    ),
    "sla_minutes": 0, "timeout_s": 7200, "layer": "DWD",
    "output_table": "dwd_fact_order_items", "upstream_tasks": "",
})
print(f"  建任务: {t1['task_id']} (订单宽表构建, sla=0 故意违规)")

# 任务2：KPI仪表盘聚合（DWD→ADS，模拟 Spark 全量指标）
t2 = orch_db.create_task({
    "task_id": "etl_kpi_dashboard", "name": "KPI仪表盘聚合",
    "description": "模拟 Spark 全量聚合：GMV/订单数/客单价，产出 ADS 看板",
    "executor_type": "sql",
    "code": (
        "SELECT 'global' AS scope, COUNT(DISTINCT o.order_id) AS total_orders, "
        "COUNT(*) AS total_items, SUM(oi.price) AS gmv_product_caliber, "
        "SUM(p.payment_value) AS gmv_payment_caliber, "
        "COUNT(DISTINCT c.customer_id) AS total_customers, "
        "ROUND(SUM(oi.price)*1.0/COUNT(DISTINCT o.order_id),2) AS avg_order_value "
        "FROM dwd_fact_orders o "
        "JOIN dwd_fact_order_items oi ON o.order_id=oi.order_id "
        "JOIN dwd_fact_payments p ON o.order_id=p.order_id "
        "JOIN dwd_dim_customer c ON o.customer_id=c.customer_id"
    ),
    "sla_minutes": 45, "timeout_s": 7200, "layer": "ADS",
    "output_table": "ads_kpi_dashboard", "upstream_tasks": "etl_order_wide",
})
print(f"  建任务: {t2['task_id']} (KPI仪表盘聚合, 不跑→无运行记录)")

# 任务3：GMV日聚合（会失败——查不存在的表 v2）
t3 = orch_db.create_task({
    "task_id": "etl_daily_gmv", "name": "GMV日聚合",
    "description": "模拟 Spark 日批聚合 GMV，故意查不存在的表演示失败传导",
    "executor_type": "sql",
    "code": (
        "SELECT date(o.purchase_ts) AS order_date, SUM(oi.price) AS gmv_product "
        "FROM dwd_fact_orders_v2 o "
        "JOIN dwd_fact_order_items oi ON o.order_id=oi.order_id "
        "GROUP BY date(o.purchase_ts)"
    ),
    "sla_minutes": 20, "timeout_s": 7200, "layer": "DWS",
    "output_table": "dws_daily_gmv", "upstream_tasks": "etl_order_wide",
})
print(f"  建任务: {t3['task_id']} (GMV日聚合, 查不存在的表→真失败)")

# 任务4：卖家绩效周报（DWD→DWS，模拟 Flink 流式聚合）
t4 = orch_db.create_task({
    "task_id": "etl_seller_perf", "name": "卖家绩效周报",
    "description": "模拟 Flink 流式聚合：seller 订单数/收入/运费/客单价",
    "executor_type": "sql",
    "code": (
        "SELECT s.seller_id, s.seller_state, COUNT(DISTINCT oi.order_id) AS order_cnt, "
        "SUM(oi.price) AS revenue, SUM(oi.freight_value) AS freight_total, "
        "ROUND(AVG(oi.price),2) AS avg_item_price "
        "FROM dwd_dim_seller s "
        "JOIN dwd_fact_order_items oi ON s.seller_id=oi.seller_id "
        "GROUP BY s.seller_id, s.seller_state"
    ),
    "sla_minutes": 15, "timeout_s": 7200, "layer": "DWS",
    "output_table": "dws_seller_performance", "upstream_tasks": "etl_order_wide",
})
print(f"  建任务: {t4['task_id']} (卖家绩效周报)")

print("\n=== 真跑任务实例 ===")

# 真跑 etl_order_wide（会成功，但因 sla=0 违规）
inst = orch_db.create_instance("task", "etl_order_wide", "schedule", "queued")
r = workflow_engine.run_task_instance(inst["instance_id"], "etl_order_wide")
print(f"  etl_order_wide: status={r['status']} (sla=0 → 应违规)")

# 真跑 etl_daily_gmv（会失败）
inst2 = orch_db.create_instance("task", "etl_daily_gmv", "schedule", "queued")
r2 = workflow_engine.run_task_instance(inst2["instance_id"], "etl_daily_gmv")
print(f"  etl_daily_gmv: status={r2['status']} (查不存在的表 → 真失败)")

# 真跑 etl_seller_perf（会成功，健康对比）
inst3 = orch_db.create_instance("task", "etl_seller_perf", "schedule", "queued")
r3 = workflow_engine.run_task_instance(inst3["instance_id"], "etl_seller_perf")
print(f"  etl_seller_perf: status={r3['status']} (健康)")

# etl_kpi_dashboard 不跑 → 无运行记录

print("\n=== 建工作流 + 真跑 ===")

# 工作流1：GMV离线批处理链路（带审核节点）
wf1 = orch_db.create_workflow({
    "workflow_id": "wf_gmv_pipeline", "name": "GMV离线批处理链路",
    "description": "模拟 Spark 离线批：宽表构建→人工审核→GMV聚合→看板产出"
})
orch_db.replace_nodes("wf_gmv_pipeline", [
    {"node_id": "g1", "task_id": "etl_order_wide", "node_name": "订单宽表构建",
     "group_name": "DWD处理", "order_index": 0, "upstream_nodes": ""},
    {"node_id": "g2", "task_id": "etl_daily_gmv", "node_name": "GMV日聚合",
     "group_name": "DWS聚合", "order_index": 1, "upstream_nodes": "g1", "wait_review": True},
    {"node_id": "g3", "task_id": "etl_kpi_dashboard", "node_name": "KPI看板产出",
     "group_name": "ADS产出", "order_index": 2, "upstream_nodes": "g2"},
])
# 真跑工作流：node1(订单宽表) success → node2(GMV日聚合, wait_review) 卡审核
winst1 = orch_db.create_instance("workflow", "wf_gmv_pipeline", "schedule", "queued")
wr1 = workflow_engine.run_workflow_instance(winst1["instance_id"], "wf_gmv_pipeline")
print(f"  GMV离线批处理链路: status={wr1['status']} (node2 wait_review → 应卡审核)")
for tr in orch_db.list_task_runs(winst1["instance_id"]):
    print(f"    node={tr.get('node_name')} status={tr['status']}")

# 工作流2：卖家绩效周报链路（失败传导）
wf2 = orch_db.create_workflow({
    "workflow_id": "wf_seller_perf", "name": "卖家绩效周报链路",
    "description": "模拟 Flink 流批：绩效计算→GMV校验→周报生成，上游失败传导"
})
orch_db.replace_nodes("wf_seller_perf", [
    {"node_id": "s1", "task_id": "etl_seller_perf", "node_name": "绩效计算",
     "group_name": "DWS处理", "order_index": 0, "upstream_nodes": ""},
    {"node_id": "s2", "task_id": "etl_daily_gmv", "node_name": "GMV校验",
     "group_name": "DWS校验", "order_index": 1, "upstream_nodes": "s1"},
    {"node_id": "s3", "task_id": "etl_kpi_dashboard", "node_name": "周报生成",
     "group_name": "ADS产出", "order_index": 2, "upstream_nodes": "s2"},
])
# 真跑工作流：node1(绩效计算) success → node2(GMV校验=etl_daily_gmv, 会失败) → node3 skipped
winst2 = orch_db.create_instance("workflow", "wf_seller_perf", "schedule", "queued")
wr2 = workflow_engine.run_workflow_instance(winst2["instance_id"], "wf_seller_perf")
print(f"  卖家绩效周报链路: status={wr2['status']} (node2 失败 → node3 应 skipped)")
for tr in orch_db.list_task_runs(winst2["instance_id"]):
    print(f"    node={tr.get('node_name')} status={tr['status']}")

print(f"\n=== 构造完成（真跑）===")
print(f"任务: {len(orch_db.list_tasks())} 个")
print(f"工作流: {len(orch_db.list_workflows())} 个")
print(f"实例: {len(orch_db.list_instances())} 个")
total_runs = len(orch_db.list_task_runs(winst1['instance_id'])) + len(orch_db.list_task_runs(winst2['instance_id']))
print(f"任务运行: 总 {total_runs} 条（真实产生）")
