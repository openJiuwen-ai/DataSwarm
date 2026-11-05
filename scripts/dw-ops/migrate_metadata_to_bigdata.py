#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""migrate_metadata_to_bigdata.py —— 一次性把系统元数据迁到 bigdata 库。

数据来源：SQLite training_warehouse.db（权威，有完整演示数据）
目标：Doris bigdata 库（唯一一套系统元数据）
副作用：DROP olist 库的 7 张 dw_*（业务库清空元数据）

7 张表：dw_tasks / dw_task_runs / dw_task_instances / dw_workflows /
        dw_workflow_nodes / dw_layer_registry / dw_column_lineage

跑完即弃，不入仓库常驻。
"""
import sqlite3
import sys
from pathlib import Path

CONNECTOR = Path(__file__).resolve().parent.parent.parent / "dw-db-connector" / "scripts"
sys.path.insert(0, str(CONNECTOR))
import db_connector as dc  # noqa: E402

SQLITE_DB = Path("d:/job/code/DataSwarm/jiuwenswarm/resources/data/training_warehouse.db")
BUSINESS_REF = "doris://"        # olist 业务库
META_REF = "doris://meta"       # bigdata 元数据库

# Doris 建表 DDL（DISTRIBUTED BY HASH + replication_num=1，表结构对齐 SQLite）
# 主键 → Doris UNIQUE KEY（支持主键语义 + 可更新）
DORIS_DDL = {
    "dw_tasks": """
        CREATE TABLE IF NOT EXISTS dw_tasks (
            task_id           VARCHAR(100),
            name              VARCHAR(200),
            description       VARCHAR(2000),
            executor_type     VARCHAR(20),
            code              VARCHAR(10000),
            params_json       VARCHAR(2000),
            datasource_ref    VARCHAR(500),
            timeout_s         INT,
            sla_minutes       INT,
            retry_count       INT,
            retry_interval_s  INT,
            status            VARCHAR(20),
            schedule_type     VARCHAR(20),
            schedule_cron     VARCHAR(100),
            schedule_once_at  VARCHAR(50),
            layer             VARCHAR(20),
            upstream_tasks    VARCHAR(500),
            output_table      VARCHAR(200),
            owner             VARCHAR(50),
            created_at        VARCHAR(30),
            updated_at        VARCHAR(30)
        ) ENGINE=OLAP
        UNIQUE KEY(task_id)
        DISTRIBUTED BY HASH(task_id) BUCKETS 1
        PROPERTIES("replication_num"="1")
    """,
    "dw_task_runs": """
        CREATE TABLE IF NOT EXISTS dw_task_runs (
            run_id           VARCHAR(100),
            instance_id      VARCHAR(100),
            task_id          VARCHAR(100),
            node_id          VARCHAR(100),
            node_name        VARCHAR(200),
            status           VARCHAR(20),
            started_at       VARCHAR(30),
            finished_at      VARCHAR(30),
            sla_time         VARCHAR(30),
            duration_s       DOUBLE,
            exit_code        INT,
            output_rows      INT,
            retry_count      INT,
            stdout_path      VARCHAR(500),
            stderr_path      VARCHAR(500),
            error_message    VARCHAR(2000),
            result_summary   VARCHAR(2000),
            start_ts         VARCHAR(30),
            end_ts           VARCHAR(30)
        ) ENGINE=OLAP
        UNIQUE KEY(run_id)
        DISTRIBUTED BY HASH(run_id) BUCKETS 1
        PROPERTIES("replication_num"="1")
    """,
    "dw_task_instances": """
        CREATE TABLE IF NOT EXISTS dw_task_instances (
            instance_id    VARCHAR(100),
            ref_type       VARCHAR(20),
            ref_id         VARCHAR(100),
            trigger_type   VARCHAR(20),
            status         VARCHAR(20),
            started_at     VARCHAR(30),
            finished_at    VARCHAR(30),
            artifact_dir   VARCHAR(500),
            error_message  VARCHAR(2000),
            created_at     VARCHAR(30)
        ) ENGINE=OLAP
        UNIQUE KEY(instance_id)
        DISTRIBUTED BY HASH(instance_id) BUCKETS 1
        PROPERTIES("replication_num"="1")
    """,
    "dw_workflows": """
        CREATE TABLE IF NOT EXISTS dw_workflows (
            workflow_id       VARCHAR(100),
            name              VARCHAR(200),
            description       VARCHAR(2000),
            status            VARCHAR(20),
            enabled           INT,
            schedule_type     VARCHAR(20),
            schedule_cron     VARCHAR(100),
            schedule_once_at  VARCHAR(50),
            owner             VARCHAR(50),
            created_at        VARCHAR(30),
            updated_at        VARCHAR(30)
        ) ENGINE=OLAP
        UNIQUE KEY(workflow_id)
        DISTRIBUTED BY HASH(workflow_id) BUCKETS 1
        PROPERTIES("replication_num"="1")
    """,
    "dw_workflow_nodes": """
        CREATE TABLE IF NOT EXISTS dw_workflow_nodes (
            node_id          VARCHAR(100),
            workflow_id      VARCHAR(100),
            task_id          VARCHAR(100),
            group_name       VARCHAR(100),
            node_name        VARCHAR(200),
            upstream_nodes   VARCHAR(500),
            wait_review      INT,
            order_index      INT,
            created_at       VARCHAR(30)
        ) ENGINE=OLAP
        UNIQUE KEY(node_id)
        DISTRIBUTED BY HASH(node_id) BUCKETS 1
        PROPERTIES("replication_num"="1")
    """,
    "dw_layer_registry": """
        CREATE TABLE IF NOT EXISTS dw_layer_registry (
            table_name       VARCHAR(100),
            layer            VARCHAR(20),
            layer_comment    VARCHAR(200),
            upstream_tables  VARCHAR(500)
        ) ENGINE=OLAP
        UNIQUE KEY(table_name)
        DISTRIBUTED BY HASH(table_name) BUCKETS 1
        PROPERTIES("replication_num"="1")
    """,
    "dw_column_lineage": """
        CREATE TABLE IF NOT EXISTS dw_column_lineage (
            source_table    VARCHAR(100),
            source_column   VARCHAR(100),
            target_table    VARCHAR(100),
            target_column   VARCHAR(100),
            transformation  VARCHAR(500)
        ) ENGINE=OLAP
        UNIQUE KEY(source_table, source_column, target_table, target_column)
        DISTRIBUTED BY HASH(source_table, target_table) BUCKETS 1
        PROPERTIES("replication_num"="1")
    """,
}

# 列序（与 DDL 列序一致，用于 INSERT）
TABLE_COLS = {
    "dw_tasks": [
        "task_id", "name", "description", "executor_type", "code",
        "params_json", "datasource_ref", "timeout_s", "sla_minutes", "retry_count",
        "retry_interval_s", "status", "schedule_type", "schedule_cron", "schedule_once_at",
        "layer", "upstream_tasks", "output_table", "owner", "created_at",
        "updated_at",
    ],
    "dw_task_runs": [
        "run_id", "instance_id", "task_id", "node_id", "node_name",
        "status", "started_at", "finished_at", "sla_time", "duration_s",
        "exit_code", "output_rows", "retry_count", "stdout_path", "stderr_path",
        "error_message", "result_summary", "start_ts", "end_ts",
    ],
    "dw_task_instances": [
        "instance_id", "ref_type", "ref_id", "trigger_type", "status",
        "started_at", "finished_at", "artifact_dir", "error_message", "created_at",
    ],
    "dw_workflows": [
        "workflow_id", "name", "description", "status", "enabled",
        "schedule_type", "schedule_cron", "schedule_once_at", "owner", "created_at",
        "updated_at",
    ],
    "dw_workflow_nodes": [
        "node_id", "workflow_id", "task_id", "group_name", "node_name",
        "upstream_nodes", "wait_review", "order_index", "created_at",
    ],
    "dw_layer_registry": ["table_name", "layer", "layer_comment", "upstream_tables"],
    "dw_column_lineage": ["source_table", "source_column", "target_table", "target_column", "transformation"],
}

TABLES = list(DORIS_DDL.keys())


def main():
    if not SQLITE_DB.is_file():
        print(f"[ERROR] SQLite 源库不存在: {SQLITE_DB}", file=sys.stderr)
        return 1

    sconn = sqlite3.connect(str(SQLITE_DB))
    sconn.row_factory = sqlite3.Row
    # bigdata 元数据库连接（读写）
    mconn, mmeta = dc.connect(META_REF, read_only=False)
    mcur = mconn.cursor()

    print(f"源: SQLite {SQLITE_DB.name}")
    print(f"目标: Doris bigdata (meta={mmeta.get('database')})")
    print("=" * 60)

    for t in TABLES:
        cols = TABLE_COLS[t]
        # 1. 建表（IF NOT EXISTS 幂等）
        mcur.execute(DORIS_DDL[t])
        # 清空旧数据（重跑幂等）
        mcur.execute(f"TRUNCATE TABLE `{t}`")
        # 2. 从 SQLite 读数据
        try:
            rows = sconn.execute(f"SELECT {','.join(cols)} FROM {t}").fetchall()
        except sqlite3.OperationalError as e:
            print(f"[SKIP] {t}: SQLite 无此表 ({e})")
            continue
        # 3. 灌入 bigdata（pymysql 用 %s 占位符）
        placeholders = ",".join(["%s"] * len(cols))
        col_list = ",".join(f"`{c}`" for c in cols)
        if rows:
            mcur.executemany(
                f"INSERT INTO `{t}` ({col_list}) VALUES ({placeholders})",
                [tuple(r[c] for c in cols) for r in rows],
            )
        mconn.commit()
        print(f"[OK]   {t}: {len(rows)} rows → bigdata")

    mconn.close()
    print("=" * 60)

    # 4. DROP olist 业务库的 dw_*（清空业务库元数据）
    print("清理 olist 业务库的 dw_* 元数据表...")
    bconn, bmeta = dc.connect(BUSINESS_REF, read_only=False)
    bcur = bconn.cursor()
    bcur.execute("SHOW TABLES")
    olist_tables = {r[0] for r in bcur.fetchall()}
    dropped = 0
    for t in TABLES:
        if t in olist_tables:
            bcur.execute(f"DROP TABLE `{t}`")
            dropped += 1
            print(f"  DROP olist.{t}")
    bconn.close()
    print(f"[OK] 业务库清理 {dropped} 张 dw_*")
    sconn.close()
    print("\n迁移完成。bigdata 现在持有唯一一套系统元数据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
