#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
seed_anomalies.py —— 为 DEMO 制造可被巡检命中的异常（只对 db 副本操作，保原始库干净）。

为什么要有这个脚本：
    真实 olist.db 数据平稳、质量尚可，巡检虽能命中一些真实问题（null 率、缺索引），
    但要稳定演示「schema 漂移 / 时序突增 / PK 重复 / 负金额 / 全空列」等场景，
    需要可控地往副本库里注入异常，让 demo 流程能稳定复现"巡检→发现→修复"闭环。

    本脚本一律先 cp 原始 db 到副本，再在副本上注入，绝不碰原始 olist.db。

用法:
    # 造一份带各类异常的副本（默认全部注入）
    python seed_anomalies.py --src warehouse_demo.db --out training_warehouse.db

    # 只注入某几类（逗号分隔）
    python seed_anomalies.py --src olist.db --out demo.db --only drift,dup,negative

注入类型:
    drift        schema 漂移：加列 + 改类型（触发 DRF findings）
    null         批量置空某列（触发 NUL high）
    dup          复制行造 PK 重复（触发 UNI high）
    spike        复制某天数据造时序突增（触发 TSR spike）
    negative     把部分金额改负（触发 DST high）
    emptycol     加一个全空列（触发 NUL high，整列空）
    orphan       删部分 items 的对应 orders 记录造孤儿（触发血缘追溯 RCA）
    metric_drift 改部分 payments.payment_value 使其与 items.price 严重背离
                 （放大指标口径不一致 MCT，GMV 支付口径 vs 商品口径差异飙升）

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
    sp = Path(src).expanduser().resolve()
    op = Path(out).expanduser().resolve()
    if not sp.exists():
        raise FileNotFoundError(f"源 db 不存在: {sp}")
    shutil.copy2(sp, op)
    # 副本的 -shm/-wal 侧车一并清掉，避免陈旧
    for ext in ("-shm", "-wal"):
        side = Path(str(sp) + ext)
        if side.exists():
            shutil.copy2(side, Path(str(op) + ext))
    return op


def has_table(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def inject_drift(conn):
    """schema 漂移：加列 + 加一个新表。"""
    table = "olist_orders"
    conn.execute(f"ALTER TABLE {table} ADD COLUMN test_drift_col TEXT;")
    conn.execute("CREATE TABLE olist_temp_drift (id INTEGER, note TEXT);")
    conn.execute("INSERT INTO olist_temp_drift VALUES (1, 'temp'), (2, 'temp2');")


def inject_null(conn):
    """批量置空 review_comment_title（本就有 88% null，改到 ~95% 让 NUL 更醒目）。"""
    table = "dwd_fact_reviews" if has_table(conn, "dwd_fact_reviews") else "olist_order_reviews"
    conn.execute(f'UPDATE "{table}" SET review_comment_title = NULL '
                 "WHERE review_comment_title IS NOT NULL AND rowid % 2 = 0;")


def inject_dup(conn):
    """造业务键重复：复制 10000 行 customers，换新 customer_id 但保留相同
    customer_unique_id → distinct ratio 跌到 ~0.91，触发 UNI 唯一性破坏检测。

    customer_unique_id 无 UNIQUE 约束，可重复插入。
    原 50 行对 9.9 万基数不显著（ratio≈0.9995），检测不到；现复制 1 万行
    使业务键重复成为明显信号（ratio≈0.91 < 0.93）。"""
    if has_table(conn, "dwd_dim_customer"):
        conn.execute(
            "INSERT INTO dwd_dim_customer(customer_id, customer_unique_id, "
            "customer_zip_code_prefix, customer_city, customer_state) "
            "SELECT 'dup_' || rowid || '_' || customer_id, customer_unique_id, "
            "customer_zip_code_prefix, customer_city, customer_state "
            "FROM dwd_dim_customer WHERE rowid <= 10000;"
        )
    else:
        conn.execute(
            "INSERT INTO olist_customers(customer_id, customer_unique_id, "
            "customer_zip_code_prefix, customer_city, customer_state) "
            "SELECT 'dup_' || rowid || '_' || customer_id, customer_unique_id, "
            "customer_zip_code_prefix, customer_city, customer_state "
            "FROM olist_customers WHERE rowid <= 10000;"
        )


def inject_spike(conn):
    """在数据中部某正常日造时序突增（spike）。
    选一个原本订单量正常的日期，把当天数据复制 50 倍注入，使该天 row_count
    远超历史均值，被 z-score（需 --scan-all 全程扫描）标记为 spike。

    注意：
    - z-score 默认 detection_window 只看最近 N 桶；历史日突增要用 --scan-all 才能扫到。
    - expanding window 会把当前值计入均值稀释 Z，所以倍数要够大（50 倍）。
    - olist 有真实黑色星期五突增（2017-11-24），本注入另选正常日制造"人造突增"作对比。
    """
    # 选 2017-10-15（正常日）做注入目标；如该日无数据则回退到任意中部日
    orders_table = "dwd_fact_orders" if has_table(conn, "dwd_fact_orders") else "olist_orders"
    time_col = "purchase_ts" if orders_table == "dwd_fact_orders" else "order_purchase_timestamp"
    target = conn.execute(
        f"SELECT date({time_col}) FROM {orders_table} "
        f"WHERE date({time_col})='2017-10-15' LIMIT 1"
    ).fetchone()
    if not target:
        # 回退：取数据第 30 个百分位的日期（避开首尾稀疏与已知高峰日）
        target = conn.execute(
            f"SELECT date({time_col}) d FROM {orders_table} "
            "GROUP BY d ORDER BY COUNT(*) ASC LIMIT 1 OFFSET 200"
        ).fetchone()
    target_day = target[0]
    if orders_table == "dwd_fact_orders":
        conn.execute(
        "INSERT INTO dwd_fact_orders(order_id, customer_id, order_status, purchase_ts, "
        "approved_ts, delivered_carrier_ts, delivered_customer_ts, estimated_delivery_ts) "
        "SELECT 'spk_' || b.k || '_' || rowid || '_' || order_id, customer_id, order_status, "
        "purchase_ts, approved_ts, delivered_carrier_ts, delivered_customer_ts, estimated_delivery_ts "
        "FROM dwd_fact_orders, "
        "(WITH RECURSIVE seq(k) AS (SELECT '01' UNION ALL SELECT printf('%02d', CAST(k AS INT)+1) "
        "FROM seq WHERE CAST(k AS INT)<50) "
        "SELECT k FROM seq) b "
        "WHERE date(purchase_ts) = ?;",
        (target_day,)
        )
    else:
        conn.execute(
        "INSERT INTO olist_orders(order_id, customer_id, order_status, order_purchase_timestamp, "
        "order_approved_at, order_delivered_carrier_date, order_delivered_customer_date, "
        "order_estimated_delivery_date) "
        "SELECT 'spk_' || b.k || '_' || rowid || '_' || order_id, customer_id, order_status, "
        "order_purchase_timestamp, order_approved_at, order_delivered_carrier_date, "
        "order_delivered_customer_date, order_estimated_delivery_date "
        "FROM olist_orders, "
        "(WITH RECURSIVE seq(k) AS (SELECT '01' UNION ALL SELECT printf('%02d', CAST(k AS INT)+1) "
        "FROM seq WHERE CAST(k AS INT)<50) "
        "SELECT k FROM seq) b "
        "WHERE date(order_purchase_timestamp) = ?;", (target_day,))
    print(f"    (spike 注入日: {target_day})")


def inject_negative(conn):
    """把部分订单项 price 改成负值。"""
    table = "dwd_fact_order_items" if has_table(conn, "dwd_fact_order_items") else "olist_order_items"
    conn.execute(f'UPDATE "{table}" SET price = -ABS(price) WHERE rowid <= 20;')


def inject_emptycol(conn):
    """加一个全空列。"""
    table = "dwd_dim_seller" if has_table(conn, "dwd_dim_seller") else "olist_sellers"
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN test_empty_col TEXT;')


def inject_orphan(conn):
    """造孤儿：删除部分 olist_order_items 记录在 olist_orders 中对应的订单。
    即保留 items 行，但删掉它们引用的 orders 父行 → 血缘链上 items 引用的 order_id
    在 orders 表找不到 → trace 追溯时 items 异常可溯到上游 orders 缺失。

    删 50 个订单（连带其 items 仍留着），制造 50 个孤儿 item 行。
    """
    # 选 50 个订单的 order_id，从 olist_orders 删掉（items 留着 → 孤儿）
    orders_table = "dwd_fact_orders" if has_table(conn, "dwd_fact_orders") else "olist_orders"
    targets = [r[0] for r in conn.execute(
        f"SELECT order_id FROM {orders_table} WHERE rowid <= 50"
    ).fetchall()]
    if not targets:
        return
    placeholders = ",".join("?" * len(targets))
    conn.execute(f"DELETE FROM {orders_table} WHERE order_id IN ({placeholders})", targets)
    print(f"    (orphan 删除 {len(targets)} 个 orders，对应 items 变孤儿)")


def inject_metric_drift(conn):
    """改部分 payments.payment_value 使其与 items.price 严重背离，
    放大 GMV 支付口径 vs 商品口径的差异（MCT finding 的 rel_diff 飙升）。

    把前 50000 笔支付（约一半）的 payment_value 乘 3 → 支付口径 GMV 显著虚高，
    与商品口径差异从 ~15% 放大到 ~50%+，演示口径治理的差异识别。
    """
    if has_table(conn, "dwd_fact_payments"):
        conn.execute("UPDATE dwd_fact_payments SET payment_value = payment_value * 3 "
                     "WHERE rowid <= 50000")
    if has_table(conn, "dws_daily_gmv"):
        conn.execute("UPDATE dws_daily_gmv SET gmv_payment = gmv_payment * 3 "
                     "WHERE rowid <= 50000")
    else:
        conn.execute("UPDATE olist_order_payments SET payment_value = payment_value * 3 "
                     "WHERE rowid <= 50000")
    print("    (metric_drift: 50000 笔 payment_value ×3，支付口径 GMV 显著虚高)")


INJECTORS = {
    "drift": inject_drift,
    "null": inject_null,
    "dup": inject_dup,
    "spike": inject_spike,
    "negative": inject_negative,
    "emptycol": inject_emptycol,
    "orphan": inject_orphan,
    "metric_drift": inject_metric_drift,
}


def inject_tasks(conn):
    """废弃：不再注入假样本任务。

    任务治理现在直读编排模块（数据开发）写入的真实
    dw_tasks/dw_task_runs/dw_workflow_nodes 表。假样本任务/运行记录已废弃，
    注入会破坏真实调度数据。本函数保留为空操作以兼容旧 --no-tasks 调用。
    """
    return


def main():
    ap = argparse.ArgumentParser(description="为 DEMO 造异常（只对副本）")
    ap.add_argument("--src", required=True, help="原始 db 路径（如 olist.db）")
    ap.add_argument("--out", required=True, help="输出副本路径")
    ap.add_argument("--only", default="",
                    help=f"只注入某几类，逗号分隔；可选: {','.join(INJECTORS)}")
    ap.add_argument("--no-tasks", action="store_true", help="不注入任务元数据")
    args = ap.parse_args()

    only = [s.strip() for s in args.only.split(",") if s.strip()] if args.only else list(INJECTORS)
    bad = [k for k in only if k not in INJECTORS]
    if bad:
        print(f"[ERROR] 未知注入类型: {bad}; 可选 {list(INJECTORS)}", file=sys.stderr)
        sys.exit(1)

    try:
        op = copy_db(args.src, args.out)
        conn = sqlite3.connect(str(op), timeout=30)
        conn.execute("BEGIN")
        for k in only:
            INJECTORS[k](conn)
            print(f"  [+] 已注入: {k}")
        if not args.no_tasks:
            inject_tasks(conn)
            print("  [+] 已注入: task metadata")
        conn.commit()
        conn.close()
        print(f"\n副本已生成: {op}")
        print(f"已注入类型: {only}")
        print("现在可对该副本跑巡检:")
        print(f"  python <orchestrator>/scripts/run_pipeline.py --db {op}")
    except sqlite3.Error as e:
        print(f"[ERROR] DB: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
