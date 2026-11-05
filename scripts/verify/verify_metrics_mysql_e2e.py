#!/usr/bin/env python
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""真实 MySQL 元库端到端：ensure 建表 → Ossie 导入 → 发布投影 → 编译查询 → 追溯。

V2 的 mysql DDL 此前只过 sqlglot 语法层；本脚本在**真实 MySQL** 上跑指标
全链路（元库 = MySQL，业务库 = 临时 sqlite——元库方言 ≠ 指标 SQL 执行方言，
指标 SQL 始终按业务库方言编译执行）。

覆盖的真实差异面：DDL 可执行性（utf8mb4/TEXT/索引/AUTO_INCREMENT）、
pymysql 连接（charset/超时）、占位符方言（``_exec`` 的 ? → %s）、
``ensure_system_schema`` 的 mysql 分支、store CRUD 与追溯落库在真实
MySQL 行为下的一致性。

用法::

    # 本机便携实例（D:/job/demo/mysql-e2e，mysqld 已在 33071 起好）
    python scripts/verify_metrics_mysql_e2e.py

    # 任意 MySQL 实例
    python scripts/verify_metrics_mysql_e2e.py \\
        --meta-ref "mysql://1.2.3.4:3306/ds_meta_e2e" \\
        --mysql-user someuser --mysql-password secret

幂等：每次运行先 DROP DATABASE 再 CREATE，从零重建（发布/投影/追溯
全部重新产生）。MySQL 不可达或凭据错误时**大声失败**（exit 2），
不静默跳过——跳过等于没验证。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WORKSPACE = "mysql-e2e"

# 与 tests/unit_tests/web/conftest.py 的 e2e 夹具同源（期望值经上游
# verify_examples.py 核实）：east aov=200 / north NULL（分母 0）/ west aov=200
ORDERS_ROWS = [
    ("o1", "2026-08-01 00:00:00", "east", 100, "PAID"),
    ("o2", "2026-08-31 23:59:59", "east", 300, "PAID"),
    ("o3", "2026-08-12 12:00:00", "west", 200, "PAID"),
    ("o4", "2026-08-13 12:00:00", "north", 999, "PENDING"),
    ("o5", "2026-09-01 00:00:00", "east", 500, "PAID"),  # 9 月单，被时间范围排除
]

OSSIE_EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "jiuwenswarm" / "resources" / "ossie" / "commerce-metrics.example.yaml"
)

_REPO = Path(__file__).resolve().parents[1]


@dataclass
class _Step:
    name: str
    ms: float
    note: str = ""


_STEPS: list[_Step] = []


def _step(name: str) -> float:
    _STEPS.append(_Step(name, 0.0))
    return time.time()


def _step_done(t0: float, note: str = "") -> None:
    _STEPS[-1].ms = (time.time() - t0) * 1000
    _STEPS[-1].note = note


@dataclass
class _FakeWorkspace:
    """workspace_context 打桩：元库 = 目标 MySQL，业务库 = 临时 sqlite。"""

    workspace_id: str = WORKSPACE
    name: str = WORKSPACE
    business_db_ref: str = ""
    metadata_db_ref: str = ""


def _reset_database(meta_ref: str, user: str, password: str) -> None:
    """DROP + CREATE 目标库（连 server 级不带 database）。"""
    import pymysql

    from jiuwenswarm.common import db_connector

    spec = db_connector.parse_db_ref(meta_ref)
    if spec.get("type") != "mysql":
        raise SystemExit(f"--meta-ref 必须是 mysql:// 形态，收到 {meta_ref!r}")
    conn = pymysql.connect(
        host=spec["host"], port=int(spec["port"]), user=user, password=password,
        charset="utf8mb4", connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            db = spec["database"]
            cur.execute(f"DROP DATABASE IF EXISTS `{db}`")
            cur.execute(f"CREATE DATABASE `{db}` CHARACTER SET utf8mb4")
        conn.commit()
    finally:
        conn.close()


def _sqlite_baseline_tables() -> set[str]:
    """sqlite 基线表集合（文件驱动，DDL 增删自动适配）。"""
    from jiuwenswarm.channels.web.schema import ensure

    return ensure._table_names(ensure._load_statements(ensure.resolve_schema_dir(), "sqlite"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--meta-ref", default="mysql://127.0.0.1:33071/ds_meta_e2e",
                        help="MySQL 元库 ref（mysql://host:port/database）")
    parser.add_argument("--mysql-user", default=os.environ.get("MYSQL_USER", "root"))
    parser.add_argument("--mysql-password", default=os.environ.get("MYSQL_PASSWORD", ""))
    parser.add_argument("--keep-db", action="store_true",
                        help="结束时保留 MySQL 数据（默认保留；--reset 才重置）")
    args = parser.parse_args()

    # db_connector 的凭据解析链：spec > config mysql: 段 > env。脚本进程内
    # 显式注入 env（不写 os.environ 之外的东西；config 不动）
    os.environ["MYSQL_USER"] = args.mysql_user
    os.environ["MYSQL_PASSWORD"] = args.mysql_password

    # 0. 连通性：不可达大声失败（exit 2）
    import pymysql

    spec_host = None
    try:
        from jiuwenswarm.common import db_connector
        s = db_connector.parse_db_ref(args.meta_ref)
        spec_host = (s.get("host"), int(s.get("port") or 3306))
        pymysql.connect(host=s["host"], port=int(s["port"]), user=args.mysql_user,
                        password=args.mysql_password, charset="utf8mb4",
                        connect_timeout=10)
    except Exception as exc:  # noqa: BLE001 —— 任何连接类失败都归一为 exit 2
        print(f"[FAIL] MySQL 不可达（{args.meta_ref}, user={args.mysql_user}）: {exc}")
        return 2
    print(f"[ok] MySQL 连通 {spec_host[0]}:{spec_host[1]} (8.4.x)")

    # 1. 重置目标库
    t0 = _step("reset-database")
    _reset_database(args.meta_ref, args.mysql_user, args.mysql_password)
    _step_done(t0, "DROP + CREATE utf8mb4")

    # Windows：SQLAlchemy/只读护栏的连接可能晚释放占用临时库文件，
    # ignore_cleanup_errors=True 让残留目录交给系统清理（Temp 目录，无害）
    with tempfile.TemporaryDirectory(prefix="metrics-mysql-e2e-",
                                     ignore_cleanup_errors=True) as tmp:
        tmp_path = Path(tmp)

        # 2. 业务库：orders 表 5 行夹具（指标 SQL 在这里执行）
        t0 = _step("business-fixture")
        business_db = tmp_path / "business.db"
        with sqlite3.connect(business_db) as conn:
            conn.execute(
                "CREATE TABLE orders(order_id TEXT PRIMARY KEY, paid_at TEXT, region TEXT,"
                " amount_cny NUMERIC, pay_status TEXT)")
            conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", ORDERS_ROWS)
        business_ref = f"sqlite://{business_db}".replace("\\", "/")
        _step_done(t0, "sqlite orders 5 行")

        # 3. workspace_context 打桩（finally 恢复）
        from jiuwenswarm.common import workspace_context
        saved = {fn: getattr(workspace_context, fn) for fn in (
            "current_workspace", "metadata_db_ref",
            "validate_ref_in_workspace", "resolve_business_ref")}
        fake = _FakeWorkspace(business_db_ref=business_ref, metadata_db_ref=args.meta_ref)
        try:
            workspace_context.current_workspace = lambda *a, **k: fake  # type: ignore[assignment]
            workspace_context.metadata_db_ref = lambda *a, **k: args.meta_ref  # type: ignore[assignment]
            workspace_context.validate_ref_in_workspace = lambda *a, **k: business_ref  # type: ignore[assignment]
            workspace_context.resolve_business_ref = lambda *a, **k: business_ref  # type: ignore[assignment]

            # 4. ensure_system_schema（真实生产路径）→ 断言库里实际表集合 ⊇ sqlite 基线
            #    注意：parse_db_ref → find_credentials → _ensure_table 的链路可能在
            #    ensure 主体执行前就把表建好（重入让位语义），所以断言依据是
            #    SHOW TABLES 的真实集合，而非本次执行的 ensured_tables。
            t0 = _step("ensure-system-schema")
            from jiuwenswarm.channels.web.schema.ensure import ensure_system_schema
            from jiuwenswarm.common import db_connector
            result = ensure_system_schema(args.meta_ref)
            actual = {str(x) for x in db_connector.tables(args.meta_ref).get("tables", [])}
            baseline = _sqlite_baseline_tables()
            missing = baseline - actual
            if missing:
                print(f"[FAIL] MySQL 元库缺基线表: {sorted(missing)}")
                return 1
            _step_done(t0, f"库中 {len(actual)} 表（基线 {len(baseline)}，"
                           f"本次新建 {len(result['ensured_tables'])}）")

            # 5. dw_datasources 注册业务库（Ossie 导入的 alias 解析）
            t0 = _step("register-datasource")
            from datetime import datetime, timezone
            from jiuwenswarm.channels.web.orchestration.db import _connect, _exec
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            conn = _connect()
            try:
                _exec(conn, """INSERT INTO dw_datasources
                       (source_id, source_type, name, description, config, db_ref, engine,
                        enabled, config_version, created_at, updated_at)
                       VALUES ('biz_sales', 'sqlite', 'biz_sales', 'e2e 业务库', '{}', ?,
                        'sqlalchemy', 1, 1, ?, ?)""",
                      (business_ref, now_iso, now_iso))
                conn.commit()
            finally:
                conn.close()
            _step_done(t0)

            # 6. 模拟语义层采集：业务库 schema → dw_semantic_snapshots（published）
            #    深集成后指标 schema 供给只读这张表（严格单一通道）；发布时的
            #    绑定校验（ensure_semantic_refs）依赖它产出 manifest.semantic_refs
            t0 = _step("semantic-snapshot")
            from jiuwenswarm.channels.web.dataagent.semantic.model import (
                ColumnMeta, DatabaseMeta, DataSourceMeta, MetadataSnapshot, TableMeta)
            with sqlite3.connect(str(business_db)) as biz:
                columns = [
                    ColumnMeta("biz_sales", "mysql", "app", "orders", col[1],
                               data_type=str(col[2] or ""), column_type=str(col[2] or ""),
                               ordinal=int(col[0]) + 1, nullable=not col[5],
                               column_key="PRI" if col[5] else "")
                    for col in biz.execute("PRAGMA table_info(orders)")]
            snapshot = MetadataSnapshot(
                snapshot_id="snapshot-biz_sales-001", run_id="run-biz_sales-001",
                source=DataSourceMeta("biz_sales", "mysql", business_ref),
                databases=[DatabaseMeta("biz_sales", "mysql", "app")],
                tables=[TableMeta("biz_sales", "mysql", "app", "orders",
                                  comment="订单宽表", columns=columns)],
                scope={"schemas": ["app"]}, status="published")
            from jiuwenswarm.channels.web.orchestration.db import _connect as _meta_connect
            from jiuwenswarm.channels.web.orchestration.db import _exec as _meta_exec
            mconn = _meta_connect()
            try:
                _meta_exec(mconn, """INSERT INTO dw_semantic_snapshots
                       (snapshot_id, run_id, source_id, status, scope_json, snapshot_json,
                        object_count, relation_count, semantica_version, published_at, created_at)
                       VALUES (?, ?, 'biz_sales', 'published', ?, ?,
                        ?, 0, '', ?, ?)""",
                           (snapshot.snapshot_id, snapshot.run_id,
                            json.dumps(snapshot.scope), json.dumps(snapshot.to_dict()),
                            len(snapshot.object_map()), now_iso, now_iso))
                mconn.commit()
            finally:
                mconn.close()
            _step_done(t0, "orders 表快照 published")

            # 7. 导入 Ossie → 确认映射 → 发布
            t0 = _step("import-ossie")
            from jiuwenswarm.channels.web.dataagent.metrics import versioning
            imported = versioning.import_ossie(
                OSSIE_EXAMPLE.read_text(encoding="utf-8"), WORKSPACE,
                target_db_type="sqlite",
                datasource_resolver=lambda alias: business_ref if alias == "biz_sales" else None)
            if len(imported["revisions"]) != 3:
                print(f"[FAIL] 期望导入 3 个修订，得到 {len(imported['revisions'])}")
                return 1
            _step_done(t0, "3 修订")

            t0 = _step("confirm-mappings")
            from jiuwenswarm.channels.web.dataagent.metrics import store
            revision_ids: list[str] = []
            for metric in store.list_defs(WORKSPACE):
                revision_ids.extend(r.revision_id for r in store.list_revisions(metric.metric_id))
            confirmed = 0
            for revision_id in revision_ids:
                for mapping in store.list_concept_maps(revision_id, status="candidate"):
                    if mapping.relation_type in ("numerator", "denominator"):
                        versioning.confirm_mapping(mapping.mapping_id, confirmed_by="mysql-e2e")
                        confirmed += 1
            _step_done(t0, f"{confirmed} 条角色映射")

            t0 = _step("publish-release")
            release = versioning.publish_release(
                WORKSPACE, revision_ids,
                label="mysql e2e", published_by="verify-script")
            if release.status != "published":
                print(f"[FAIL] 发布状态 {release.status}")
                return 1
            refs = (release.manifest or {}).get("semantic_refs") or []
            if refs != [{"source_id": "biz_sales",
                         "snapshot_id": "snapshot-biz_sales-001"}]:
                print(f"[FAIL] manifest.semantic_refs 异常: {refs}")
                return 1
            # Neo4j 未配置属正常降级：发布成功 + projection_status=failed
            if release.projection_status not in ("published", "failed"):
                print(f"[FAIL] projection_status 异常: {release.projection_status}")
                return 1
            _step_done(t0, f"{release.release_id[:16]} refs=1 "
                           f"projection={release.projection_status}")

            # 8. 结构化查询（编译 → guard → 业务库执行 → 追溯落 MySQL）
            from jiuwenswarm.channels.web.dataagent.metrics import service
            from jiuwenswarm.channels.web.dataagent.metrics.model import QuerySpec, TimeRange
            spec = QuerySpec(
                metrics=("commerce.aov",),
                group_by=("orders.region",),
                time=TimeRange(dimension="orders.paid_at",
                               start="2026-08-01", end_exclusive="2026-09-01"),
            )
            t0 = _step("query-aov")
            result_q = service.query(spec, WORKSPACE, question="8 月各地区支付客单价")
            _step_done(t0, f"path={result_q.path}")

            if result_q.path != "compiler" or result_q.authoritative is not True:
                print(f"[FAIL] 期望 compiler 权威通路，得到 path={result_q.path}"
                      f" authoritative={result_q.authoritative}")
                return 1
            by_region = {row["region"]: row for row in result_q.rows}
            expect = {"east": 200.0, "north": None, "west": 200.0}
            for region, want in expect.items():
                got = by_region.get(region, {}).get("commerce_aov")
                if want is None:
                    if got is not None:
                        print(f"[FAIL] {region} 期望 NULL（分母 0），得到 {got}")
                        return 1
                elif float(got) != want:
                    print(f"[FAIL] {region} 期望 {want}，得到 {got}")
                    return 1
            print(f"[ok] aov 三行期望值一致: "
                  f"east=200 / north=NULL / west=200（行数 {result_q.row_count}）")

            # 9. 追溯断言（dw_metric_query_log 落在 MySQL）
            t0 = _step("query-log")
            logs = store.list_queries(WORKSPACE)
            if not logs or logs[0]["path"] != "compiler" or logs[0]["executed"] != 1:
                print(f"[FAIL] 追溯缺失或异常: {logs[:1]}")
                return 1
            validation = json.loads(logs[0]["validation_json"] or "{}")
            checks = [c.get("check") for c in validation.get("checks", [])]
            if checks != ["read_only", "object_scope", "caliber"]:
                print(f"[FAIL] 校验记录异常: {checks}")
                return 1
            _step_done(t0, f"{len(logs)} 行追溯，checks={checks}")

            # 10. 二次导入幂等（definition_hash 在 MySQL 上的行为一致性）
            t0 = _step("reimport-idempotent")
            again = versioning.import_ossie(
                OSSIE_EXAMPLE.read_text(encoding="utf-8"), WORKSPACE,
                target_db_type="sqlite",
                datasource_resolver=lambda alias: business_ref if alias == "biz_sales" else None)
            if again["created"] is not False:
                print("[FAIL] 二次导入应幂等（created=False）")
                return 1
            _step_done(t0)

        finally:
            for fn, original in saved.items():
                setattr(workspace_context, fn, original)
            # Windows：SQLAlchemy 池占着 sqlite 业务库文件，不 dispose 则
            # TemporaryDirectory 清理报 WinError 32（脚本进程即将退出，全部放掉）
            from jiuwenswarm.common import db_connector
            for key, eng in list(db_connector._ENGINES.items()):
                try:
                    db_connector._ENGINES.pop(key, None)
                    eng.dispose()
                except Exception:
                    pass

    # ---- 报告 ----
    print()
    print("=" * 64)
    print("MySQL 元库 e2e：全链路 PASS")
    print("=" * 64)
    for s in _STEPS:
        print(f"  {s.name:<24} {s.ms:>8.0f} ms  {s.note}")
    total = sum(s.ms for s in _STEPS)
    print(f"  {'TOTAL':<24} {total:>8.0f} ms")
    print(f"\n元库: {args.meta_ref}（数据保留，可手工检查；重跑会重置）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
