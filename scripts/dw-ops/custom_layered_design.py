# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Custom layered table design for Olist e-commerce data warehouse.

Reads ODS schemas from plan_table, designs DWD/DWS/ADS layers
targeting 5 core metrics: GMV, order count, repurchase rate,
delivery timeliness, category sales distribution.
"""

import json
import re
import sys
import uuid
from pathlib import Path

# 项目根（DataSwarm）——从 skill scripts 目录向上 7 级
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from jiuwenswarm.channels.web.dataagent.meta_data import get_store  # noqa: E402

_store = get_store()

# 从命令行参数获取，不再硬编码
PLAN_ID = ""
BIZ_DB = ""


def read_ods_from_plan(plan_id: str) -> dict:
    """Read ODS table DDLs from plan_table, return {short_name: ddl_sql}."""
    rows = _store.list_plan_tables(plan_id, layer="ODS")

    result = {}
    for r in rows:
        short = r["table_name"].split(".")[-1] if "." in r["table_name"] else r["table_name"]
        result[short] = r["ddl_sql"] or ""
    return result


def parse_ddl_columns(ddl: str) -> list[dict]:
    """Extract columns from DDL text."""
    cols = []
    in_cols = False
    for line in ddl.split("\n"):
        line = line.strip()
        if line.startswith("("):
            in_cols = True
            continue
        if line.startswith(")") or line.startswith("UNIQUE") or line.startswith("DUPLICATE"):
            in_cols = False
            continue
        if in_cols and line.startswith("`"):
            m = re.match(r"`(\w+)`\s+(\S+)", line.rstrip(","))
            if m:
                name = m.group(1)
                dtype = m.group(2)
                if name == "__load_time":
                    continue
                cols.append({"name": name, "type": dtype, "nullable": True})
    return cols


def record_to_plan_table(plan_id: str, tables: list[dict]):
    """Insert designed tables into plan_table (upsert via store)."""
    rows = []
    for t in tables:
        rows.append({
            "plan_id": plan_id,
            "table_name": t["table_name"],
            "layer": t["layer"],
            "ddl_sql": t.get("ddl_sql"),
            "etl_sql": t.get("etl_sql"),
            "source_tables": t.get("source_tables"),
            "status": "designed",
        })
    _store.save_plan_tables(plan_id, rows)


# ──────────────────────────────────────────────
# DWD Layer Design
# ──────────────────────────────────────────────

def design_dwd_order_detail(ods_schemas: dict) -> dict:
    """DWD: 订单明细宽表 — 核心事实表，JOIN 订单+客户+商品+卖家+品类翻译"""
    dwd_name = "dwd_order_detail"
    cols = [
        ("order_id", "VARCHAR(64)"),
        ("customer_id", "VARCHAR(64)"),
        ("customer_unique_id", "VARCHAR(64)"),
        ("customer_city", "VARCHAR(100)"),
        ("customer_state", "VARCHAR(10)"),
        ("order_status", "VARCHAR(30)"),
        ("order_purchase_timestamp", "DATETIME"),
        ("order_approved_at", "DATETIME"),
        ("order_delivered_carrier_date", "DATETIME"),
        ("order_delivered_customer_date", "DATETIME"),
        ("order_estimated_delivery_date", "DATETIME"),
        ("order_item_id", "INT"),
        ("product_id", "VARCHAR(64)"),
        ("seller_id", "VARCHAR(64)"),
        ("price", "DOUBLE"),
        ("freight_value", "DOUBLE"),
        ("product_category_name", "VARCHAR(100)"),
        ("product_category_name_english", "VARCHAR(100)"),
        ("product_weight_g", "DOUBLE"),
        ("product_length_cm", "DOUBLE"),
        ("product_height_cm", "DOUBLE"),
        ("product_width_cm", "DOUBLE"),
        ("seller_city", "VARCHAR(100)"),
        ("seller_state", "VARCHAR(10)"),
        ("dt", "DATE"),
    ]
    col_defs = [f"  `{c}` {t}" for c, t in cols]
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{dwd_name}` (\n"
        + ",\n".join(col_defs)
        + "\n) DUPLICATE KEY(`order_id`)\n"
        f"DISTRIBUTED BY HASH(`order_id`) BUCKETS 8\n"
        f"PROPERTIES ('replication_num' = '1')"
    )

    etl = f"""INSERT INTO `{dwd_name}` (
  `order_id`, `customer_id`, `customer_unique_id`, `customer_city`, `customer_state`,
  `order_status`, `order_purchase_timestamp`, `order_approved_at`,
  `order_delivered_carrier_date`, `order_delivered_customer_date`,
  `order_estimated_delivery_date`, `order_item_id`, `product_id`, `seller_id`,
  `price`, `freight_value`, `product_category_name`, `product_category_name_english`,
  `product_weight_g`, `product_length_cm`, `product_height_cm`, `product_width_cm`,
  `seller_city`, `seller_state`, `dt`
)
SELECT
  o.`order_id`,
  o.`customer_id`,
  c.`customer_unique_id`,
  c.`customer_city`,
  c.`customer_state`,
  o.`order_status`,
  o.`order_purchase_timestamp`,
  o.`order_approved_at`,
  o.`order_delivered_carrier_date`,
  o.`order_delivered_customer_date`,
  o.`order_estimated_delivery_date`,
  oi.`order_item_id`,
  oi.`product_id`,
  oi.`seller_id`,
  oi.`price`,
  oi.`freight_value`,
  p.`product_category_name`,
  COALESCE(ct.`product_category_name_english`, p.`product_category_name`) AS `product_category_name_english`,
  p.`product_weight_g`,
  p.`product_length_cm`,
  p.`product_height_cm`,
  p.`product_width_cm`,
  s.`seller_city`,
  s.`seller_state`,
  DATE(o.`order_purchase_timestamp`) AS `dt`
FROM `ods_olist_orders_dataset` o
LEFT JOIN `ods_olist_customers_dataset` c ON o.`customer_id` = c.`customer_id`
LEFT JOIN `ods_olist_order_items_dataset` oi ON o.`order_id` = oi.`order_id`
LEFT JOIN `ods_olist_products_dataset` p ON oi.`product_id` = p.`product_id`
LEFT JOIN `ods_product_category_name_translation` ct ON p.`product_category_name` = ct.`product_category_name`
LEFT JOIN `ods_olist_sellers_dataset` s ON oi.`seller_id` = s.`seller_id`
WHERE o.`order_status` IN ('delivered', 'shipped', 'approved', 'processing', 'invoiced')"""

    return {
        "table_name": f"{BIZ_DB}.{dwd_name}",
        "table_name_short": dwd_name,
        "layer": "DWD",
        "ddl_sql": ddl,
        "etl_sql": etl,
        "source_tables": json.dumps([
            f"{BIZ_DB}.ods_olist_orders_dataset",
            f"{BIZ_DB}.ods_olist_customers_dataset",
            f"{BIZ_DB}.ods_olist_order_items_dataset",
            f"{BIZ_DB}.ods_olist_products_dataset",
            f"{BIZ_DB}.ods_product_category_name_translation",
            f"{BIZ_DB}.ods_olist_sellers_dataset",
        ]),
    }


def design_dwd_order_payment(ods_schemas: dict) -> dict:
    """DWD: 订单支付明细宽表"""
    dwd_name = "dwd_order_payment"
    cols = [
        ("order_id", "VARCHAR(64)"),
        ("payment_sequential", "INT"),
        ("payment_type", "VARCHAR(30)"),
        ("payment_installments", "INT"),
        ("payment_value", "DOUBLE"),
        ("order_purchase_timestamp", "DATETIME"),
        ("dt", "DATE"),
    ]
    col_defs = [f"  `{c}` {t}" for c, t in cols]
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{dwd_name}` (\n"
        + ",\n".join(col_defs)
        + "\n) DUPLICATE KEY(`order_id`)\n"
        f"DISTRIBUTED BY HASH(`order_id`) BUCKETS 4\n"
        f"PROPERTIES ('replication_num' = '1')"
    )

    etl = f"""INSERT INTO `{dwd_name}` (
  `order_id`, `payment_sequential`, `payment_type`, `payment_installments`,
  `payment_value`, `order_purchase_timestamp`, `dt`
)
SELECT
  op.`order_id`,
  op.`payment_sequential`,
  op.`payment_type`,
  op.`payment_installments`,
  op.`payment_value`,
  o.`order_purchase_timestamp`,
  DATE(o.`order_purchase_timestamp`) AS `dt`
FROM `ods_olist_order_payments_dataset` op
LEFT JOIN `ods_olist_orders_dataset` o ON op.`order_id` = o.`order_id`"""

    return {
        "table_name": f"{BIZ_DB}.{dwd_name}",
        "table_name_short": dwd_name,
        "layer": "DWD",
        "ddl_sql": ddl,
        "etl_sql": etl,
        "source_tables": json.dumps([
            f"{BIZ_DB}.ods_olist_order_payments_dataset",
            f"{BIZ_DB}.ods_olist_orders_dataset",
        ]),
    }


def design_dwd_order_review(ods_schemas: dict) -> dict:
    """DWD: 订单评价明细宽表"""
    dwd_name = "dwd_order_review"
    cols = [
        ("review_id", "VARCHAR(64)"),
        ("order_id", "VARCHAR(64)"),
        ("review_score", "INT"),
        ("review_comment_title", "VARCHAR(500)"),
        ("review_comment_message", "TEXT"),
        ("review_creation_date", "DATETIME"),
        ("review_answer_timestamp", "DATETIME"),
        ("order_purchase_timestamp", "DATETIME"),
        ("dt", "DATE"),
    ]
    col_defs = [f"  `{c}` {t}" for c, t in cols]
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{dwd_name}` (\n"
        + ",\n".join(col_defs)
        + "\n) DUPLICATE KEY(`review_id`)\n"
        f"DISTRIBUTED BY HASH(`review_id`) BUCKETS 4\n"
        f"PROPERTIES ('replication_num' = '1')"
    )

    etl = f"""INSERT INTO `{dwd_name}` (
  `review_id`, `order_id`, `review_score`, `review_comment_title`,
  `review_comment_message`, `review_creation_date`, `review_answer_timestamp`,
  `order_purchase_timestamp`, `dt`
)
SELECT
  r.`review_id`,
  r.`order_id`,
  r.`review_score`,
  r.`review_comment_title`,
  r.`review_comment_message`,
  r.`review_creation_date`,
  r.`review_answer_timestamp`,
  o.`order_purchase_timestamp`,
  DATE(o.`order_purchase_timestamp`) AS `dt`
FROM `ods_olist_order_reviews_dataset` r
LEFT JOIN `ods_olist_orders_dataset` o ON r.`order_id` = o.`order_id`"""

    return {
        "table_name": f"{BIZ_DB}.{dwd_name}",
        "table_name_short": dwd_name,
        "layer": "DWD",
        "ddl_sql": ddl,
        "etl_sql": etl,
        "source_tables": json.dumps([
            f"{BIZ_DB}.ods_olist_order_reviews_dataset",
            f"{BIZ_DB}.ods_olist_orders_dataset",
        ]),
    }


# ──────────────────────────────────────────────
# DWS Layer Design
# ──────────────────────────────────────────────

def design_dws_trade_order_1d() -> dict:
    """DWS: 日交易汇总表 — GMV + 订单量 + 客单价"""
    dws_name = "dws_trade_order_1d"
    cols = [
        ("dt", "DATE"),
        ("gmv", "DOUBLE COMMENT '日GMV(含运费)'"),
        ("gmv_excl_freight", "DOUBLE COMMENT '日GMV(不含运费)'"),
        ("order_count", "BIGINT COMMENT '日订单量'"),
        ("item_count", "BIGINT COMMENT '日商品件数'"),
        ("avg_order_value", "DOUBLE COMMENT '客单价'"),
        ("total_freight", "DOUBLE COMMENT '日运费总额'"),
    ]
    col_defs = [f"  `{c}` {t}" for c, t in cols]
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{dws_name}` (\n"
        + ",\n".join(col_defs)
        + "\n) UNIQUE KEY(`dt`)\n"
        f"DISTRIBUTED BY HASH(`dt`) BUCKETS 4\n"
        f"PROPERTIES ('replication_num' = '1')"
    )

    etl = f"""INSERT INTO `{dws_name}` (
  `dt`, `gmv`, `gmv_excl_freight`, `order_count`, `item_count`, `avg_order_value`, `total_freight`
)
SELECT
  `dt`,
  SUM(`price` + `freight_value`) AS `gmv`,
  SUM(`price`) AS `gmv_excl_freight`,
  COUNT(DISTINCT `order_id`) AS `order_count`,
  COUNT(*) AS `item_count`,
  CASE WHEN COUNT(DISTINCT `order_id`) > 0
       THEN SUM(`price` + `freight_value`) / COUNT(DISTINCT `order_id`)
       ELSE 0 END AS `avg_order_value`,
  SUM(`freight_value`) AS `total_freight`
FROM `dwd_order_detail`
GROUP BY `dt`"""

    return {
        "table_name": f"{BIZ_DB}.{dws_name}",
        "table_name_short": dws_name,
        "layer": "DWS",
        "ddl_sql": ddl,
        "etl_sql": etl,
        "source_tables": json.dumps([f"{BIZ_DB}.dwd_order_detail"]),
    }


def design_dws_category_sale_1d() -> dict:
    """DWS: 品类销售日汇总表 — 品类销售分布"""
    dws_name = "dws_category_sale_1d"
    cols = [
        ("dt", "DATE"),
        ("product_category_name_english", "VARCHAR(100)"),
        ("gmv", "DOUBLE COMMENT '品类日GMV'"),
        ("order_count", "BIGINT COMMENT '品类日订单量'"),
        ("item_count", "BIGINT COMMENT '品类日商品件数'"),
        ("avg_price", "DOUBLE COMMENT '品类平均单价'"),
    ]
    col_defs = [f"  `{c}` {t}" for c, t in cols]
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{dws_name}` (\n"
        + ",\n".join(col_defs)
        + "\n) UNIQUE KEY(`dt`, `product_category_name_english`)\n"
        f"DISTRIBUTED BY HASH(`dt`) BUCKETS 4\n"
        f"PROPERTIES ('replication_num' = '1')"
    )

    etl = f"""INSERT INTO `{dws_name}` (
  `dt`, `product_category_name_english`, `gmv`, `order_count`, `item_count`, `avg_price`
)
SELECT
  `dt`,
  `product_category_name_english`,
  SUM(`price` + `freight_value`) AS `gmv`,
  COUNT(DISTINCT `order_id`) AS `order_count`,
  COUNT(*) AS `item_count`,
  CASE WHEN COUNT(*) > 0 THEN SUM(`price`) / COUNT(*) ELSE 0 END AS `avg_price`
FROM `dwd_order_detail`
WHERE `product_category_name_english` IS NOT NULL
GROUP BY `dt`, `product_category_name_english`"""

    return {
        "table_name": f"{BIZ_DB}.{dws_name}",
        "table_name_short": dws_name,
        "layer": "DWS",
        "ddl_sql": ddl,
        "etl_sql": etl,
        "source_tables": json.dumps([f"{BIZ_DB}.dwd_order_detail"]),
    }


def design_dws_customer_repurchase_1d() -> dict:
    """DWS: 客户复购日汇总表 — 复购率"""
    dws_name = "dws_customer_repurchase_1d"
    cols = [
        ("dt", "DATE"),
        ("total_customers", "BIGINT COMMENT '当日下单客户数'"),
        ("new_customers", "BIGINT COMMENT '当日新客数'"),
        ("repeat_customers", "BIGINT COMMENT '当日复购客数'"),
        ("repeat_rate", "DOUBLE COMMENT '当日复购率'"),
        ("cum_total_customers", "BIGINT COMMENT '累计客户总数'"),
        ("cum_repeat_customers", "BIGINT COMMENT '累计复购客户数'"),
        ("cum_repeat_rate", "DOUBLE COMMENT '累计复购率'"),
    ]
    col_defs = [f"  `{c}` {t}" for c, t in cols]
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{dws_name}` (\n"
        + ",\n".join(col_defs)
        + "\n) UNIQUE KEY(`dt`)\n"
        f"DISTRIBUTED BY HASH(`dt`) BUCKETS 4\n"
        f"PROPERTIES ('replication_num' = '1')"
    )

    etl = f"""INSERT INTO `{dws_name}` (
  `dt`, `total_customers`, `new_customers`, `repeat_customers`, `repeat_rate`,
  `cum_total_customers`, `cum_repeat_customers`, `cum_repeat_rate`
)
SELECT
  t.`dt`,
  t.`total_customers`,
  t.`new_customers`,
  t.`repeat_customers`,
  CASE WHEN t.`total_customers` > 0
       THEN CAST(t.`repeat_customers` AS DOUBLE) / t.`total_customers`
       ELSE 0 END AS `repeat_rate`,
  c.`cum_total_customers`,
  c.`cum_repeat_customers`,
  CASE WHEN c.`cum_total_customers` > 0
       THEN CAST(c.`cum_repeat_customers` AS DOUBLE) / c.`cum_total_customers`
       ELSE 0 END AS `cum_repeat_rate`
FROM (
  SELECT
    `dt`,
    COUNT(DISTINCT `customer_unique_id`) AS `total_customers`,
    COUNT(DISTINCT CASE WHEN `is_first_order` = 1 THEN `customer_unique_id` END) AS `new_customers`,
    COUNT(DISTINCT CASE WHEN `is_first_order` = 0 THEN `customer_unique_id` END) AS `repeat_customers`
  FROM (
    SELECT
      `dt`,
      `customer_unique_id`,
      CASE WHEN ROW_NUMBER() OVER (PARTITION BY `customer_unique_id` ORDER BY `dt`) = 1
           THEN 1 ELSE 0 END AS `is_first_order`
    FROM (
      SELECT DISTINCT `dt`, `customer_unique_id`
      FROM `dwd_order_detail`
    ) sub
  ) sub2
  GROUP BY `dt`
) t
LEFT JOIN (
  SELECT
    a.`dt`,
    COUNT(DISTINCT b.`customer_unique_id`) AS `cum_total_customers`,
    COUNT(DISTINCT CASE WHEN b.`order_cnt` > 1 THEN b.`customer_unique_id` END) AS `cum_repeat_customers`
  FROM (SELECT DISTINCT `dt` FROM `dwd_order_detail`) a
  LEFT JOIN (
    SELECT `customer_unique_id`, COUNT(DISTINCT `dt`) AS `order_cnt`,
           MIN(`dt`) AS `first_dt`
    FROM (SELECT DISTINCT `dt`, `customer_unique_id` FROM `dwd_order_detail`) sub
    GROUP BY `customer_unique_id`
  ) b ON b.`first_dt` <= a.`dt`
  GROUP BY a.`dt`
) c ON t.`dt` = c.`dt`"""

    return {
        "table_name": f"{BIZ_DB}.{dws_name}",
        "table_name_short": dws_name,
        "layer": "DWS",
        "ddl_sql": ddl,
        "etl_sql": etl,
        "source_tables": json.dumps([f"{BIZ_DB}.dwd_order_detail"]),
    }


def design_dws_delivery_performance_1d() -> dict:
    """DWS: 配送时效日汇总表 — 配送时效"""
    dws_name = "dws_delivery_performance_1d"
    cols = [
        ("dt", "DATE"),
        ("delivered_order_count", "BIGINT COMMENT '当日送达订单数'"),
        ("avg_delivery_days", "DOUBLE COMMENT '平均配送天数(下单→送达)'"),
        ("avg_carrier_days", "DOUBLE COMMENT '平均承运天数(下单→承运)'"),
        ("avg_estimated_days", "DOUBLE COMMENT '平均预计配送天数'"),
        ("on_time_rate", "DOUBLE COMMENT '准时送达率'"),
        ("max_delivery_days", "DOUBLE COMMENT '最长配送天数'"),
    ]
    col_defs = [f"  `{c}` {t}" for c, t in cols]
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{dws_name}` (\n"
        + ",\n".join(col_defs)
        + "\n) UNIQUE KEY(`dt`)\n"
        f"DISTRIBUTED BY HASH(`dt`) BUCKETS 4\n"
        f"PROPERTIES ('replication_num' = '1')"
    )

    etl = f"""INSERT INTO `{dws_name}` (
  `dt`, `delivered_order_count`, `avg_delivery_days`, `avg_carrier_days`,
  `avg_estimated_days`, `on_time_rate`, `max_delivery_days`
)
SELECT
  `dt`,
  COUNT(DISTINCT `order_id`) AS `delivered_order_count`,
  AVG(DATEDIFF(`order_delivered_customer_date`, `order_purchase_timestamp`)) AS `avg_delivery_days`,
  AVG(DATEDIFF(`order_delivered_carrier_date`, `order_purchase_timestamp`)) AS `avg_carrier_days`,
  AVG(DATEDIFF(`order_estimated_delivery_date`, `order_purchase_timestamp`)) AS `avg_estimated_days`,
  CAST(SUM(CASE WHEN `order_delivered_customer_date` <= `order_estimated_delivery_date` THEN 1 ELSE 0 END) AS DOUBLE)
    / NULLIF(COUNT(DISTINCT `order_id`), 0) AS `on_time_rate`,
  MAX(DATEDIFF(`order_delivered_customer_date`, `order_purchase_timestamp`)) AS `max_delivery_days`
FROM `dwd_order_detail`
WHERE `order_status` = 'delivered'
  AND `order_delivered_customer_date` IS NOT NULL
  AND `order_purchase_timestamp` IS NOT NULL
GROUP BY `dt`"""

    return {
        "table_name": f"{BIZ_DB}.{dws_name}",
        "table_name_short": dws_name,
        "layer": "DWS",
        "ddl_sql": ddl,
        "etl_sql": etl,
        "source_tables": json.dumps([f"{BIZ_DB}.dwd_order_detail"]),
    }


def design_dws_payment_type_1d() -> dict:
    """DWS: 支付方式日汇总表"""
    dws_name = "dws_payment_type_1d"
    cols = [
        ("dt", "DATE"),
        ("payment_type", "VARCHAR(30)"),
        ("payment_count", "BIGINT COMMENT '支付笔数'"),
        ("payment_total", "DOUBLE COMMENT '支付总额'"),
        ("avg_payment_value", "DOUBLE COMMENT '平均支付金额'"),
        ("avg_installments", "DOUBLE COMMENT '平均分期数'"),
    ]
    col_defs = [f"  `{c}` {t}" for c, t in cols]
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{dws_name}` (\n"
        + ",\n".join(col_defs)
        + "\n) UNIQUE KEY(`dt`, `payment_type`)\n"
        f"DISTRIBUTED BY HASH(`dt`) BUCKETS 4\n"
        f"PROPERTIES ('replication_num' = '1')"
    )

    etl = f"""INSERT INTO `{dws_name}` (
  `dt`, `payment_type`, `payment_count`, `payment_total`, `avg_payment_value`, `avg_installments`
)
SELECT
  `dt`,
  `payment_type`,
  COUNT(*) AS `payment_count`,
  SUM(`payment_value`) AS `payment_total`,
  AVG(`payment_value`) AS `avg_payment_value`,
  AVG(`payment_installments`) AS `avg_installments`
FROM `dwd_order_payment`
GROUP BY `dt`, `payment_type`"""

    return {
        "table_name": f"{BIZ_DB}.{dws_name}",
        "table_name_short": dws_name,
        "layer": "DWS",
        "ddl_sql": ddl,
        "etl_sql": etl,
        "source_tables": json.dumps([f"{BIZ_DB}.dwd_order_payment"]),
    }


# ──────────────────────────────────────────────
# ADS Layer Design
# ──────────────────────────────────────────────

def design_ads_gmv_daily() -> dict:
    """ADS: 日GMV看板表"""
    ads_name = "ads_gmv_daily"
    cols = [
        ("dt", "DATE"),
        ("gmv", "DOUBLE COMMENT '日GMV(含运费)'"),
        ("gmv_excl_freight", "DOUBLE COMMENT '日GMV(不含运费)'"),
        ("order_count", "BIGINT COMMENT '日订单量'"),
        ("avg_order_value", "DOUBLE COMMENT '客单价'"),
        ("gmv_wow", "DOUBLE COMMENT 'GMV周同比'"),
        ("gmv_dod", "DOUBLE COMMENT 'GMV日环比'"),
    ]
    col_defs = [f"  `{c}` {t}" for c, t in cols]
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{ads_name}` (\n"
        + ",\n".join(col_defs)
        + "\n) UNIQUE KEY(`dt`)\n"
        f"DISTRIBUTED BY HASH(`dt`) BUCKETS 1\n"
        f"PROPERTIES ('replication_num' = '1')"
    )

    etl = f"""INSERT INTO `{ads_name}` (
  `dt`, `gmv`, `gmv_excl_freight`, `order_count`, `avg_order_value`, `gmv_wow`, `gmv_dod`
)
SELECT
  t.`dt`,
  t.`gmv`,
  t.`gmv_excl_freight`,
  t.`order_count`,
  t.`avg_order_value`,
  CASE WHEN w.`gmv` > 0 THEN (t.`gmv` - w.`gmv`) / w.`gmv` ELSE NULL END AS `gmv_wow`,
  CASE WHEN d.`gmv` > 0 THEN (t.`gmv` - d.`gmv`) / d.`gmv` ELSE NULL END AS `gmv_dod`
FROM `dws_trade_order_1d` t
LEFT JOIN `dws_trade_order_1d` w ON w.`dt` = DATE_SUB(t.`dt`, INTERVAL 7 DAY)
LEFT JOIN `dws_trade_order_1d` d ON d.`dt` = DATE_SUB(t.`dt`, INTERVAL 1 DAY)"""

    return {
        "table_name": f"{BIZ_DB}.{ads_name}",
        "table_name_short": ads_name,
        "layer": "ADS",
        "ddl_sql": ddl,
        "etl_sql": etl,
        "source_tables": json.dumps([f"{BIZ_DB}.dws_trade_order_1d"]),
    }


def design_ads_customer_repurchase() -> dict:
    """ADS: 复购率分析表"""
    ads_name = "ads_customer_repurchase"
    cols = [
        ("dt", "DATE"),
        ("total_customers", "BIGINT COMMENT '当日下单客户数'"),
        ("new_customers", "BIGINT COMMENT '当日新客数'"),
        ("repeat_customers", "BIGINT COMMENT '当日复购客数'"),
        ("repeat_rate", "DOUBLE COMMENT '当日复购率'"),
        ("cum_total_customers", "BIGINT COMMENT '累计客户总数'"),
        ("cum_repeat_customers", "BIGINT COMMENT '累计复购客户数'"),
        ("cum_repeat_rate", "DOUBLE COMMENT '累计复购率'"),
    ]
    col_defs = [f"  `{c}` {t}" for c, t in cols]
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{ads_name}` (\n"
        + ",\n".join(col_defs)
        + "\n) UNIQUE KEY(`dt`)\n"
        f"DISTRIBUTED BY HASH(`dt`) BUCKETS 1\n"
        f"PROPERTIES ('replication_num' = '1')"
    )

    etl = f"""INSERT INTO `{ads_name}` (
  `dt`, `total_customers`, `new_customers`, `repeat_customers`, `repeat_rate`,
  `cum_total_customers`, `cum_repeat_customers`, `cum_repeat_rate`
)
SELECT
  `dt`, `total_customers`, `new_customers`, `repeat_customers`, `repeat_rate`,
  `cum_total_customers`, `cum_repeat_customers`, `cum_repeat_rate`
FROM `dws_customer_repurchase_1d`"""

    return {
        "table_name": f"{BIZ_DB}.{ads_name}",
        "table_name_short": ads_name,
        "layer": "ADS",
        "ddl_sql": ddl,
        "etl_sql": etl,
        "source_tables": json.dumps([f"{BIZ_DB}.dws_customer_repurchase_1d"]),
    }


def design_ads_delivery_performance() -> dict:
    """ADS: 配送时效分析表"""
    ads_name = "ads_delivery_performance"
    cols = [
        ("dt", "DATE"),
        ("delivered_order_count", "BIGINT COMMENT '当日送达订单数'"),
        ("avg_delivery_days", "DOUBLE COMMENT '平均配送天数'"),
        ("avg_carrier_days", "DOUBLE COMMENT '平均承运天数'"),
        ("on_time_rate", "DOUBLE COMMENT '准时送达率'"),
        ("max_delivery_days", "DOUBLE COMMENT '最长配送天数'"),
    ]
    col_defs = [f"  `{c}` {t}" for c, t in cols]
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{ads_name}` (\n"
        + ",\n".join(col_defs)
        + "\n) UNIQUE KEY(`dt`)\n"
        f"DISTRIBUTED BY HASH(`dt`) BUCKETS 1\n"
        f"PROPERTIES ('replication_num' = '1')"
    )

    etl = f"""INSERT INTO `{ads_name}` (
  `dt`, `delivered_order_count`, `avg_delivery_days`, `avg_carrier_days`,
  `on_time_rate`, `max_delivery_days`
)
SELECT
  `dt`, `delivered_order_count`, `avg_delivery_days`, `avg_carrier_days`,
  `on_time_rate`, `max_delivery_days`
FROM `dws_delivery_performance_1d`"""

    return {
        "table_name": f"{BIZ_DB}.{ads_name}",
        "table_name_short": ads_name,
        "layer": "ADS",
        "ddl_sql": ddl,
        "etl_sql": etl,
        "source_tables": json.dumps([f"{BIZ_DB}.dws_delivery_performance_1d"]),
    }


def design_ads_category_sales_distribution() -> dict:
    """ADS: 品类销售分布表"""
    ads_name = "ads_category_sales_distribution"
    cols = [
        ("dt", "DATE"),
        ("product_category_name_english", "VARCHAR(100)"),
        ("gmv", "DOUBLE COMMENT '品类日GMV'"),
        ("order_count", "BIGINT COMMENT '品类日订单量'"),
        ("item_count", "BIGINT COMMENT '品类日商品件数'"),
        ("avg_price", "DOUBLE COMMENT '品类平均单价'"),
        ("gmv_share", "DOUBLE COMMENT 'GMV占比'"),
        ("order_share", "DOUBLE COMMENT '订单量占比'"),
    ]
    col_defs = [f"  `{c}` {t}" for c, t in cols]
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{ads_name}` (\n"
        + ",\n".join(col_defs)
        + "\n) UNIQUE KEY(`dt`, `product_category_name_english`)\n"
        f"DISTRIBUTED BY HASH(`dt`) BUCKETS 4\n"
        f"PROPERTIES ('replication_num' = '1')"
    )

    etl = f"""INSERT INTO `{ads_name}` (
  `dt`, `product_category_name_english`, `gmv`, `order_count`, `item_count`,
  `avg_price`, `gmv_share`, `order_share`
)
SELECT
  c.`dt`,
  c.`product_category_name_english`,
  c.`gmv`,
  c.`order_count`,
  c.`item_count`,
  c.`avg_price`,
  CASE WHEN t.`total_gmv` > 0 THEN c.`gmv` / t.`total_gmv` ELSE 0 END AS `gmv_share`,
  CASE WHEN t.`total_orders` > 0 THEN c.`order_count` / t.`total_orders` ELSE 0 END AS `order_share`
FROM `dws_category_sale_1d` c
LEFT JOIN (
  SELECT `dt`, SUM(`gmv`) AS `total_gmv`, SUM(`order_count`) AS `total_orders`
  FROM `dws_category_sale_1d`
  GROUP BY `dt`
) t ON c.`dt` = t.`dt`"""

    return {
        "table_name": f"{BIZ_DB}.{ads_name}",
        "table_name_short": ads_name,
        "layer": "ADS",
        "ddl_sql": ddl,
        "etl_sql": etl,
        "source_tables": json.dumps([f"{BIZ_DB}.dws_category_sale_1d"]),
    }


def design_ads_order_count_daily() -> dict:
    """ADS: 日订单量看板表"""
    ads_name = "ads_order_count_daily"
    cols = [
        ("dt", "DATE"),
        ("order_count", "BIGINT COMMENT '日订单量'"),
        ("item_count", "BIGINT COMMENT '日商品件数'"),
        ("items_per_order", "DOUBLE COMMENT '平均每单件数'"),
        ("order_count_wow", "DOUBLE COMMENT '订单量周同比'"),
        ("order_count_dod", "DOUBLE COMMENT '订单量日环比'"),
    ]
    col_defs = [f"  `{c}` {t}" for c, t in cols]
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{ads_name}` (\n"
        + ",\n".join(col_defs)
        + "\n) UNIQUE KEY(`dt`)\n"
        f"DISTRIBUTED BY HASH(`dt`) BUCKETS 1\n"
        f"PROPERTIES ('replication_num' = '1')"
    )

    etl = f"""INSERT INTO `{ads_name}` (
  `dt`, `order_count`, `item_count`, `items_per_order`, `order_count_wow`, `order_count_dod`
)
SELECT
  t.`dt`,
  t.`order_count`,
  t.`item_count`,
  CASE WHEN t.`order_count` > 0 THEN CAST(t.`item_count` AS DOUBLE) / t.`order_count` ELSE 0 END AS `items_per_order`,
  CASE WHEN w.`order_count` > 0 THEN (t.`order_count` - w.`order_count`) / w.`order_count` ELSE NULL END AS `order_count_wow`,
  CASE WHEN d.`order_count` > 0 THEN (t.`order_count` - d.`order_count`) / d.`order_count` ELSE NULL END AS `order_count_dod`
FROM `dws_trade_order_1d` t
LEFT JOIN `dws_trade_order_1d` w ON w.`dt` = DATE_SUB(t.`dt`, INTERVAL 7 DAY)
LEFT JOIN `dws_trade_order_1d` d ON d.`dt` = DATE_SUB(t.`dt`, INTERVAL 1 DAY)"""

    return {
        "table_name": f"{BIZ_DB}.{ads_name}",
        "table_name_short": ads_name,
        "layer": "ADS",
        "ddl_sql": ddl,
        "etl_sql": etl,
        "source_tables": json.dumps([f"{BIZ_DB}.dws_trade_order_1d"]),
    }


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    global PLAN_ID, BIZ_DB
    import argparse
    ap = argparse.ArgumentParser(description="Olist 电商领域分层表设计（5 大核心指标）")
    ap.add_argument("--plan-id", required=True, help="已有 plan 的 plan_id")
    ap.add_argument("--biz-database", required=True, help="业务数据库名（如 olist）")
    args = ap.parse_args()
    PLAN_ID = args.plan_id
    BIZ_DB = args.biz_database

    print("Reading ODS schemas from plan_table...", file=sys.stderr)
    ods_schemas = read_ods_from_plan(PLAN_ID)
    print(f"Found {len(ods_schemas)} ODS tables", file=sys.stderr)

    # Design DWD tables
    dwd_tables = [
        design_dwd_order_detail(ods_schemas),
        design_dwd_order_payment(ods_schemas),
        design_dwd_order_review(ods_schemas),
    ]

    # Design DWS tables
    dws_tables = [
        design_dws_trade_order_1d(),
        design_dws_category_sale_1d(),
        design_dws_customer_repurchase_1d(),
        design_dws_delivery_performance_1d(),
        design_dws_payment_type_1d(),
    ]

    # Design ADS tables
    ads_tables = [
        design_ads_gmv_daily(),
        design_ads_order_count_daily(),
        design_ads_customer_repurchase(),
        design_ads_delivery_performance(),
        design_ads_category_sales_distribution(),
    ]

    all_new = dwd_tables + dws_tables + ads_tables

    # Record to plan_table
    print(f"Recording {len(all_new)} tables to plan_table...", file=sys.stderr)
    record_to_plan_table(PLAN_ID, all_new)

    result = {
        "ok": True,
        "plan_id": PLAN_ID,
        "biz_database": BIZ_DB,
        "dwd_tables": len(dwd_tables),
        "dws_tables": len(dws_tables),
        "ads_tables": len(ads_tables),
        "total_new": len(all_new),
        "tables": [t["table_name_short"] for t in all_new],
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
