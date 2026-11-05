#!/usr/bin/env python
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""深集成端到端：MySQL 业务源采集 → 指标发布投影 → 语义召回 → 编译查询。

覆盖「指标模块 × 语义层数据面」的完整集成面（区别于
verify_metrics_mysql_e2e.py 只验证元库方言）：

1. 便携 MySQL 上建**业务库**（同事 MySQLMetadataAdapter 只支持 mysql 源，
   采集走真实 information_schema）；
2. 注册 datasource → ``semantic.service.start_run`` **同步真实采集**
   （collect → 指纹 diff → dw_semantic_snapshots published）；
3. Ossie 导入 → 确认 → 发布：``ensure_semantic_refs`` 读真实采集快照校验绑定、
   manifest 钉 semantic_refs、graph_projection 投影 Metric 节点 + 跨 namespace 边；
4. ask 召回（schema 通道走语义层快照）→ compiler 编译 → query 执行 → 数值断言。

两种运行模式：

- **默认（无 ``SEMANTICA_URI``）**：采集与投影的图适配器注入
  ``InMemorySemanticaAdapter``——除图存储外全链路真实（information_schema、
  指纹、落库、召回、编译、执行），跨 namespace 边经包装替身断言直写参数。
- **完整 Neo4j（配置 ``SEMANTICA_URI/USERNAME/PASSWORD``）**：图侧走真实
  Neo4j 三 namespace（physical / metrics）与跨边直写；连通检查复用
  ``scripts/verify_semantica.py``。

幂等：每次运行 DROP 并重建元库与业务库。任何一步失败**大声退出**（exit 1），
不静默跳过。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WORKSPACE = "semantic-e2e"
SOURCE_ID = "biz_orders_src"
BUSINESS_DB = "biz_orders_e2e"
META_DB = "ds_semantic_e2e"

ORDERS_ROWS = [
    ("o1", "2026-08-01 00:00:00", "east", 100, "PAID"),
    ("o2", "2026-08-31 23:59:59", "east", 300, "PAID"),
    ("o3", "2026-08-12 12:00:00", "west", 200, "PAID"),
    ("o4", "2026-08-13 12:00:00", "north", 999, "PENDING"),
    ("o5", "2026-09-01 00:00:00", "east", 500, "PAID"),
]

OSSIE_EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "jiuwenswarm" / "resources" / "ossie" / "commerce-metrics.example.yaml"
)


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


def _fail(message: str) -> int:
    print(f"[FAIL] {message}")
    return 1


def _mysql_exec(meta_ref: str, user: str, password: str, sql: str) -> None:
    import pymysql

    host, port = _host_port(meta_ref)
    conn = pymysql.connect(host=host, port=port, user=user, password=password,
                           charset="utf8mb4", autocommit=True, connect_timeout=10)
    try:
        with conn.cursor() as cursor:
            for statement in [s.strip() for s in sql.split(";") if s.strip()]:
                cursor.execute(statement)
    finally:
        conn.close()


def _host_port(ref: str) -> tuple[str, int]:
    import urllib.parse

    parsed = urllib.parse.urlparse(ref)
    return parsed.hostname or "127.0.0.1", parsed.port or 3306


def _create_business_db(meta_ref: str, user: str, password: str) -> str:
    """在便携实例上建业务库（orders 表 + 5 行夹具）→ 返回业务 db_ref。"""
    _mysql_exec(meta_ref, user, password, f"DROP DATABASE IF EXISTS {BUSINESS_DB}")
    values = ", ".join(
        f"('{r[0]}','{r[1]}','{r[2]}',{r[3]},'{r[4]}')" for r in ORDERS_ROWS)
    _mysql_exec(
        meta_ref, user, password,
        f"CREATE DATABASE {BUSINESS_DB} CHARACTER SET utf8mb4;"
        f"CREATE TABLE {BUSINESS_DB}.orders ("
        "  order_id VARCHAR(32) PRIMARY KEY,"
        "  paid_at DATETIME,"
        "  region VARCHAR(16),"
        "  amount_cny DECIMAL(12,2),"
        "  pay_status VARCHAR(16)"
        ") ;"
        f"INSERT INTO {BUSINESS_DB}.orders VALUES {values}")
    host, port = _host_port(meta_ref)
    # 去凭据形态（红线）：user/password 存 dw_datasources.config，
    # 连接层经 find_credentials 按四元组补齐
    return f"mysql://{host}:{port}/{BUSINESS_DB}"


@dataclass
class _FakeWorkspace:
    workspace_id: str = WORKSPACE
    name: str = WORKSPACE
    business_db_ref: str = ""
    metadata_db_ref: str = ""


class _CrossRecordingAdapter:
    """InMemory 包装：记录 create_cross_edges 调用（跨 namespace 边断言用）。"""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.cross_calls: list[tuple[str, list]] = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def create_cross_edges(self, namespace: str, edges) -> dict:
        self.cross_calls.append((namespace, list(edges)))
        return {"created": len(edges), "missed": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description="深集成（指标 × 语义层）端到端验证")
    parser.add_argument("--meta-ref", default="mysql://127.0.0.1:33071/" + META_DB)
    parser.add_argument("--mysql-user", default="root")
    parser.add_argument("--mysql-password", default=os.environ.get("MYSQL_PASSWORD", ""))
    args = parser.parse_args()

    neo4j_mode = bool(os.environ.get("SEMANTICA_URI", "").strip())
    print(f"[mode] 图侧：{'真实 Neo4j（SEMANTICA_URI 已配置）' if neo4j_mode else 'InMemory 替身（配置 SEMANTICA_URI 切真实 Neo4j）'}")

    host, port = _host_port(args.meta_ref)
    probe = socket.socket()
    probe.settimeout(2)
    if probe.connect_ex((host, port)) != 0:
        probe.close()
        return _fail(f"MySQL 不可达 {host}:{port}（本机便携实例："
                     "D:/job/demo/mysql-e2e，mysqld --port=33071）")
    probe.close()

    # 元库连接凭据：spec 无凭据时连接层回退 MYSQL_USER/MYSQL_PASSWORD env
    # （与 V9 的 verify_metrics_mysql_e2e 同源实测约定）
    os.environ.setdefault("MYSQL_USER", args.mysql_user)
    os.environ["MYSQL_PASSWORD"] = args.mysql_password

    from jiuwenswarm.channels.web.schema.ensure import ensure_system_schema

    started = time.time()
    print(f"[ok] MySQL 连通 {host}:{port}")

    # 1. 重置元库 + 建业务库（同一实例）
    t0 = _step("reset-databases")
    _mysql_exec(args.meta_ref, args.mysql_user, args.mysql_password,
                f"DROP DATABASE IF EXISTS {META_DB};"
                f"CREATE DATABASE {META_DB} CHARACTER SET utf8mb4")
    business_ref = _create_business_db(args.meta_ref, args.mysql_user, args.mysql_password)
    _step_done(t0, "元库 + 业务库重建")

    # 2. workspace_context 打桩（finally 恢复）——深集成链路里同事采集器的
    #    _execute 会经 set_current→resolve 安装上下文，一并打桩
    from jiuwenswarm.common import workspace_context

    saved = {fn: getattr(workspace_context, fn) for fn in (
        "current_workspace", "metadata_db_ref",
        "validate_ref_in_workspace", "resolve_business_ref",
        "resolve", "set_current", "reset")}
    fake = _FakeWorkspace(business_db_ref=business_ref, metadata_db_ref=args.meta_ref)
    workspace_context.current_workspace = lambda *a, **k: fake  # type: ignore[assignment]
    workspace_context.metadata_db_ref = lambda *a, **k: args.meta_ref  # type: ignore[assignment]
    workspace_context.validate_ref_in_workspace = lambda *a, **k: business_ref  # type: ignore[assignment]
    workspace_context.resolve_business_ref = lambda *a, **k: business_ref  # type: ignore[assignment]
    workspace_context.resolve = lambda *a, **k: fake  # type: ignore[assignment]
    workspace_context.set_current = lambda workspace_id: ("stub-token", workspace_id)  # type: ignore[assignment]
    workspace_context.reset = lambda token: None  # type: ignore[assignment]

    try:
        # 3. ensure 建元库表（真实生产路径）
        t0 = _step("ensure-system-schema")
        ensure_system_schema(args.meta_ref)
        _step_done(t0)

        # 4. 注册 mysql 数据源（dw_datasources——采集与绑定解析的锚点）
        t0 = _step("register-datasource")
        from datetime import datetime, timezone

        from jiuwenswarm.channels.web.orchestration.db import _connect, _exec

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        source_config = json.dumps({"user": args.mysql_user,
                                    "password": args.mysql_password})
        conn = _connect()
        try:
            _exec(conn, """INSERT INTO dw_datasources
                   (source_id, source_type, name, description, config, db_ref, engine,
                    enabled, config_version, created_at, updated_at)
                   VALUES (?, 'mysql', ?, '深集成 e2e 业务源', ?, ?,
                    'sqlalchemy', 1, 1, ?, ?)""",
                  (SOURCE_ID, SOURCE_ID, source_config, business_ref, now_iso, now_iso))
            conn.commit()
        finally:
            conn.close()
        _step_done(t0, f"source={SOURCE_ID}")

        # 5. 真实语义采集（同步）：information_schema → 指纹 → published 快照
        t0 = _step("semantic-collect")
        from jiuwenswarm.channels.web.dataagent.semantic import repository, service
        from jiuwenswarm.channels.web.dataagent.semantic.semantica import (
            InMemorySemanticaAdapter,
        )

        collect_adapter = None if neo4j_mode else InMemorySemanticaAdapter()
        run = service.start_run(
            {"source_id": SOURCE_ID, "schemas": [BUSINESS_DB],
             "include_profile": False},
            adapter=collect_adapter, synchronous=True)
        if run.get("status") != "published":
            return _fail(f"采集 run 状态 {run.get('status')}: {run.get('error_json')}")
        snapshot = repository.latest_published_snapshot(SOURCE_ID)
        if snapshot is None or not any(t.name == "orders" for t in snapshot.tables):
            return _fail(f"published 快照缺 orders 表: "
                         f"{[t.name for t in (snapshot.tables if snapshot else [])]}")
        _step_done(t0, f"snapshot={snapshot.snapshot_id} "
                       f"tables={len(snapshot.tables)} cols={sum(len(t.columns) for t in snapshot.tables)}")

        # 6. Ossie 导入 → 确认 ratio 角色
        t0 = _step("import-ossie")
        from jiuwenswarm.channels.web.dataagent.metrics import store, versioning

        imported = versioning.import_ossie(
            OSSIE_EXAMPLE.read_text(encoding="utf-8"), WORKSPACE,
            target_db_type="mysql",
            datasource_resolver=lambda alias: business_ref if alias == "biz_sales" else None)
        if len(imported["revisions"]) != 3:
            return _fail(f"期望 3 修订，得到 {len(imported['revisions'])}")
        revision_ids: list[str] = []
        for metric in store.list_defs(WORKSPACE):
            revision_ids.extend(r.revision_id for r in store.list_revisions(metric.metric_id))
        confirmed = 0
        for revision_id in revision_ids:
            for mapping in store.list_concept_maps(revision_id, status="candidate"):
                if mapping.relation_type in ("numerator", "denominator"):
                    versioning.confirm_mapping(mapping.mapping_id, confirmed_by="semantic-e2e")
                    confirmed += 1
        _step_done(t0, f"3 修订 / {confirmed} 角色映射")

        # 7. 发布：ensure_semantic_refs 校验真实快照 + Neo4j/InMemory 投影
        t0 = _step("publish-release")
        graph_adapter = None if neo4j_mode else _CrossRecordingAdapter(InMemorySemanticaAdapter())
        release = versioning.publish_release(
            WORKSPACE, revision_ids, graph_adapter=graph_adapter,
            label="semantic e2e", published_by="verify-script")
        if release.status != "published":
            return _fail(f"发布状态 {release.status}")
        refs = (release.manifest or {}).get("semantic_refs") or []
        if not refs or refs[0]["source_id"] != SOURCE_ID or \
                refs[0]["snapshot_id"] != snapshot.snapshot_id:
            return _fail(f"manifest.semantic_refs 与真实采集不符: {refs}")
        if refs[0]["snapshot_id"] != snapshot.snapshot_id:
            return _fail("refs 钉的快照不是最新 published")
        if release.projection_status != "published":
            return _fail(f"projection_status={release.projection_status} "
                         f"error={release.error_message}")
        # 绑定必须经真实快照校验为 verified
        for revision_id in revision_ids:
            for binding in store.list_bindings(revision_id):
                if binding.source_table and binding.verify_status != "verified":
                    return _fail(f"绑定 {binding.binding_id} 状态 {binding.verify_status}"
                                 f" ({binding.verify_detail})")
        _step_done(t0, f"{release.release_id[:16]} refs=[{SOURCE_ID}] projection=published")

        # 8. 跨 namespace 边（InMemory 模式断言直写参数；Neo4j 模式由图侧承载）
        if not neo4j_mode:
            cross_calls = graph_adapter.cross_calls  # type: ignore[union-attr]
            if not cross_calls:
                return _fail("跨 namespace 边未直写（create_cross_edges 未被调用）")
            _ns, edges = cross_calls[0]
            kinds = {e.type for e in edges}
            if not {"usesTable", "timeField"} <= kinds:
                return _fail(f"跨边类型缺失: {sorted(kinds)}")
            targets = [e.target for e in edges]
            if not any(t.startswith(f"tbl:mysql:{SOURCE_ID}:") for t in targets):
                return _fail(f"跨边目标不是 physical 稳定 ID: {targets[:4]}")
            t0b = time.time()
            _STEPS.append(_Step("cross-edges", 0.0,
                                f"{len(edges)} 条（{','.join(sorted(kinds))}）"))
            _step_done(t0b)

        # 9. ask 语义召回 → compiler；query 二步执行 → 数值
        t0 = _step("ask-compiler")
        from jiuwenswarm.channels.web.dataagent.metrics import llm as metrics_llm
        from jiuwenswarm.channels.web.dataagent.metrics import service as metric_service
        from jiuwenswarm.channels.web.dataagent.metrics.model import QuerySpec

        spec_json = json.dumps({
            "spec": {"metrics": ["commerce.aov"], "group_by": ["orders.region"],
                     "time": {"dimension": "orders.paid_at", "start": "2026-08-01",
                              "end_exclusive": "2026-09-01"}},
            "clarifications": []})
        metrics_llm.call_llm_sync = lambda *a, **k: spec_json  # type: ignore[assignment]
        answer = metric_service.ask("8月各地区支付客单价", WORKSPACE)
        if answer.route != "compiler" or answer.answer is None:
            return _fail(f"ask 路由 {answer.route}（warnings={answer.catalog_warnings}）")
        executed = metric_service.query(
            QuerySpec.from_dict(answer.answer.spec), WORKSPACE)
        if executed.path != "compiler" or executed.authoritative is not True:
            return _fail(f"query path={executed.path} authoritative={executed.authoritative}")
        by_region = {row["region"]: row for row in executed.rows}
        expect = {"east": 200.0, "north": None, "west": 200.0}
        for region, want in expect.items():
            got = by_region.get(region, {}).get("commerce_aov")
            if want is None:
                if got is not None:
                    return _fail(f"{region} 期望 NULL，得到 {got}")
            elif float(got) != want:
                return _fail(f"{region} 期望 {want}，得到 {got}")
        _step_done(t0, f"path=compiler rows={executed.row_count} east=200/north=NULL/west=200")

        # 10. R2 schema 通道：列名词汇经真实快照传导到指标
        t0 = _step("schema-recall-r2")
        from jiuwenswarm.channels.web.dataagent.metrics import semantic_link

        _r, snapshots = semantic_link.current_snapshots()
        hits, confidence = semantic_link.schema_recall(
            WORKSPACE, "orders 表 amount_cny 按 region 汇总", snapshots, warnings=[])
        if not any(h.logical == "orders" for h in hits) and \
                not any(h.logical and h.logical.startswith("orders.") for h in hits):
            return _fail(f"schema 通道未命中 orders 对象: {[h.logical for h in hits]}")
        propagated = semantic_link.propagate_to_metrics(
            WORKSPACE, store.get_active_release(WORKSPACE), snapshots, hits)
        if "commerce.gmv" not in propagated:
            return _fail(f"amount_cny 未传导到 GMV: {propagated}")
        _step_done(t0, f"schema_conf={confidence:.2f} gmv_propagated={propagated.get('commerce.gmv', 0):.2f}")

        # 11. 追溯
        t0 = _step("query-log")
        logs = store.list_queries(WORKSPACE)
        if not logs or not any(log.get("executed") == 1 for log in logs):
            return _fail(f"追溯缺失: {logs[:1]}")
        _step_done(t0, f"{len(logs)} 行")

        elapsed = time.time() - started
        print()
        print("=" * 64)
        print(f"深集成 e2e：全链路 PASS（{'Neo4j' if neo4j_mode else 'InMemory 图替身'}）")
        print("=" * 64)
        for step in _STEPS:
            print(f"  {step.name:<26} {step.ms:7.0f} ms  {step.note}")
        print(f"  {'TOTAL':<26} {elapsed * 1000:7.0f} ms")
        print()
        print(f"元库: {args.meta_ref} / 业务库: {business_ref.split('@')[-1]}"
              f"（数据保留可手工检查；重跑会重置）")
        return 0
    finally:
        for fn, original in saved.items():
            setattr(workspace_context, fn, original)


if __name__ == "__main__":
    sys.exit(main())
