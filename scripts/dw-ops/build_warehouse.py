#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
build_warehouse.py —— 把扁平 Olist 业务源按数仓规范分层建模成物化表。

为什么要有这个脚本：
    原始 Olist 是 9 张扁平 olist_* 业务表，没有数仓分层（ODS/DWD/DWS/ADS）。
    拿扁平业务源硬演"数仓治理"缺乏真实感——数仓治理的核心就是分层治理：
    ODS 接源→DWD 明细→DWS 汇总→ADS 应用，每层有不同质检规则、SLA、血缘落点。

    本脚本从干净源 olist.db.orig 拷贝副本，在副本上用 CREATE TABLE AS SELECT
    物化出四层结构（每层真实存储，非视图）。物化表的好处：
      1. 每层独立存储，能对每层单独注入故障/巡检/漂移
      2. 层间引用是真实血缘（ODS→DWD→DWS→ADS），非视图重算
      3. 每层物化由一个任务产出（任务治理真实，DAG=分层依赖链）

    绝不碰原始 olist.db.orig，一律在副本上操作。

分层结构（中等规模，18 张业务表）：
    ODS  原始层（9）：源库中的全部 olist_* 表及类目翻译表（源表原样）
    DWD  明细层（7）：事实/维度物化表，完整覆盖下游消费的 ODS
    DWS  汇总层（3）：dws_daily_gmv, dws_daily_orders, dws_seller_performance
    ADS  应用层（1）：ads_kpi_dashboard

用法:
    python build_warehouse.py --src olist.db.orig --out training_warehouse_baseline.db
    python build_warehouse.py --src olist.db.orig --out training_warehouse_baseline.db --drop-existing

退出码: 0 成功; 1 参数错误; 2 DB 错误
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def copy_db(src: str, out: str) -> Path:
    """从干净源拷副本，清掉陈旧 shm/wal 侧车。"""
    sp = Path(src).expanduser().resolve()
    op = Path(out).expanduser().resolve()
    if not sp.exists():
        raise FileNotFoundError(f"源 db 不存在: {sp}")
    if not sp.is_file():
        raise ValueError(f"源路径不是文件: {sp}")
    shutil.copy2(sp, op)
    for ext in ("-shm", "-wal"):
        side = Path(str(sp) + ext)
        if side.exists():
            shutil.copy2(side, Path(str(op) + ext))
    return op


# =========================================================================
# DWD 明细层：清洗 + 标准化 + 维度去重。
# 重要：DWS/ADS 只能依赖这些物化表，不能绕回 ODS。
# =========================================================================
DWD_SQL = [
    ("dwd_dim_product",
     """
     CREATE TABLE dwd_dim_product AS
     SELECT
         p.product_id,
         p.product_category_name           AS category_pt,
         t.product_category_name_english   AS category_en,
         p.product_name_lenght             AS name_length,
         p.product_description_lenght      AS desc_length,
         p.product_photos_qty              AS photos_qty,
         p.product_weight_g                AS weight_g,
         p.product_length_cm               AS length_cm,
         p.product_height_cm               AS height_cm,
         p.product_width_cm                AS width_cm
     FROM olist_products p
     LEFT JOIN product_category_name_translation t
         ON p.product_category_name = t.product_category_name;
     """),
    ("dwd_dim_customer",
     """
     CREATE TABLE dwd_dim_customer AS
     SELECT
         customer_id,
         customer_unique_id,
         customer_zip_code_prefix,
         customer_city,
         customer_state
     FROM olist_customers;
     """),
    ("dwd_dim_seller",
     """
     CREATE TABLE dwd_dim_seller AS
     SELECT
         seller_id,
         seller_zip_code_prefix,
         seller_city,
         seller_state
     FROM olist_sellers;
     """),
    ("dwd_fact_orders",
     """
     CREATE TABLE dwd_fact_orders AS
     SELECT
         order_id,
         customer_id,
         order_status,
         -- 时间标准化：源是 TEXT ISO，保留原值便于时序分桶
         order_purchase_timestamp      AS purchase_ts,
         order_approved_at              AS approved_ts,
         order_delivered_carrier_date   AS delivered_carrier_ts,
         order_delivered_customer_date  AS delivered_customer_ts,
         order_estimated_delivery_date  AS estimated_delivery_ts
     FROM olist_orders;
     """),
    ("dwd_fact_order_items",
     """
     CREATE TABLE dwd_fact_order_items AS
     SELECT
         oi.order_id,
         oi.order_item_id,
         oi.product_id,
         oi.seller_id,
         oi.shipping_limit_date,
         oi.price,
         oi.freight_value,
         -- 商品口径 GMV：price + freight（订单项贡献的 GMV）
         (oi.price + oi.freight_value)  AS gmv_product_caliber
     FROM olist_order_items oi;
     """),
    ("dwd_fact_payments",
     """
     CREATE TABLE dwd_fact_payments AS
     SELECT
         order_id,
         payment_sequential,
         payment_type,
         payment_installments,
         payment_value
     FROM olist_order_payments;
     """),
    ("dwd_fact_reviews",
     """
     CREATE TABLE dwd_fact_reviews AS
     SELECT
         review_id,
         order_id,
         review_score,
         review_comment_title,
         review_comment_message,
         review_creation_date,
         review_answer_timestamp
     FROM olist_order_reviews;
     """),
]

# =========================================================================
# DWS 汇总层：日级聚合 + 主题汇总
# =========================================================================
DWS_SQL = [
    ("dws_daily_gmv",
     """
     CREATE TABLE dws_daily_gmv AS
     WITH
     -- 商品口径：按下单日汇总 order_items 的 price+freight
     prod AS (
         SELECT date(o.purchase_ts) AS order_date,
                SUM(oi.price + oi.freight_value) AS gmv_product
         FROM dwd_fact_orders o
         JOIN dwd_fact_order_items oi ON o.order_id = oi.order_id
         GROUP BY date(o.purchase_ts)
     ),
     -- 支付口径：按下单日汇总 payments.payment_value
     pay AS (
         SELECT date(o.purchase_ts) AS order_date,
                SUM(p.payment_value) AS gmv_payment
         FROM dwd_fact_orders o
         JOIN dwd_fact_payments p ON o.order_id = p.order_id
         GROUP BY date(o.purchase_ts)
     )
     SELECT
         COALESCE(prod.order_date, pay.order_date) AS order_date,
         prod.gmv_product,
         pay.gmv_payment,
         -- 双口径并存：指标治理在此实算对比 rel_diff
         CASE WHEN prod.gmv_product IS NOT NULL AND pay.gmv_payment IS NOT NULL
              THEN ABS(prod.gmv_product - pay.gmv_payment)
                   / MAX(ABS(prod.gmv_product), ABS(pay.gmv_payment), 1.0)
              ELSE NULL END AS caliber_rel_diff
     FROM prod FULL OUTER JOIN pay USING (order_date)
     ORDER BY order_date;
     """),
    ("dws_daily_orders",
     """
     CREATE TABLE dws_daily_orders AS
     SELECT
         date(purchase_ts) AS order_date,
         order_status,
         COUNT(*)                        AS order_cnt
     FROM dwd_fact_orders
     GROUP BY date(purchase_ts), order_status
     ORDER BY order_date, order_status;
     """),
    ("dws_seller_performance",
     """
     CREATE TABLE dws_seller_performance AS
     SELECT
         oi.seller_id,
         COUNT(DISTINCT oi.order_id)                          AS order_cnt,
         COUNT(*)                                            AS item_cnt,
         SUM(oi.price)                                       AS revenue,
         SUM(oi.freight_value)                               AS freight_total,
         ROUND(AVG(oi.price), 2)                              AS avg_item_price
     FROM dwd_fact_order_items oi
     GROUP BY oi.seller_id;
     """),
]

# =========================================================================
# ADS 应用层：核心指标看板
# =========================================================================
ADS_SQL = [
    ("ads_kpi_dashboard",
     """
     CREATE TABLE ads_kpi_dashboard AS
     SELECT
         'overall' AS scope,
         (SELECT COUNT(DISTINCT order_id) FROM dwd_fact_orders)                    AS total_orders,
         (SELECT COUNT(*) FROM dwd_fact_order_items)                               AS total_items,
         (SELECT SUM(gmv_product) FROM dws_daily_gmv)                              AS gmv_product_caliber,
         (SELECT SUM(gmv_payment) FROM dws_daily_gmv)                              AS gmv_payment_caliber,
         (SELECT COUNT(DISTINCT customer_unique_id) FROM dwd_dim_customer)         AS total_customers,
         (SELECT COUNT(DISTINCT seller_id) FROM dwd_dim_seller)                    AS total_sellers,
         (SELECT ROUND(AVG(review_score), 2) FROM dwd_fact_reviews)                AS avg_review_score,
         -- 客单价：GMV(商品口径) / 订单数
         ROUND((SELECT SUM(gmv_product) FROM dws_daily_gmv)
               / NULLIF((SELECT COUNT(DISTINCT order_id) FROM dwd_fact_orders), 0), 2)
                                                                                     AS avg_order_value,
         -- GMV 口径差异率（指标治理：支付 vs 商品）
         CASE WHEN (SELECT SUM(gmv_product) FROM dws_daily_gmv) > 0
              THEN ROUND(ABS((SELECT SUM(gmv_payment) FROM dws_daily_gmv)
                            - (SELECT SUM(gmv_product) FROM dws_daily_gmv))
                        / (SELECT SUM(gmv_product) FROM dws_daily_gmv), 4)
              ELSE NULL END AS gmv_caliber_diff_ratio;
     """),
]

# =========================================================================
# 元数据：表分层注册（供巡检/任务治理识别层归属）
# 注意：每条 SQL 必须是单语句，conn.execute 不支持分号分隔多语句。
# =========================================================================
META_SQL = [
    ("dw_layer_registry",
     """
     CREATE TABLE dw_layer_registry (
         table_name      TEXT PRIMARY KEY,
         layer           TEXT NOT NULL,        -- ODS / DWD / DWS / ADS
         layer_comment   TEXT,
         upstream_tables TEXT                  -- 逗号分隔，供血缘/任务治理
     )
     """),
    ("dw_layer_registry",
     """
    INSERT INTO dw_layer_registry VALUES
         ('olist_orders','ODS','原始订单主表',''),
         ('olist_order_items','ODS','原始订单明细',''),
         ('olist_order_payments','ODS','原始支付明细',''),
         ('olist_products','ODS','原始商品维',''),
         ('olist_customers','ODS','原始客户维',''),
         ('olist_geolocation','ODS','原始地理维',''),
         ('olist_order_reviews','ODS','原始评价明细',''),
         ('olist_sellers','ODS','原始卖家维',''),
         ('product_category_name_translation','ODS','类目翻译映射',''),
         ('dwd_fact_orders','DWD','订单事实(清洗)','olist_orders'),
         ('dwd_fact_order_items','DWD','明细事实(加GMV字段)','olist_order_items,dwd_fact_orders'),
         ('dwd_fact_payments','DWD','支付事实(标准化)','olist_order_payments'),
         ('dwd_fact_reviews','DWD','评价事实(标准化)','olist_order_reviews'),
         ('dwd_dim_product','DWD','商品维(补英文类目)','olist_products,product_category_name_translation'),
         ('dwd_dim_customer','DWD','客户维','olist_customers'),
         ('dwd_dim_seller','DWD','卖家维','olist_sellers'),
         ('dws_daily_gmv','DWS','日GMV(双口径)','dwd_fact_orders,dwd_fact_order_items,dwd_fact_payments'),
         ('dws_daily_orders','DWS','日订单状态分布','dwd_fact_orders'),
         ('dws_seller_performance','DWS','卖家业绩汇总','dwd_fact_order_items'),
         ('ads_kpi_dashboard','ADS','核心KPI看板',
          'dws_daily_gmv,dwd_fact_orders,dwd_fact_order_items,dwd_dim_customer,dwd_dim_seller,dwd_fact_reviews')
     """),
    ("dw_column_lineage",
     """
     CREATE TABLE dw_column_lineage (
         source_table TEXT NOT NULL, source_column TEXT NOT NULL,
         target_table TEXT NOT NULL, target_column TEXT NOT NULL,
         transformation TEXT NOT NULL,
         PRIMARY KEY (source_table, source_column, target_table, target_column)
     )
     """),
    ("dw_column_lineage",
     """
     INSERT INTO dw_column_lineage VALUES
         ('olist_order_items','price','dwd_fact_order_items','gmv_product_caliber','price + freight_value'),
         ('olist_order_items','freight_value','dwd_fact_order_items','gmv_product_caliber','price + freight_value'),
         ('dwd_fact_order_items','gmv_product_caliber','dws_daily_gmv','gmv_product','SUM BY order_date'),
         ('dwd_fact_payments','payment_value','dws_daily_gmv','gmv_payment','SUM BY order_date'),
         ('dws_daily_gmv','gmv_product','ads_kpi_dashboard','gmv_product_caliber','SUM(gmv_product)'),
         ('dws_daily_gmv','gmv_payment','ads_kpi_dashboard','gmv_payment_caliber','SUM(gmv_payment)')
     """),
]


def build(src: str, out: str, drop_existing: bool = False) -> Path:
    op = copy_db(src, out)
    conn = sqlite3.connect(str(op), timeout=30)
    try:
        # 每层独立 BEGIN/commit：一错只回滚当前层，避免前面已建的表被连带撤掉
        def _run_layer(layer_label: str, layer_sql):
            """每层一个事务：每条 (表名, SQL) 先 DROP IF EXISTS 再 CREATE。
            适用于 DWD/DWS/ADS——每条 SQL 都是 CREATE TABLE AS SELECT，表名=SQL 目标表。
            META 层有建表+插种子两条针对同表，用 _run_meta 单独处理。"""
            conn.execute("BEGIN")
            try:
                for name, sql in layer_sql:
                    conn.execute(f"DROP TABLE IF EXISTS {name}")
                    conn.execute(sql)
                    cnt = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                    print(f"  [+] {layer_label} {name:30s} {cnt} rows")
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise

        def _run_meta(layer_label: str, layer_sql):
            """META 层：CREATE 语句建表，INSERT 语句写种子。"""
            conn.execute("BEGIN")
            try:
                for name, sql in layer_sql:
                    if "CREATE TABLE" in sql.upper():
                        conn.execute(f"DROP TABLE IF EXISTS {name}")
                    conn.execute(sql)
                    cnt = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                    print(f"  [+] {layer_label} {name:30s} {cnt} rows")
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise

        _run_layer("DWD", DWD_SQL)
        _run_layer("DWS", DWS_SQL)
        _run_layer("ADS", ADS_SQL)
        _run_meta("META", META_SQL)
    except sqlite3.Error as e:
        conn.close()
        raise
    conn.close()
    return op


def main():
    ap = argparse.ArgumentParser(description="Olist 扁平源 → 数仓四层物化建模")
    ap.add_argument("--src", required=True, help="干净源 db 路径（如 olist.db.orig）")
    ap.add_argument("--out", required=True, help="输出分层副本路径")
    ap.add_argument("--drop-existing", action="store_true",
                    help="若输出已含分层表则先删（覆盖重建）")
    args = ap.parse_args()

    try:
        op = build(args.src, args.out, drop_existing=args.drop_existing)
        print(f"\n分层仓库已生成: {op}")
        print("分层结构: ODS(9) + DWD(7) + DWS(3) + ADS(1) + META(2) = 22 张物理表（含原始源表）")
        print("下一步可注入异常:")
        print(f"  python seed_anomalies.py --src {args.out} --out training_warehouse.db")
        print("或直接巡检(分层库本身是干净基线，可先建 baseline):")
        print(f"  python run_pipeline.py --db {op}")
    except sqlite3.Error as e:
        print(f"[ERROR] DB: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
