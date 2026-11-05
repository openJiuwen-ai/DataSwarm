#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""rebuild_layer_registry.py —— 按 Doris olist 实际表结构重建 dw_layer_registry。

[废弃 v2/2026-08] 本脚本已废弃。血缘真相源已统一为 bigdata.table_lineage /
bigdata.column_lineage（由编排任务 publish/执行时解析 SQL 写入，见
unify-lineage-task-driven 计划 §3.5）。血缘追溯不再读 dw_layer_registry。
本脚本保留仅作兼容/历史参考，新部署无需再跑；后续将随 dw_layer_registry 表一并下线。

背景：dw_layer_registry 之前从 SQLite training_warehouse.db 灌入，表名（olist_orders/
dwd_fact_order_items/ads_kpi_dashboard）与 Doris olist 业务库实际表（olist_orders_dataset/
dwd_order_detail/ads_gmv_daily）对不上，导致血缘图 0 边。本脚本按 olist 真实
33 张表（SOURCE/ODS/DWD/DWS/ADS 分层）+ 7 张 dw_* 元数据表（META）重建。

做法：TRUNCATE dw_layer_registry → INSERT 40 行（幂等可重跑）。

表结构（Doris bigdata）：table_name / layer / layer_comment / upstream_tables
"""
import sys
from pathlib import Path

CONNECTOR = Path(__file__).resolve().parent.parent.parent / "dw-db-connector" / "scripts"
sys.path.insert(0, str(CONNECTOR))
import db_connector as dc  # noqa: E402

META_REF = "doris://meta"  # bigdata 元数据库

# 33 张业务表分层 + 上游 + 一句话注释（SOURCE 为无前缀 olist_*_dataset 原始镜像）
# 上游用真实消费关系：ODS←SOURCE；DWD←ODS；DWS←DWD；ADS←DWS
REGISTRY = [
    # ---- SOURCE：无前缀原始镜像（9 张，无上游） ----
    ("olist_customers_dataset", "SOURCE", "原始客户表（CSV 全量镜像）", ""),
    ("olist_geolocation_dataset", "SOURCE", "原始地理维表（CSV 全量镜像）", ""),
    ("olist_order_items_dataset", "SOURCE", "原始订单明细（CSV 全量镜像）", ""),
    ("olist_order_payments_dataset", "SOURCE", "原始支付明细（CSV 全量镜像）", ""),
    ("olist_order_reviews_dataset", "SOURCE", "原始评价明细（CSV 全量镜像）", ""),
    ("olist_orders_dataset", "SOURCE", "原始订单主表（CSV 全量镜像）", ""),
    ("olist_products_dataset", "SOURCE", "原始商品表（CSV 全量镜像）", ""),
    ("olist_sellers_dataset", "SOURCE", "原始卖家表（CSV 全量镜像）", ""),
    ("product_category_name_translation", "SOURCE", "原始类目翻译映射（CSV 全量镜像）", ""),

    # ---- ODS：清洗接入层（9 张，上游=对应 SOURCE） ----
    ("ods_olist_customers_dataset", "ODS", "客户接入层（加 __load_time，清洗）", "olist_customers_dataset"),
    ("ods_olist_geolocation_dataset", "ODS", "地理接入层（加 __load_time，清洗）", "olist_geolocation_dataset"),
    ("ods_olist_order_items_dataset", "ODS", "订单明细接入层（加 __load_time）", "olist_order_items_dataset"),
    ("ods_olist_order_payments_dataset", "ODS", "支付接入层（加 __load_time）", "olist_order_payments_dataset"),
    ("ods_olist_order_reviews_dataset", "ODS", "评价接入层（加 __load_time）", "olist_order_reviews_dataset"),
    ("ods_olist_orders_dataset", "ODS", "订单接入层（加 __load_time）", "olist_orders_dataset"),
    ("ods_olist_products_dataset", "ODS", "商品接入层（加 __load_time）", "olist_products_dataset"),
    ("ods_olist_sellers_dataset", "ODS", "卖家接入层（加 __load_time）", "olist_sellers_dataset"),
    ("ods_product_category_name_translation", "ODS", "类目翻译接入层（加 __load_time）", "product_category_name_translation"),

    # ---- DWD：明细/维层（5 张，上游=ODS） ----
    ("dwd_dim_customer", "DWD", "客户维表（清洗去重，唯一客户）", "ods_olist_customers_dataset"),
    ("dwd_fact_orders", "DWD", "订单事实表（时间戳重命名、清洗）", "ods_olist_orders_dataset"),
    ("dwd_order_detail", "DWD", "订单明细宽表（join 订单/明细/商品/卖家/客户/地理/类目）",
     "ods_olist_orders_dataset,ods_olist_order_items_dataset,ods_olist_products_dataset,"
     "ods_olist_sellers_dataset,ods_olist_customers_dataset,ods_olist_geolocation_dataset,"
     "ods_product_category_name_translation"),
    ("dwd_order_payment", "DWD", "支付事实表（标准化）", "ods_olist_order_payments_dataset,ods_olist_orders_dataset"),
    ("dwd_order_review", "DWD", "评价事实表（标准化）", "ods_olist_order_reviews_dataset,ods_olist_orders_dataset"),

    # ---- DWS：汇总层（5 张，上游=DWD） ----
    ("dws_trade_order_1d", "DWS", "日交易汇总（GMV/订单量/件数）", "dwd_order_detail,dwd_fact_orders"),
    ("dws_category_sale_1d", "DWS", "日类目销售汇总", "dwd_order_detail"),
    ("dws_payment_type_1d", "DWS", "日支付方式汇总", "dwd_order_payment"),
    ("dws_customer_repurchase_1d", "DWS", "日客户复购汇总", "dwd_order_detail"),
    ("dws_delivery_performance_1d", "DWS", "日履约时效汇总", "dwd_order_detail"),

    # ---- ADS：应用层（5 张，上游=DWS） ----
    ("ads_gmv_daily", "ADS", "GMV 日报看板", "dws_trade_order_1d"),
    ("ads_order_count_daily", "ADS", "订单量日报看板", "dws_trade_order_1d"),
    ("ads_category_sales_distribution", "ADS", "类目销售分布看板", "dws_category_sale_1d"),
    ("ads_customer_repurchase", "ADS", "客户复购指标看板", "dws_customer_repurchase_1d"),
    ("ads_delivery_performance", "ADS", "履约时效指标看板", "dws_delivery_performance_1d"),

    # ---- META：系统元数据表（7 张，自身分层，无业务上游） ----
    ("dw_tasks", "META", "任务定义表（ETL 配置与调度依赖）", ""),
    ("dw_task_runs", "META", "任务运行历史表（执行状态/SLA）", ""),
    ("dw_task_instances", "META", "任务/工作流实例表（触发与状态）", ""),
    ("dw_workflows", "META", "工作流定义表", ""),
    ("dw_workflow_nodes", "META", "工作流节点表（DAG 编排）", ""),
    ("dw_layer_registry", "META", "数仓分层注册表（本表）", ""),
    ("dw_column_lineage", "META", "列级血缘元数据表", ""),
]


def main():
    if dc.tables(META_REF).get("tables") is None:
        print("[ERROR] 无法连接 bigdata 元数据库", file=sys.stderr)
        return 1
    print(f"目标: Doris bigdata.dw_layer_registry（重建 {len(REGISTRY)} 行）")
    print("=" * 70)
    # TRUNCATE 清空旧数据（幂等重跑）
    dc.execute(META_REF, "TRUNCATE TABLE `dw_layer_registry`")
    print("[OK] TRUNCATE dw_layer_registry")
    # 逐行 INSERT（%s 占位符）
    inserted = 0
    for table_name, layer, comment, upstream in REGISTRY:
        dc.execute(
            META_REF,
            "INSERT INTO `dw_layer_registry` "
            "(table_name, layer, layer_comment, upstream_tables) VALUES (%s,%s,%s,%s)",
            (table_name, layer, comment, upstream),
        )
        inserted += 1
    print(f"[OK] INSERT {inserted} 行")
    print("=" * 70)
    # 汇总
    rows = dc.query_dict(META_REF, "SELECT layer, COUNT(*) AS c FROM dw_layer_registry GROUP BY layer ORDER BY layer")
    for r in rows:
        print(f"  {r['layer']:<6} {r['c']} 张")
    print("\n重建完成。重跑 lineage 即可看到完整血缘链 SOURCE→ODS→DWD→DWS→ADS。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
