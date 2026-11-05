#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""dw_client.py —— skill 侧统一数据访问委托层（方案批次 2，替代直连 db_connector）。

连接层已上收服务端（jiuwenswarm.common.db_connector），skill 侧不再持有连接代码。
本模块**保留 db_connector 的函数签名（首参 db_ref）**，内部经 subprocess 调
`python -m jiuwenswarm.cli.dwcli` 走 web API——skill 脚本只需把

    import db_connector as dc

换成

    import dw_client as dc

即可完成迁移，其余调用点零改动。

不提供：_acquire/_cursor/executemany 等底层连接 API（落库走服务端 inspection
persist 端点，见 dwcli inspection persist/fix-persist）。
纯函数（parse_db_ref/quote_ident）委托 dw_sql_utils；config 解析保持本地
（datasource.yaml 文件读取，不读凭据段）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import dw_paths

# dw_sql_utils 同目录（纯函数，无连接能力）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dw_sql_utils import parse_db_ref as _pure_parse_db_ref  # noqa: E402
from dw_sql_utils import quote_ident as _pure_quote_ident  # noqa: E402

_DWCLI_TIMEOUT = int(os.environ.get("DWCLI_TIMEOUT_S", "600"))


# ==========================================================================
# dwcli 子进程调用
# ==========================================================================
# ==========================================================================
# workspace 上下文解析（多工作空间方案 P4-2 / 决策八-1）
# ==========================================================================
def resolve_workspace_id(explicit: str | None = None) -> str:
    """skill 侧 workspace 解析链：显式参数 > env JIUWENSWARM_WORKSPACE_ID > ""。

    返回 "" 表示未指定，由 dwcli/服务端按配置形态分流（单工作空间静默默认，
    多工作空间大声失败 WORKSPACE_CONTEXT_REQUIRED）。
    """
    wid = str(explicit or os.environ.get("JIUWENSWARM_WORKSPACE_ID") or "").strip()
    return wid


class WorkspaceContextError(RuntimeError):
    """workspace 上下文错误（透传服务端错误码）。

    code 可能取 WORKSPACE_CONTEXT_REQUIRED / WORKSPACE_CONTEXT_CONFLICT /
    DB_REF_NOT_ALLOWED / DUPLICATE_METADATA_DB_REF 等。
    """

    def __init__(self, message: str, code: str = "WORKSPACE_CONTEXT_ERROR"):
        super().__init__(message)
        self.code = code


def dwcli(*args: str, timeout: int | None = None) -> dict:
    """公开 CLI 通道（跨 skill 调用入口）：与 `_dwcli` 同一实现，供其它模块/脚本使用。"""
    return _dwcli(*args, timeout=timeout)


def _dwcli(*args: str, timeout: int | None = None) -> dict:
    """subprocess 调 dwcli，返回解析后的 JSON 载荷；业务失败抛 RuntimeError。

    连接类瞬时错误（web 服务重启窗口/Doris 连接建立中）自动重试 2 次：
    这些错误语义是"请求未到达服务端"，重试无副作用。5xx/业务错误不重试
    （可能已执行，重复执行有副作用风险），原样抛出大声失败。
    """
    cmd = [sys.executable or "python", "-m", "jiuwenswarm.cli.dwcli", *args]
    payload: dict = {}
    for attempt in range(3):
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout or _DWCLI_TIMEOUT)  # noqa: S603
        try:
            payload = json.loads(proc.stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            payload = {"status": "error", "error": f"dwcli 输出非 JSON: {proc.stdout[:200]!r}"}
        err_text = str(payload.get("error") or "").lower()
        transient = any(m in err_text for m in (
            "connection failed", "10061", "connection refused",
            "connectionreset", "connection reset", "temporarily unavailable",
        ))
        if transient and attempt < 2:
            time.sleep(1.0 * (attempt + 1))
            continue
        break
    # 只把工作空间类错误包成 WorkspaceContextError；其余错误必须走 _err_msg
    # 带出 detail——否则技能侧只能看到 "not_found" / "internal_error" 这类光秃秃
    # 的错误名，hint 指向「查数据库日志」，真实原因全丢。
    code = payload.get("error_code") or payload.get("error")
    if code in (
            "WORKSPACE_CONTEXT_REQUIRED", "WORKSPACE_CONTEXT_CONFLICT",
            "DB_REF_NOT_ALLOWED", "DUPLICATE_METADATA_DB_REF"):
        raise WorkspaceContextError(_err_msg(payload), code)
    if payload.get("status") == "error":
        raise RuntimeError(_err_msg(payload))
    if payload.get("http_code", 0) >= 400:
        raise RuntimeError(_err_msg(payload))
    if payload.get("ok") is False:
        raise RuntimeError(_err_msg(payload))
    return payload


def _err_msg(payload: dict) -> str:
    """组装可定位的错误消息：error + detail/reason 一起带出（防只留 internal_error）。"""
    err = str(payload.get("error") or payload.get("reason") or "dwcli 调用失败")
    detail = payload.get("detail") or payload.get("stderr") or ""
    detail = str(detail).strip()
    if detail and detail != err:
        return f"{err}: {detail[:300]}"
    return err


def _datasource_config_path() -> Path:
    home = dw_paths.workspace_root()
    for cand in (home / "config" / "datasource.yaml", home / "datasource.yaml"):
        if cand.is_file():
            return cand
    return home / "config" / "datasource.yaml"


_DATASOURCE_CONFIG = _datasource_config_path()


def _read_config_key(key: str) -> str | None:
    """读 config 顶层 key（default_db/metadata_db），轻量行解析。

    每次调用重新取 _datasource_config_path()（支持测试注入与运行时配置变更），
    模块级 _DATASOURCE_CONFIG 仅作历史位兼容回退。
    """
    cfg = _datasource_config_path()
    if not cfg.is_file():
        cfg = _DATASOURCE_CONFIG
    if not cfg.is_file():
        return None
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].rstrip()
            m = re.match(rf"^{key}\s*:\s*(.+)$", line.strip())
            if m:
                return m.group(1).strip().strip('"').strip("'")
    except OSError:
        pass
    return None


# ==========================================================================
# config 顶层 key（本地文件读取，不 subprocess）
# ==========================================================================
def default_db_ref(workspace_id: str | None = None) -> str | None:
    """读 datasource.yaml 的 default_db（业务库）。

    多工作空间（P4-2）：传 workspace_id 或设置 env JIUWENSWARM_WORKSPACE_ID
    时经 dwcli datasource current 走 workspace_context 解析；多工作空间缺
    上下文时 dwcli 大声失败（WORKSPACE_CONTEXT_REQUIRED），不静默落顶层键。
    """
    wid = resolve_workspace_id(workspace_id)
    if wid or os.environ.get("JIUWENSWARM_WORKSPACE_ID", "").strip():
        try:
            payload = _dwcli("datasource", "current", "--workspace-id", wid)
        except WorkspaceContextError:
            raise
        return payload.get("business_db_ref") or _read_config_key("default_db")
    return _read_config_key("default_db")


def metadata_db_ref(workspace_id: str | None = None) -> str | None:
    """读 datasource.yaml 的 metadata_db（系统元数据库）。

    多工作空间语义同 default_db_ref。
    """
    wid = resolve_workspace_id(workspace_id)
    if wid or os.environ.get("JIUWENSWARM_WORKSPACE_ID", "").strip():
        try:
            payload = _dwcli("datasource", "current", "--workspace-id", wid)
        except WorkspaceContextError:
            raise
        return payload.get("metadata_db_ref") or _read_config_key("metadata_db")
    return _read_config_key("metadata_db")


def _read_config_section_key(section: str, key: str) -> str | None:
    """读 config 的 doris:/mysql: 段内子键（如 database/metadata_database）。

    database 名不是凭据，skill 侧可读；凭据（host/user/password）不读。
    简单 YAML 缩进解析，不引 PyYAML。section 缺失返回 None。
    """
    cfg = _datasource_config_path()
    if not cfg.is_file():
        cfg = _DATASOURCE_CONFIG
    if not cfg.is_file():
        return None
    in_section = False
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            raw = line.split("#", 1)[0].rstrip()
            if not raw.strip():
                continue
            stripped = raw.lstrip()
            indent = len(raw) - len(stripped)
            if indent == 0:
                in_section = stripped.startswith(f"{section}:")
                continue
            if not in_section:
                continue
            m = re.match(r"^(\w+)\s*:\s*(.+)$", stripped)
            if m and m.group(1) == key:
                return m.group(2).strip().strip("'").strip('"')
    except OSError:
        pass
    return None


# ==========================================================================
# 纯函数委托（dw_sql_utils）
# ==========================================================================
def parse_db_ref(db_ref: str) -> dict:
    """db-ref 结构解析（不读凭据、不连库）。

    与纯函数版差异：doris://mysql:// 省略库名时，读 config 对应段的 database
    子键补全（database 名非凭据，skill 侧可读）——供 information_schema 查询
    拼 table_schema 用。凭据（host/user/password）仍不读。
    """
    spec = _pure_parse_db_ref(db_ref)
    # 省略库名的 doris:// / mysql:// → 读 config 段补全 database
    if spec.get("type") in ("doris", "mysql") and not spec.get("database"):
        scheme = spec["type"]
        if spec.get("is_metadata"):
            db = _read_config_section_key(scheme, "metadata_database")
        else:
            db = _read_config_section_key(scheme, "database")
        if db:
            spec["database"] = db
    return spec


def quote_ident(db_ref: str, name: str) -> str:
    """按库类型给标识符加引号（纯函数）。"""
    return _pure_quote_ident(db_ref, name)


# ==========================================================================
# 数据访问（经 dwcli，保留 db_connector 签名）
# ==========================================================================
def query(db_ref: str, sql: str, limit: int | None = 10000, role: str = "business") -> dict:
    """只读查询 → {row_count, columns, rows, truncated, elapsed_ms, db}（db_connector 兼容 shape）。

    注意：**不支持 params**——dwcli 无 params 通道，SQL 一律字面拼（占位符会原样
    发给服务端导致执行报错）。需要参数化查询时在调用方 f-string 拼值。
    """
    args = ["sql", "run", "--db-ref", db_ref, "--sql", sql, "--role", role]
    if limit is not None:
        args += ["--limit", str(limit)]
    payload = _dwcli(*args)
    return {
        "row_count": payload.get("row_count", 0),
        "truncated": payload.get("truncated", False),
        "columns": payload.get("columns", []),
        "rows": payload.get("rows", []),
        "elapsed_ms": payload.get("elapsed_ms", 0),
        "db": {"type": None, "ref": db_ref},
    }


def query_dict(db_ref: str, sql: str, limit: int | None = 10000, role: str = "business") -> list[dict]:
    """只读查询，返回 list[dict]（列名: 值）。不支持 params（同 query）。"""
    result = query(db_ref, sql, limit=limit, role=role)
    return [dict(zip(result["columns"], row)) for row in result["rows"]]


def tables(db_ref: str, role: str = "business") -> dict:
    """表清单。经 dwcli databases tables（db 名从 db_ref 解析）。

    API 返回 DAO 格式 dict 列表（name/row_count/...）；db_connector 原契约是
    表名字符串列表（各 skill 调用方 set()/lower()/startswith() 均按字符串处理），
    此处归一化回字符串列表，保持 db_connector 签名兼容。
    metadata role 经 sql run（SQLite 用 sqlite_master，MySQL/Doris 用 SHOW TABLES）。
    """
    spec = parse_db_ref(db_ref)
    if role == "metadata":
        if spec.get("type") == "sqlite":
            sql = ("SELECT name FROM sqlite_master WHERE type='table' "
                   "AND name NOT LIKE 'sqlite_%' ORDER BY name")
        else:
            sql = "SHOW TABLES"
        payload = _dwcli("sql", "run", "--db-ref", db_ref,
                         "--role", "metadata", "--sql",
                         sql, "--limit", "10000")
        rows = payload.get("rows", [])
        columns = payload.get("columns", [])
        names = [str(row[0]) for row in rows if row]
        if columns and not names:
            name_index = next(
                (i for i, c in enumerate(columns)
                 if str(c).lower() in {"tables_in_" + str(spec.get("database", "")).lower(), "table_name", "name"}),
                0)
            names = [str(row[name_index]) for row in rows if len(row) > name_index]
        return {"tables": names, "table_count": len(names), "db": {"type": spec.get("type"), "ref": db_ref}}
    db_name = spec.get("database") or ""
    payload = _dwcli("databases", "tables", "--db", db_name, "--db-ref", db_ref)
    raw = payload.get("tables", [])
    names = [t if isinstance(t, str) else str(t.get("name") or t.get("table_name") or "")
             for t in raw if isinstance(t, (str, dict))]
    return {"tables": names, "table_count": payload.get("table_count", len(names)),
            "db": {"type": spec.get("type"), "ref": db_ref}}


def schema(db_ref: str, table: str, role: str = "business") -> dict:
    """表结构。经 dwcli databases table。

    ``resolved_table`` 是服务端目录里的规范表名：带 schema 的目录（DuckDB
    原生库 / SQL Server）接受裸表名后返回全限定名，调用方据此拼后续 SQL。
    """
    spec = parse_db_ref(db_ref)
    db_name = spec.get("database") or ""
    payload = _dwcli("databases", "table", "--db", db_name, "--table", table,
                     "--db-ref", db_ref)
    return {"table": table,
            "resolved_table": payload.get("name") or table,
            "columns": payload.get("columns", payload.get("fields", [])),
            "db": {"type": spec.get("type"), "ref": db_ref}}


def list_indexes(db_ref: str, table: str) -> dict:
    """表索引。经 sql run 查询方言化信息架构（SHOW INDEX / PRAGMA）。

    duckdb 文件源是运行时注册的 VIEW，无索引语义——返回空列表（对齐服务端
    db_connector.list_indexes 的 duckdb 分支，勿向 DuckDB 发 SHOW INDEX）。
    """
    spec = parse_db_ref(db_ref)
    if spec["type"] == "duckdb":
        return {"table": table, "indexes": [], "db": {"type": "duckdb", "ref": db_ref}}
    if spec["type"] == "sqlite":
        sql = f'PRAGMA index_list("{table}")'
    else:
        sql = f"SHOW INDEX FROM `{table}`"
    result = query(db_ref, sql, limit=1000, role="business")
    return {"table": table, "indexes": result["rows"], "db": result["db"]}


def show_create_table(db_ref: str, table: str) -> dict:
    """表 CREATE 语句。经 sql run（SHOW CREATE TABLE / sqlite_master）。

    duckdb 文件源的 VIEW 由运行时注册，无持久 DDL——返回空串（对齐服务端
    db_connector.show_create_table 的 duckdb 分支，勿向 DuckDB 发 SHOW CREATE）。
    """
    spec = parse_db_ref(db_ref)
    if spec["type"] == "duckdb":
        return {"table": table, "create_sql": "", "db": {"type": "duckdb", "ref": db_ref}}
    if spec["type"] == "sqlite":
        sql = f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'"
    else:
        sql = f"SHOW CREATE TABLE `{table}`"
    result = query(db_ref, sql, limit=1, role="business")
    create = result["rows"][0][-1] if result["rows"] else ""
    return {"table": table, "create_sql": create or "", "db": result["db"]}


def execute(db_ref: str, sql: str, params=None, commit: bool = True,
            role: str = "business") -> dict:
    """DDL/DML 写操作 → dwcli sql ddl --confirm → {affected_rows, elapsed_ms, db}。

    role 默认 business（业务库写）；写元库 dw_* 表（如规则 save/toggle）须显式
    role="metadata"，否则元库 ref 过不了服务端 business role 校验（多工作空间 400）。
    """
    payload = _dwcli("sql", "ddl", "--db-ref", db_ref, "--sql", sql, "--confirm",
                     "--role", role)
    return {"affected_rows": payload.get("affected_rows"),
            "elapsed_ms": payload.get("elapsed_ms", 0),
            "db": {"type": None, "ref": db_ref}}


# ==========================================================================
# 语义增强通道（一期接线：指标召回术语扩展 + nl2sql 表混合召回）
# ==========================================================================
def glossary_resolve(query_text: str, limit: int = 3) -> dict:
    """术语库三级召回（精确别名/向量/LIKE）→ 服务端 resolve 原样返回。

    Returns:
        ``{"query", "degraded", "matches": [...], "warnings": [...]}``——
        matches 元素含 ``term``（name/synonyms_list/definition）与 ``score``/
        ``match_type``。服务不可达时抛 RuntimeError，调用方自行降级（术语
        扩展是召回增益不是依赖）。
    """
    return _dwcli("glossary", "resolve", "--q", query_text, "--limit", str(limit))


def semantic_vector_recall(db_ref: str, query_text: str, limit: int = 20) -> dict:
    """schema 向量表/列语义召回（服务端 FAISS，按 db_ref 反查注册源）。

    Returns:
        ``{"tables": [...], "query_vectors": [...], "warnings": [...]}``——
        服务端降级契约：通道关闭/未绑定返回空 tables + warning，**不是**错误。
        服务不可达时抛 RuntimeError，调用方降级词法召回。
    """
    return _dwcli("semantic", "vector-recall", "--db-ref", db_ref,
                  "--q", query_text, "--limit", str(limit))
