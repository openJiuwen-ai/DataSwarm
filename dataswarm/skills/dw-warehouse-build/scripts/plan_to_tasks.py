#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""plan_to_tasks.py —— 数仓任务化桥接（dw-warehouse-build）：plan → 编排任务 + 分层 DAG 工作流。

核心思路：
    把 plan 里的建表 DDL、CSV 导入、ETL 全部变成编排任务，
    组成一个 ODS→DWD→DWS→ADS 分层 DAG 工作流，publish 后同步 debug 执行。
    血缘随 publish（构建时，plan_id=task_id）+ debug 执行（运行时，plan_id=instance_id）
    自动落库（bigdata.table_lineage / column_lineage），与 [[unify-lineage-task-driven]] 一致。

任务形态（每张表 2 个任务）：
    ODS 表（9 张）：<prefix>_ddl_<short>（SQL，CREATE TABLE）+ <prefix>_imp_<short>（Python，CSV 导入）
    DWD/DWS/ADS 表（13 张）：<prefix>_ddl_<short>（SQL，CREATE TABLE）+ <prefix>_etl_<short>（SQL，INSERT...SELECT）

工作流 DAG（分层依赖）：
    ODS：ddl_A → imp_A
    分层表：ddl_B → etl_B；etl_B 的 upstream 追加其 source_tables 里已有任务的节点
        （DWD 源 ODS → imp 节点；DWS 源 DWD → etl 节点；ADS 源 DWS → etl 节点）
    天然形成 ODS→DWD→DWS→ADS 分层依赖。

用法:
    python plan_to_tasks.py --plan-id <plan_id>                  # 建任务+工作流+publish+同步执行
    python plan_to_tasks.py --plan-id <plan_id> --no-run          # 只建不执行
    python plan_to_tasks.py --plan-id <plan_id> --no-publish      # 建任务+工作流，不 publish 不执行
    python plan_to_tasks.py --plan-id <plan_id> --clean           # 清演示任务/工作流/血缘
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile


# 项目根（DataSwarm）——从 skill scripts 目录向上 8 级：
# scripts→dw-warehouse-build→skills→workspace→agent→resources→jiuwenswarm→DataSwarm
# runtime 路径陷阱（P4-4）：包安装/runtime 布局下 8 级向上可能不存在，逐级探测
# 含 jiuwenswarm 包的真实项目根；找不到时退回原值（不影响纯 config 驱动路径）。
def _resolve_project_root() -> Path:
    p = Path(__file__).resolve()
    for _ in range(10):
        if (p / "jiuwenswarm" / "__init__.py").is_file():
            return p
        p = p.parent
    return Path(__file__).resolve().parent.parent.parent.parent.parent.parent.parent.parent


_PROJECT_ROOT = _resolve_project_root()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# _dw_common/scripts——优先从本 skill 同级目录解析（dev 与 runtime 通用）；
# 缺失时回退项目根 resources 路径。
_DW_COMMON_DIR = Path(__file__).resolve().parent.parent.parent / "_dw_common" / "scripts"
if _DW_COMMON_DIR.is_dir():
    if str(_DW_COMMON_DIR) not in sys.path:
        sys.path.insert(0, str(_DW_COMMON_DIR))
else:
    _DW_COMMON = (
        _PROJECT_ROOT
        / "jiuwenswarm"
        / "resources"
        / "agent"
        / "workspace"
        / "skills"
        / "_dw_common"
        / "scripts"
    )
    if str(_DW_COMMON) not in sys.path:
        sys.path.insert(0, str(_DW_COMMON))

import dw_type_map  # noqa: E402  # 语义类型映射唯一真相源（批次 1b G3）
import dw_paths  # noqa: E402
import dw_aggregate_check  # noqa: E402  # GROUP BY 聚合校验唯一真相源（批次 1b G8）


# 业务库 db-ref。默认读 datasource.yaml 的 default_db（本地开发可为 sqlite://...），
# 未配置时回退 doris://（4 层表所在）。具体库名从 plan.biz_database 读取。
def _datasource_config_path() -> Path:
    """数据源配置文件路径：config/ 子目录优先，兼容 ~/.dataswarm/ 根历史位置。"""
    home = dw_paths.workspace_root()
    for cand in (home / "config" / "datasource.yaml", home / "datasource.yaml"):
        if cand.is_file():
            return cand
    return home / "config" / "datasource.yaml"


# 业务库 db-ref（多工作空间 P4-4：执行时经 workspace 解析链取，不再 import 固化）。
# 解析链：env JIUWENSWARM_WORKSPACE_ID / 显式 workspace_id 经 dw_client
# （→ dwcli datasource current → workspace_context）> yaml 顶层 default_db。
# 多工作空间缺上下文时 dw_client 大声失败（WORKSPACE_CONTEXT_REQUIRED）。
def _default_db_ref(workspace_id: str = "") -> str:
    try:
        import dw_client  # noqa: PLC0415  # 脚本环境，就近 import
        ref = dw_client.default_db_ref(workspace_id or None)
        if ref:
            return ref
    except Exception as exc:  # noqa: BLE001 —— workspace 错误向上抛，其余走 legacy 兜底
        if type(exc).__name__ == "WorkspaceContextError":
            raise
    cfg_path = _datasource_config_path()
    if cfg_path.is_file():
        for line in cfg_path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].rstrip()
            m = re.match(r"^default_db\s*:\s*(.+)$", line.strip())
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return ""


def _biz_ref_for(workspace_id: str = "") -> str:
    """执行时解析业务库 ref；未配置时回退 doris://（4 层表所在）。"""
    return _default_db_ref(workspace_id) or "doris://"


def _biz_is_sqlite(workspace_id: str = "") -> bool:
    ref = _biz_ref_for(workspace_id)
    return ref.startswith("sqlite://") or (
        "://" not in ref and Path(ref).suffix.lower() == ".db"
    )


def _biz_is_mysql(workspace_id: str = "") -> bool:
    return _biz_ref_for(workspace_id).startswith("mysql://")


def _biz_is_duckdb(workspace_id: str = "") -> bool:
    return _biz_ref_for(workspace_id).startswith("duckdb://")


# 当前工作空间（main 里从 argv/env 设置；list 包装以便全模块可变读取）
WORKSPACE_ID = [os.environ.get("JIUWENSWARM_WORKSPACE_ID", "").strip()]

# 演示数据固定 ID 前缀（避免与随机 task_id 冲突，clean 精准清理）
TASK_PREFIX = "bld_"
WF_PREFIX = "bld_wf_"


def _dwcli(*args: str, stdin_json: dict | None = None, timeout: int = 1800) -> dict:
    """通过 dwcli 调用 dataagent API；任务/工作流禁止绕过此边界直写元数据库。"""
    cmd = [sys.executable or "python", "-m", "jiuwenswarm.cli.dwcli", *args]
    input_bytes = None
    if stdin_json is not None:
        input_bytes = json.dumps(stdin_json, ensure_ascii=False).encode("utf-8")
    proc = subprocess.run(cmd, input=input_bytes, capture_output=True, timeout=timeout)  # noqa: S603
    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"dwcli 输出非 JSON: {stdout[:300]!r}; stderr={stderr[:300]!r}") from exc
    if payload.get("status") == "error" or payload.get("http_code", 0) >= 400 or payload.get("ok") is False:
        detail = payload.get("detail") or payload.get("error") or stderr or payload
        raise RuntimeError(f"dwcli {' '.join(args[:2])} 失败: {detail}")
    if proc.returncode == 2:
        raise RuntimeError(f"dwcli {' '.join(args[:2])} 调用失败: {stderr or payload}")
    return payload


def _with_temp_text(text: str, suffix: str, call) -> dict:
    """把长文本经临时文件交给 dwcli，规避 Windows 命令行长度和引号限制。"""
    path = ""
    try:
        with NamedTemporaryFile("w", encoding="utf-8", suffix=suffix, delete=False) as handle:
            handle.write(text)
            path = handle.name
        return call(path)
    finally:
        if path:
            Path(path).unlink(missing_ok=True)


def _task_create(data: dict) -> dict:
    code = str(data.get("code") or "")
    args = ["task", "create"]
    for key, value in data.items():
        if key == "code" or value is None:
            continue
        if key == "params":
            value = json.dumps(value, ensure_ascii=False)
        args.extend([f"--{key.replace('_', '-')}", str(value)])
    if not code:
        return _dwcli(*args, "--code", "")
    return _with_temp_text(code, ".task-code", lambda path: _dwcli(*args, "--code-file", path))


def _workflow_create(data: dict) -> dict:
    args = ["workflow", "create"]
    for key, value in data.items():
        if value is not None:
            args.extend([f"--{key.replace('_', '-')}", str(value)])
    return _dwcli(*args)


def _save_plan(plan_id: str, plan: dict, tables: list[dict]) -> dict:
    """经 plan API 保存追加后的完整方案，禁止直接写 plan_table。"""
    plan_data = {**plan, "plan_id": plan_id, "tables": tables}
    name = str(plan.get("name") or plan.get("plan_name") or plan_id)
    business = str(plan.get("biz_database") or plan.get("business") or plan.get("biz_domain") or "")
    purpose = str(plan.get("purpose") or "")
    text = json.dumps(plan_data, ensure_ascii=False)
    result = _with_temp_text(
        text,
        ".plan.json",
        lambda path: _dwcli(
            "plan", "save", "--plan-id", plan_id, "--name", name,
            "--business", business, "--purpose", purpose, "--plan-data", f"@{path}",
        ),
    )
    _dwcli(
        "plan", "tables-save", "--json-file", "-",
        stdin_json={"plan_id": plan_id, "tables": tables},
    )
    return result


def _list_all(group: str, key: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        payload = _dwcli(group, "list", "--limit", "1000", "--offset", str(offset))
        page = payload.get(key) or []
        rows.extend(page)
        if len(page) < 1000:
            return rows
        offset += len(page)


def _workflow_get(workflow_id: str) -> dict | None:
    try:
        return _dwcli("workflow", "get", "--id", workflow_id)
    except RuntimeError as exc:
        if "not_found" in str(exc):
            return None
        raise


def _replace_nodes(workflow_id: str, nodes: list[dict]) -> list[dict]:
    payload = _dwcli(
        "workflow", "nodes", "--id", workflow_id, "--json-file", "-",
        stdin_json={"nodes": nodes},
    )
    return payload.get("nodes") or []


def _debug_and_wait(workflow_id: str, only_nodes: list[str] | None = None) -> tuple[dict, list[dict]]:
    args = ["workflow", "debug", "--id", workflow_id]
    if only_nodes is not None:
        args.extend(["--only-nodes", json.dumps(only_nodes, ensure_ascii=False)])
    inst = _dwcli(*args)
    instance_id = str(inst["instance_id"])
    result = _dwcli("instance", "wait", "--id", instance_id, "--timeout", "1800")
    runs = _dwcli("instance", "task-runs", "--id", instance_id).get("task_runs") or []
    return result, runs


def _run_ref(workspace_id: str = "") -> str:
    """导入代码运行时用的业务库 db-ref（与 DDL/ETL 任务 datasource_ref 同值）。"""
    return _biz_ref_for(workspace_id)


def _ensure_biz_db(biz_db: str) -> None:
    """幂等创建业务库。

    sqlite：文件在首次连接时自动创建，无需显式 CREATE DATABASE。
    duckdb：warehouse 文件由连接器按需创建，同样无需建库。
    doris/mysql：CREATE DATABASE IF NOT EXISTS；连不上时降级为警告（不阻断任务/工作流创建，
    实际建表仍由 DDL 任务在可写连接下执行）。

    凭据与 DDL 任务同链路：db_ref 经 db_connector.parse_db_ref 解析
    （config 段 / 环境变量 / UI 源四元组补齐），不在本脚本展开。
    """
    if _biz_is_sqlite(WORKSPACE_ID[0]):
        print(f"[db] sqlite 业务库（文件按需创建）: {biz_db}")
        return
    if _biz_is_duckdb(WORKSPACE_ID[0]):
        print(f"[db] duckdb warehouse（文件按需创建）: {biz_db}")
        return
    from jiuwenswarm.common import db_connector
    try:
        conn, meta = db_connector.connect(_run_ref(WORKSPACE_ID[0]), read_only=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 业务库 {biz_db} 预检失败（目标库不可达？）: {exc}", file=sys.stderr)
        return
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{biz_db}`")
        conn.commit()
        print(f"[db] 业务库 {biz_db} ensured (host={meta.get('host')}:{meta.get('port')})")
    finally:
        conn.close()


def _parse_ddl_columns(ddl_sql: str) -> list[str]:
    """从 DDL 中提取列名列表（排除 __load_time 等内部列）。"""
    cols = []
    # 提取括号内的列定义部分
    m = re.search(r"\(\s*\n(.*?)\n\s*\)", (ddl_sql or ""), re.DOTALL)
    if not m:
        return cols
    col_block = m.group(1)
    for line in col_block.split("\n"):
        line = line.strip()
        cm = re.match(r"`(\w+)`", line.rstrip(","))
        if cm:
            name = cm.group(1)
            if not name.startswith("__"):
                cols.append(name)
    return cols


def _build_csv_import_code(csv_dir: str, csv_file: str, table_short: str, ddl_sql: str, biz_db: str = "",
                           delimiter: str = ",", encoding: str = "utf-8-sig") -> str:
    """生成 CSV 导入任务的 Python code。

    凭据不烤进 code：运行时经 db_connector 解析 db-ref（config 段 / 环境变量 /
    UI 源四元组补齐），与 DDL/ETL 任务同链路。

    逻辑：pymysql 连 FE 建表(IF NOT EXISTS) + 复用判定 → requests PUT CSV 原文到 BE 8040 Stream Load。
    幂等：表已存在且行数 ≥ CSV 行数则跳过。
    UNIQUE KEY 表 Stream Load 自动 upsert；DUPLICATE KEY 表追加。
    sqlite 后端：直接 sqlite3 建表(IF NOT EXISTS) + DELETE + 按列名匹配 INSERT。
    mysql 后端：pymysql 建表 + DELETE + executemany 批量 INSERT（_build_mysql_csv_import_code）。
    duckdb 后端：CTAS read_csv 全量重建（_build_duckdb_csv_import_code，走共享 warehouse）。
    """
    csv_path = os.path.join(csv_dir, csv_file).replace("\\", "/")
    if _biz_is_sqlite(WORKSPACE_ID[0]):
        return _build_sqlite_csv_import_code(csv_path, table_short, ddl_sql, csv_file,
                                             delimiter=delimiter, encoding=encoding)
    if _biz_is_duckdb(WORKSPACE_ID[0]):
        return _build_duckdb_csv_import_code(csv_path, table_short, ddl_sql, csv_file,
                                             delimiter=delimiter, encoding=encoding)
    if _biz_is_mysql(WORKSPACE_ID[0]):
        return _build_mysql_csv_import_code(csv_path, table_short, ddl_sql, csv_file,
                                             biz_db, delimiter=delimiter, encoding=encoding)
    # CSV 导入 code 运行时经 db_connector 解析 ref（凭据不烤进任务 code，
    # 与 DDL/ETL 任务同链路：config 段 / 环境变量 / UI 源四元组补齐）
    db_name = biz_db or "olist"  # 兼容旧调用
    ddl_raw = _prep_ddl(ddl_sql, table_short)
    ddl_prefixed = ddl_raw.replace(
        f"`{table_short}`", f"`{db_name}`.`{table_short}`", 1,
    ) if ddl_raw != "SELECT 1" else ddl_raw
    ddl_stmts = json.dumps(ddl_prefixed, ensure_ascii=False)
    # 解析 DDL 列名，排除 __load_time 等内部列，用于 Stream Load columns 参数
    csv_columns = _parse_ddl_columns(ddl_sql)
    csv_columns_str = ",".join(f"`{c}`" for c in csv_columns) if csv_columns else ""
    run_ref = _run_ref(WORKSPACE_ID[0])
    # 生成固定 Python 代码（占位符替换，避免嵌套 f-string 转义地狱）
    code = r'''# -*- coding: utf-8 -*-
"""CSV 导入任务（plan_to_tasks 生成）— Stream Load 版"""
import uuid

import requests

REF = __REF_JSON__
DB = __DB_JSON__
TABLE = __TABLE_JSON__
CSV_FILE = __CSV_FILE_JSON__
CSV_COLUMNS = __CSV_COLUMNS_JSON__

def _connect():
    """FE 连接：db-ref 运行时解析（config / 环境变量 / UI 源凭据补齐）。"""
    from jiuwenswarm.common import db_connector
    conn, _meta = db_connector.connect(REF, read_only=False)
    return conn

def main():
    # 1. FE 建表 + 复用判定
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("CREATE DATABASE IF NOT EXISTS `" + DB + "`")
        conn.commit()
        cur.execute(__DDL_SQL_JSON__)
        conn.commit()
        cur.execute("SELECT COUNT(*) AS c FROM `" + DB.replace("`", "``") + "`.`" + TABLE.replace("`", "``") + "`")
        existing = int(cur.fetchone()[0] or 0)
        csv_rows = sum(1 for _ in open(__CSV_PATH_JSON__, encoding=__ENC_JSON__)) - 1
        if existing >= csv_rows * 0.9:
            print("[skip] %s: existing=%s csv=%s" % (TABLE, existing, csv_rows))
            return
        auth_user = conn.user
        auth_pass = conn.password
    finally:
        conn.close()

    # 2. BE Stream Load PUT 整个 CSV 文件
    import os
    from jiuwenswarm.common import db_connector
    spec = db_connector.parse_db_ref(REF)
    # BE 地址非凭据，优先级：UI 源 spec（be_host/be_http_port）> config doris: 段
    # > 环境变量 > FE host / 8040 缺省
    doris_cfg = db_connector._read_config_section("doris")
    be_host = (spec.get("be_host")
               or doris_cfg.get("be_host")
               or os.environ.get("DORIS_BE_HOST")
               or spec["host"])
    be_port = int(spec.get("be_http_port")
                  or doris_cfg.get("be_http_port")
                  or os.environ.get("DORIS_BE_HTTP_PORT")
                  or 8040)
    from urllib.parse import quote
    url = "http://%s:%s/api/%s/%s/_stream_load" % (be_host, be_port, quote(DB, safe=""), quote(TABLE, safe=""))
    label = "sl_" + "".join(c if c.isalnum() or c == "_" else "_" for c in TABLE)[:80] + "_" + uuid.uuid4().hex[:12]
    delimiter = __DELIM_JSON__
    sep_header = "\\t" if delimiter == "\t" else delimiter
    headers = {
        "Expect": "100-continue",
        "format": "csv",
        "column_separator": sep_header,
        "enclose": '"',
        "label": label,
        "strict_mode": "false",
        "max_filter_ratio": "0.1",
        "columns": CSV_COLUMNS,
    }
    with open(__CSV_PATH_JSON__, "rb") as f:
        body = f.read()
    nl = body.find(b"\n")
    if nl > 0:
        body = body[nl + 1:]
    r = requests.put(url, data=body, headers=headers,
                     auth=(auth_user, auth_pass),
                     timeout=300, allow_redirects=False)
    try:
        resp = r.json()
    except ValueError:
        raise RuntimeError("Stream Load non-JSON response (HTTP %s): %s" % (r.status_code, r.text[:300]))
    if resp.get("Status") != "Success":
        raise RuntimeError("Stream Load failed: %s (label=%s)" % (resp.get("Message"), label))
    total = int(resp.get("NumberLoadedRows", 0))
    filtered = int(resp.get("NumberFilteredRows", 0))
    if filtered > 0:
        print("[warn] %s: %s rows filtered" % (TABLE, filtered))
    print("[ok] %s: %s rows loaded via Stream Load (label=%s) from %s" % (TABLE, total, label, CSV_FILE))

if __name__ == "__main__":
    main()
'''
    code = (code
            .replace("__REF_JSON__", json.dumps(run_ref, ensure_ascii=False))
            .replace("__DDL_SQL_JSON__", ddl_stmts)
            .replace("__CSV_PATH_JSON__", json.dumps(csv_path, ensure_ascii=False))
            .replace("__TABLE_JSON__", json.dumps(str(table_short), ensure_ascii=False))
            .replace("__CSV_FILE_JSON__", json.dumps(str(csv_file), ensure_ascii=False))
            .replace("__CSV_COLUMNS_JSON__", json.dumps(csv_columns_str, ensure_ascii=False))
            .replace("__DELIM_JSON__", json.dumps(delimiter))
            .replace("__ENC_JSON__", json.dumps(encoding))
            .replace("__DB_JSON__", json.dumps(str(db_name), ensure_ascii=False)))
    return code


def _build_sqlite_csv_import_code(csv_path: str, table_short: str, ddl_sql: str, csv_file: str,
                                  delimiter: str = ",", encoding: str = "utf-8-sig") -> str:
    """生成 SQLite 后端 CSV 导入任务的 Python code。

    逻辑：sqlite3 连业务库 → CREATE TABLE IF NOT EXISTS（SQLite 翻译版 DDL）→
    DELETE 旧数据 → 按列名匹配 CSV 列 INSERT（_etl_* 内部列不在 CSV 则跳过）。
    幂等：每次导入前清空重灌。
    """
    db_path = _sqlite_db_path()
    ddl_sqlite = _prep_ddl(ddl_sql, table_short)
    csv_path = csv_path.replace("\\", "/")
    db_path_str = str(db_path).replace("\\", "/")
    code = r'''# -*- coding: utf-8 -*-
"""CSV 导入任务（sqlite 版）"""
import csv
import sqlite3

DB = __DB_PATH_JSON__
TABLE = __TABLE_JSON__
CSV_PATH = __CSV_PATH_JSON__

def main():
    conn = sqlite3.connect(DB, timeout=120)
    try:
        cur = conn.cursor()
        cur.execute(__DDL_SQL_JSON__)
        conn.commit()
        with open(CSV_PATH, encoding=__ENC_JSON__) as f:
            rows = list(csv.DictReader(f, delimiter=__DELIM_JSON__))
        if not rows:
            print("[skip] %s: CSV 为空" % TABLE)
            return
        csv_cols = [c for c in rows[0] if c is not None and c != ""]
        table_cols = [r[1] for r in cur.execute("PRAGMA table_info(%s)" % sqlite_ident(TABLE)).fetchall()]
        insert_cols = [c for c in table_cols if c in csv_cols]
        if not insert_cols:
            raise RuntimeError("CSV 列与表列无交集（表:%s CSV:%s）" % (table_cols, csv_cols))
        cur.execute("DELETE FROM %s" % sqlite_ident(TABLE))
        cols_sql = ",".join(sqlite_ident(c) for c in insert_cols)
        ph = ",".join("?" for _ in insert_cols)
        data = [[r.get(c) for c in insert_cols] for r in rows]
        # 分批提交：避免大表单事务长时间独占写锁（工作流并行节点会撞锁）
        BATCH = 10000
        stmt = "INSERT INTO %s (%s) VALUES (%s)" % (sqlite_ident(TABLE), cols_sql, ph)
        try:
            for i in range(0, len(data), BATCH):
                cur.executemany(stmt, data[i:i + BATCH])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        print("[ok] %s: %d 行导入（来自 %s）" % (TABLE, len(data), "__CSV_FILE__"))
    finally:
        conn.close()

def sqlite_ident(name):
    return '"' + str(name).replace('"', '""') + '"'

if __name__ == "__main__":
    main()
'''
    return (code
            .replace("__DB_PATH_JSON__", json.dumps(db_path_str, ensure_ascii=False))
            .replace("__TABLE_JSON__", json.dumps(str(table_short), ensure_ascii=False))
            .replace("__CSV_PATH_JSON__", json.dumps(csv_path, ensure_ascii=False))
            .replace("__CSV_FILE__", str(csv_file).replace("\\", "\\\\").replace('"', '\\"'))
            .replace("__DELIM_JSON__", json.dumps(delimiter))
            .replace("__ENC_JSON__", json.dumps(encoding))
            .replace("__DDL_SQL_JSON__", json.dumps(ddl_sqlite, ensure_ascii=False)))


def _build_duckdb_csv_import_code(csv_path: str, table_short: str, ddl_sql: str, csv_file: str,
                                  delimiter: str = ",", encoding: str = "utf-8-sig") -> str:
    """生成 DuckDB 后端 CSV 导入任务的 Python code。

    逻辑：db_connector.execute 走共享 warehouse 连接（白名单 CTAS），
    CREATE OR REPLACE TABLE ... AS SELECT * FROM read_csv(文件)——天然幂等
    （全量重建），列类型由 duckdb 嗅探。无需建表/导入两步。
    """
    ref = _biz_ref_for(WORKSPACE_ID[0])
    csv_path = csv_path.replace("\\", "/")
    is_excel = csv_file.lower().endswith((".xlsx", ".xlsm", ".xls"))
    # duckdb read_csv 只认自身编码名（utf-8/gbk...），utf-8-sig 是 Python codec 名；
    # utf-8 模式下 BOM 由 reader 自动剥离（实测），gbk 原样可用。
    encoding_param = "utf-8" if encoding.lower() in ("utf-8-sig", "utf-8") else encoding
    if is_excel:
        # excel 无分隔符/编码参数；read_xlsx autoload excel 扩展
        sql_template = (
            'CREATE OR REPLACE TABLE "%s" AS '
            "SELECT * FROM read_xlsx(%s)"
        )
        reader_args = "% (TABLE, json.dumps(CSV_PATH))"
    else:
        sql_template = (
            'CREATE OR REPLACE TABLE "%s" AS '
            "SELECT * FROM read_csv(%s, delim=%s, header=true, encoding=%s, quote=%s)"
        )
        reader_args = "% (TABLE, json.dumps(CSV_PATH), json.dumps(DELIM), json.dumps(ENC), json.dumps('\"'))"
    code = r'''# -*- coding: utf-8 -*-
"""CSV/Excel 导入任务（duckdb warehouse CTAS 版）"""
import json
import sys

REF = __REF_JSON__
TABLE = __TABLE_JSON__
CSV_PATH = __CSV_PATH_JSON__
DELIM = __DELIM_JSON__
ENC = __ENC_JSON__

def main():
    from jiuwenswarm.common import db_connector
    # 文件路径/delim/encoding 必须以 SQL 字符串字面量进 SQL（json.dumps），
    # 裸值会被 parse_write_sql 判为语法错误
    sql = (
        __SQL_TEMPLATE_JSON__
        __READER_ARGS__
    )
    result = db_connector.execute(REF, sql)
    print("[ok] %s: CTAS 重建完成（来自 __CSV_FILE__），affected=%s"
          % (TABLE, result.get("affected_rows")))

if __name__ == "__main__":
    main()
'''
    return (code
            .replace("__REF_JSON__", json.dumps(ref, ensure_ascii=False))
            .replace("__TABLE_JSON__", json.dumps(str(table_short), ensure_ascii=False))
            .replace("__CSV_PATH_JSON__", json.dumps(csv_path, ensure_ascii=False))
            .replace("__CSV_FILE__", str(csv_file).replace("\\", "\\\\").replace('"', '\\"'))
            .replace("__SQL_TEMPLATE_JSON__", json.dumps(sql_template, ensure_ascii=False))
            .replace("__READER_ARGS__", reader_args)
            .replace("__DELIM_JSON__", json.dumps(delimiter))
            .replace("__ENC_JSON__", json.dumps(encoding_param)))


def _build_duckdb_ctas_sql(csv_dir: str, csv_file: str, table_short: str,
                           delimiter: str = ",", encoding: str = "utf-8-sig") -> str:
    """生成 DuckDB 后端 ODS 导入的单条 CTAS SQL（sql executor 直跑）。

    与 _build_duckdb_csv_import_code 同语义（CTAS 全量重建，天然幂等），但不再
    套 python 子进程壳——PythonExecutor 用 subprocess 起独立进程打开 warehouse，
    与执行进程（web/编排引擎）持有的共享连接跨进程锁冲突（DuckDB 单文件单写者）。
    sql executor 在执行进程内走 db_connector.execute → 共享连接，无锁冲突。
    路径/delim/encoding 以 SQL 字符串字面量进 SQL（json.dumps），
    裸值会被 parse_write_sql 判为语法错误。
    """
    csv_path = os.path.join(csv_dir, csv_file).replace("\\", "/")
    # duckdb read_csv 只认自身编码名（utf-8/gbk...），utf-8-sig 是 Python codec 名；
    # utf-8 模式下 BOM 由 reader 自动剥离（实测），gbk 原样可用。
    encoding_param = "utf-8" if encoding.lower() in ("utf-8-sig", "utf-8") else encoding
    if csv_file.lower().endswith((".xlsx", ".xlsm", ".xls")):
        # excel 无分隔符/编码参数；read_xlsx autoload excel 扩展
        return (
            f'CREATE OR REPLACE TABLE "{table_short}" AS '
            f"SELECT * FROM read_xlsx({json.dumps(csv_path)})"
        )
    return (
        f'CREATE OR REPLACE TABLE "{table_short}" AS '
        f"SELECT * FROM read_csv({json.dumps(csv_path)}, "
        f"delim={json.dumps(delimiter)}, header=true, encoding={json.dumps(encoding_param)}, quote='\"')"
    )


def _build_mysql_csv_import_code(csv_path: str, table_short: str, ddl_sql: str, csv_file: str,
                                 biz_db: str, delimiter: str = ",",
                                 encoding: str = "utf-8-sig") -> str:
    """生成 MySQL 后端 CSV 导入任务的 Python code。

    逻辑：pymysql 连业务库 → CREATE TABLE IF NOT EXISTS（_to_mysql_ddl 翻译版 DDL）
    → DELETE 旧数据 → 按列名匹配 CSV 列 executemany 批量 INSERT（分批提交）。
    幂等：每次导入前清空重灌（与 sqlite 版一致）。
    """
    ddl_mysql = _prep_ddl(ddl_sql, table_short)
    ddl_prefixed = ddl_mysql.replace(
        f"`{table_short}`", f"`{biz_db}`.`{table_short}`", 1,
    ) if ddl_mysql != "SELECT 1" else ddl_mysql
    csv_path = csv_path.replace("\\", "/")
    run_ref = _run_ref(WORKSPACE_ID[0])
    code = r'''# -*- coding: utf-8 -*-
"""CSV 导入任务（plan_to_tasks 生成）— MySQL 版"""
import csv

REF = __REF_JSON__
DB = __DB_JSON__
TABLE = __TABLE_JSON__
CSV_PATH = __CSV_PATH_JSON__

def q(name):
    return "`" + str(name).replace("`", "``") + "`"

def main():
    # db-ref 运行时解析凭据（config / 环境变量 / UI 源四元组补齐），不烤进任务 code；
    # REF 自带 database 是源库（凭据四元组匹配用），写入目标经 select_db 切到业务库
    from jiuwenswarm.common import db_connector
    conn, _meta = db_connector.connect(REF, read_only=False)
    try:
        conn.select_db(DB)
        cur = conn.cursor()
        cur.execute(__DDL_SQL_JSON__)
        conn.commit()
        with open(CSV_PATH, encoding=__ENC_JSON__, newline="") as f:
            rows = list(csv.DictReader(f, delimiter=__DELIM_JSON__))
        if not rows:
            print("[skip] %s: CSV 为空" % TABLE)
            return
        csv_cols = [c for c in rows[0] if c is not None and c != ""]
        cur.execute("SHOW COLUMNS FROM " + q(DB) + "." + q(TABLE))
        table_cols = [r[0] for r in cur.fetchall()]
        insert_cols = [c for c in table_cols if c in csv_cols]
        if not insert_cols:
            raise RuntimeError("CSV 列与表列无交集（表:%s CSV:%s）" % (table_cols, csv_cols))
        cur.execute("DELETE FROM " + q(DB) + "." + q(TABLE))
        cols_sql = ",".join(q(c) for c in insert_cols)
        ph = ",".join(["%s"] * len(insert_cols))
        data = [[r.get(c) for c in insert_cols] for r in rows]
        # 空串→None：Doris strict_mode 语义对齐，避免 NOT NULL 列/日期列写入 '' 报错
        data = [[(v if v != "" else None) for v in row] for row in data]
        BATCH = 10000
        stmt = "INSERT INTO " + q(DB) + "." + q(TABLE) + " (" + cols_sql + ") VALUES (" + ph + ")"
        try:
            for i in range(0, len(data), BATCH):
                cur.executemany(stmt, data[i:i + BATCH])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        print("[ok] %s: %d 行导入（来自 __CSV_FILE__）" % (TABLE, len(data)))
    finally:
        conn.close()

if __name__ == "__main__":
    main()
'''
    return (code
            .replace("__REF_JSON__", json.dumps(run_ref, ensure_ascii=False))
            .replace("__DDL_SQL_JSON__", json.dumps(ddl_prefixed, ensure_ascii=False))
            .replace("__DB_JSON__", json.dumps(str(biz_db), ensure_ascii=False))
            .replace("__TABLE_JSON__", json.dumps(str(table_short), ensure_ascii=False))
            .replace("__CSV_PATH_JSON__", json.dumps(csv_path, ensure_ascii=False))
            .replace("__CSV_FILE__", str(csv_file).replace("\\", "\\\\").replace('"', '\\"'))
            .replace("__DELIM_JSON__", json.dumps(delimiter))
            .replace("__ENC_JSON__", json.dumps(encoding)))


def _prep_ddl(ddl_sql: str, table_short: str, sqlite: bool | None = None) -> str:
    """把 DDL 转成可执行的 CREATE TABLE（加库前缀 + IF NOT EXISTS）。返回纯 SQL。

    sqlite=True 时把 Doris 风格 DDL 翻译成 SQLite 兼容（去 UNIQUE KEY /
    DISTRIBUTED BY / PROPERTIES / PARTITION / COMMENT）。
    mysql 后端：plan 由设计层渲染 MySQL DDL 直接可用；旧 plan（Doris 风格）
    由 _to_mysql_ddl 兜底翻译。
    duckdb 后端：新 plan 已是双引号 DDL 直接可用；旧 plan（Doris 风格）由
    _to_duckdb_ddl 兜底翻译。
    """
    if sqlite is None:
        sqlite = _biz_is_sqlite(WORKSPACE_ID[0])
    if sqlite:
        return _to_sqlite_ddl(ddl_sql, table_short)
    if _biz_is_mysql(WORKSPACE_ID[0]):
        return _to_mysql_ddl(ddl_sql, table_short)
    if _biz_is_duckdb(WORKSPACE_ID[0]):
        return _to_duckdb_ddl(ddl_sql, table_short)
    ddl = (ddl_sql or "").strip()
    if not ddl:
        return "SELECT 1"
    if not ddl.startswith("CREATE TABLE"):
        ddl = f"CREATE TABLE IF NOT EXISTS `{table_short}`\n" + ddl
    return ddl


def _to_mysql_ddl(ddl_sql: str, table_short: str) -> str:
    """把 Doris 风格 DDL 翻译成 MySQL 兼容的 CREATE TABLE（兜底，新 plan 应已是 MySQL DDL）。

    Doris 专属子句（ENGINE=OLAP / DUPLICATE KEY / DISTRIBUTED BY / PROPERTIES）
    出现时才翻译：提取列定义块重拼；已是 MySQL DDL 则原样返回（加 IF NOT EXISTS）。
    """
    ddl = (ddl_sql or "").strip()
    if not ddl:
        return f"CREATE TABLE IF NOT EXISTS `{table_short}` (\n  `id` BIGINT NULL\n)"
    if not re.search(r"ENGINE=OLAP|DUPLICATE\s+KEY|DISTRIBUTED\s+BY|PROPERTIES", ddl, re.I):
        if not ddl.startswith("CREATE TABLE"):
            ddl = f"CREATE TABLE IF NOT EXISTS `{table_short}`\n" + ddl
        return ddl

    open_idx = ddl.find("(")
    if open_idx == -1:
        return ddl
    depth = 0
    end = -1
    for i in range(open_idx, len(ddl)):
        if ddl[i] == "(":
            depth += 1
        elif ddl[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return ddl
    col_block = ddl[open_idx + 1:end]
    # Doris 类型 → MySQL 类型：唯一真相源 _dw_common/scripts/dw_type_map.py
    # （dialect-convergence 批次 1b G3，收口前此处与 render.py 各维护一份正则）
    col_block = re.sub(
        r"`(\w+)`\s+([A-Za-z]+(?:\(\d+(?:,\s*\d+)?\))?)",
        lambda m: f"`{m.group(1)}` {dw_type_map.dialect_type(m.group(2), 'mysql')}",
        col_block,
    )
    lines = [ln.rstrip().rstrip(",").rstrip() for ln in col_block.split("\n") if ln.strip()]
    body = ",\n".join(lines)
    return (
        f"CREATE TABLE IF NOT EXISTS `{table_short}` (\n{body}\n"
        f") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )


def _sqlite_db_path() -> Path:
    """解析业务库 sqlite db-ref → 绝对路径。"""
    ref = _biz_ref_for(WORKSPACE_ID[0])
    if ref.startswith("sqlite://"):
        ref = ref[len("sqlite://"):]
    return Path(ref).expanduser().resolve()


def _to_duckdb_ddl(ddl_sql: str, table_short: str) -> str:
    """把 DDL 翻译成 DuckDB 兼容的 CREATE TABLE（兜底，新 plan 应已是双引号 DDL）。

    duckdb 不支持：反引号标识符（→ 双引号）、列内 COMMENT（→ 剥离）、
    LARGEINT（→ BIGINT）、ENGINE=OLAP/DUPLICATE KEY/DISTRIBUTED BY/PROPERTIES
    尾部子句（→ 剥离）。已是 duckdb DDL 则仅加 IF NOT EXISTS 原样返回。
    """
    ddl = (ddl_sql or "").strip()
    if not ddl:
        return f'CREATE TABLE IF NOT EXISTS "{table_short}" (\n  "id" BIGINT NULL\n)'
    doris_style = bool(re.search(
        r"ENGINE=OLAP|DUPLICATE\s+KEY|DISTRIBUTED\s+BY|PROPERTIES", ddl, re.I,
    )) or "`" in ddl or re.search(r"\bCOMMENT\s+'", ddl, re.I) or "LARGEINT" in ddl.upper()
    if not doris_style:
        if not ddl.startswith("CREATE TABLE"):
            ddl = f'CREATE TABLE IF NOT EXISTS "{table_short}"\n' + ddl
        return ddl

    open_idx = ddl.find("(")
    if open_idx == -1:
        return ddl
    depth = 0
    end = -1
    for i in range(open_idx, len(ddl)):
        if ddl[i] == "(":
            depth += 1
        elif ddl[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return ddl
    col_block = ddl[open_idx + 1:end]
    col_block = re.sub(r"\s+COMMENT\s+'[^']*'", "", col_block, flags=re.I)
    col_block = re.sub(r"\bLARGEINT\b", "BIGINT", col_block, flags=re.I)
    col_block = col_block.replace("`", '"')
    lines = [ln.rstrip().rstrip(",").rstrip() for ln in col_block.split("\n") if ln.strip()]
    body = ",\n".join(lines)
    return f'CREATE TABLE IF NOT EXISTS "{table_short}" (\n{body}\n)'


def _duckdb_temporal_columns(ddl_sql: str) -> dict[str, str]:
    """Return target columns that require explicit DuckDB temporal casts."""
    if not ddl_sql:
        return {}
    open_idx = ddl_sql.find("(")
    if open_idx == -1:
        return {}
    depth = 0
    end = -1
    for index in range(open_idx, len(ddl_sql)):
        if ddl_sql[index] == "(":
            depth += 1
        elif ddl_sql[index] == ")":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end == -1:
        return {}

    temporal: dict[str, str] = {}
    for line in ddl_sql[open_idx + 1:end].splitlines():
        match = re.match(
            r"\s*[`\"]([^`\"]+)[`\"]\s+([A-Za-z]+)", line.rstrip(",")
        )
        if not match:
            continue
        dtype = match.group(2).upper()
        if dtype in {"DATE", "DATETIME", "TIMESTAMP"}:
            temporal[match.group(1).lower()] = "DATE" if dtype == "DATE" else "TIMESTAMP"
    return temporal


def _cast_duckdb_temporal_selects(sql: str, ddl_sql: str) -> str:
    """Add explicit casts for VARCHAR expressions assigned to temporal columns."""
    temporal = _duckdb_temporal_columns(ddl_sql)
    if not temporal:
        return sql
    match = re.search(
        r"(INSERT\s+INTO\s+[^()]+\((?P<columns>[^()]*)\)\s+SELECT\s+)"
        r"(?P<select>.*?)\s+(FROM\s+)",
        sql,
        flags=re.I | re.S,
    )
    if not match:
        return sql

    columns = [column.strip().strip('`"').lower()
               for column in _split_top_commas(match.group("columns"))]
    select_parts = _split_top_commas(match.group("select"))
    if len(columns) != len(select_parts):
        return sql

    changed = False
    for index, column in enumerate(columns):
        target_type = temporal.get(column)
        if not target_type:
            continue
        select_part = select_parts[index]
        if re.search(r"\b(?:TRY_)?CAST\s*\(", select_part, re.I):
            continue
        alias_match = re.match(r"(?is)(.*?)(\s+AS\s+[`\"\w]+\s*)$", select_part)
        if alias_match:
            expression = alias_match.group(1).strip()
            alias = alias_match.group(2)
            select_parts[index] = f"TRY_CAST(({expression}) AS {target_type}){alias}"
        else:
            select_parts[index] = f"TRY_CAST(({select_part}) AS {target_type})"
        changed = True
    if not changed:
        return sql
    return sql[:match.start("select")] + ", ".join(select_parts) + sql[match.end("select"):]


def _translate_duckdb_sql(sql: str, ddl_sql: str = "") -> str:
    """把 Doris/MySQL 风格 ETL SQL 翻译成 DuckDB 兼容。

    duckdb 原生支持 DATEDIFF(unit,a,b)/DATE_ADD/CAST AS DATETIME/DOUBLE，
    仅两参数 DATEDIFF(a,b)（MySQL 形态，Doris 语义 day 差）需翻译。
    反引号标识符（含表别名限定形态 o.`col`）duckdb 解析器一律不认，
    统一替换为双引号。
    """
    if not sql:
        return sql
    out = _cast_duckdb_temporal_selects(sql, ddl_sql)
    out = re.sub(
        r"DATEDIFF\s*\(\s*([^,()]+?)\s*,\s*([^,()]+?)\s*\)",
        r"date_diff('day', \2, \1)",
        out,
        flags=re.I,
    )
    return re.sub(r"`([^`]+)`", r'"\1"', out)


def _translate_mysql_sql(sql: str) -> str:
    """把 Doris 风格 ETL SQL 翻译成 MySQL 兼容（兜底，新 plan 应已是 MySQL SQL）。

    DATEDIFF(a,b)/DATE_ADD(x,INTERVAL n DAY)/CAST AS DATE/DATETIME 本就是
    MySQL 语法（Doris 借用），无需翻译。真正的兼容点：
      CAST(x AS DOUBLE) —— MySQL 8.0.17 前不支持 DOUBLE 目标类型 → 乘 1.0
      CURRENT_DATE()/CURRENT_TIMESTAMP() —— MySQL 无括号形态
    """
    if not sql:
        return sql
    # CAST(x AS DOUBLE)：类型在括号内（常见形态）。(\w.`+ ) 容忍 a.b/`c` 引用，
    # 不跨嵌套括号——嵌套子查询形态罕见，兜底路径不做全量方言翻译
    sql = re.sub(
        r"CAST\s*\(\s*([\w.`\"]+)\s+AS\s+DOUBLE\s*\)",
        r"(\1 * 1.0)",
        sql,
        flags=re.I,
    )
    sql = re.sub(r"CURRENT_DATE\s*\(\)", "CURRENT_DATE", sql, flags=re.I)
    sql = re.sub(r"CURRENT_TIMESTAMP\s*\(\)", "CURRENT_TIMESTAMP", sql, flags=re.I)
    return sql


def _translate_etl_sql(sql: str, ddl_sql: str = "") -> str:
    """ETL SQL 按业务库方言翻译分发：sqlite→_translate_sqlite_sql，
    duckdb→_translate_duckdb_sql，mysql→_translate_mysql_sql（旧 Doris plan 兜底），
    doris 原样。"""
    if _biz_is_sqlite(WORKSPACE_ID[0]):
        return _translate_sqlite_sql(sql)
    if _biz_is_duckdb(WORKSPACE_ID[0]):
        return _translate_duckdb_sql(sql, ddl_sql)
    if _biz_is_mysql(WORKSPACE_ID[0]):
        return _translate_mysql_sql(sql)
    return sql


def _dry_run_etl_sql(etl_sql: str, table_short: str) -> None:
    """落任务前对翻译后的 ETL SQL 做方言感知预检（dialect-convergence 批次 1a）。

    经 dwcli sql explain → 服务端 db_connector.dry_run：
    - 拦截「源表不存在/列拼错/方言函数不存在」——这些错误过去要到工作流
      执行到该节点才暴露，Agent 需看日志回炉整轮；此处落库前直接指出问题表。
    - status=SKIPPED（如 DROP 无源 SELECT 可验）不视为失败，静默放行——
      预检是增强防线不是硬门槛，Web 服务不可达（dwcli exit 2）同样放行，
      由执行期报错兜底，不阻断建仓主流程。
    - 预检 FAILED（CHECKED 语句真实执行报错）抛 ValueError 拦截。

    注意：预检在 DDL 任务创建前逐表调用，此时源表可能尚未建（分层依赖
    后建）——EXPLAIN 是纯解析/规划，表不存在会报错。因此 ODS 导入任务
    不预检（CSV 物化无 SQL 可验），派生层 ETL 预检发生在 seed 的建表
    之后逐表进行，源 ODS 表先建先导，天然满足依赖序。
    """
    sql = (etl_sql or "").strip()
    if not sql:
        return
    try:
        result = _with_temp_text(
            sql, ".etl.sql",
            lambda path: _dwcli(
                "sql", "explain",
                "--db-ref", _biz_ref_for(WORKSPACE_ID[0]),
                "--sql-file", path,
            ),
        )
    except RuntimeError as exc:
        # dwcli/HTTP 层失败（服务不可达、路由错）不阻断建仓——执行期兜底
        print(f"  [dry-run][skip] {table_short}: 预检通道不可用（{str(exc)[:120]}）",
              file=sys.stderr)
        return
    if result.get("ok") is False:
        raise ValueError(
            f"表 {table_short} 的 ETL SQL 预检失败（phase=SQL_DRY_RUN, "
            f"dialect={result.get('dialect', '?')}）：{str(result.get('error'))[:300]}"
        )
    status = result.get("status") or "CHECKED"
    print(f"  [dry-run] {table_short}: {status} (dialect={result.get('dialect', '?')})")


def _to_sqlite_ddl(ddl_sql: str, table_short: str) -> str:
    """把 Doris 风格 DDL 翻译成 SQLite 兼容的 CREATE TABLE。

    只保留列定义块（首个 ( 到配对的 )），剥离 COMMENT/DISTRIBUTED/PROPERTIES/
    PARTITION/UNIQUE KEY/DUPLICATE KEY 等 Doris 子句。SQLite 对列类型宽松，
    VARCHAR/DECIMAL/DATETIME 等原样保留即可。
    """
    ddl = (ddl_sql or "").strip()
    if not ddl:
        return f"CREATE TABLE IF NOT EXISTS `{table_short}` (\n  id INTEGER PRIMARY KEY AUTOINCREMENT\n)"
    if not ddl.startswith("CREATE TABLE"):
        ddl = f"CREATE TABLE IF NOT EXISTS `{table_short}`\n" + ddl

    open_idx = ddl.find("(")
    if open_idx == -1:
        return ddl
    depth = 0
    end = -1
    for i in range(open_idx, len(ddl)):
        if ddl[i] == "(":
            depth += 1
        elif ddl[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return ddl
    col_block = ddl[open_idx + 1:end]
    # 剥离行内 COMMENT '...'；NOT NULL 一并去掉（LEFT JOIN 空值/NULL 注入在 sqlite 更宽松，
    # 本地验证链路不被 NOT NULL 约束卡死；生产 Doris 语义保留在原始 DDL 里）
    col_block = re.sub(r"\s+COMMENT\s+'[^']*'", "", col_block, flags=re.I)
    col_block = re.sub(r"\s+NOT\s+NULL\b", "", col_block, flags=re.I)
    lines = []
    for ln in col_block.split("\n"):
        s = ln.rstrip()
        if not s.strip():
            continue
        lines.append(s.rstrip(","))
    cols_sql = ",\n  ".join(lines) if lines else "id INTEGER PRIMARY KEY"
    head = ddl[:open_idx].rstrip()
    if not re.match(r"^CREATE\s+TABLE\b", head, re.I):
        head = f"CREATE TABLE IF NOT EXISTS `{table_short}`"
    return f"{head} (\n  {cols_sql}\n)"


def _translate_sqlite_sql(sql: str) -> str:
    """把 Doris ETL SQL 翻译成 SQLite 兼容。

    处理：CAST(x AS DATETIME)→x；CAST(x AS DATE)→date(x)；
    CAST(x AS DOUBLE)→x*1.0；DATEDIFF(a,b)→julianday 差；DATE_ADD(x,INTERVAL n DAY)→date(x,'+n day')。
    """
    if not sql:
        return sql
    # DOUBLE → 乘 1.0 保持浮点语义（贪婪捕获到 AS DOUBLE 前最后一个 )）
    sql = re.sub(
        r"CAST\s*\((.*)\)\s*AS\s+DOUBLE\b",
        r"(\1 * 1.0)",
        sql,
        flags=re.I,
    )
    sql = re.sub(
        r"CAST\s*\(\s*([\w.]+)\s*AS\s+DATETIME\s*\)",
        r"\1",
        sql,
        flags=re.I,
    )
    sql = re.sub(
        r"CAST\s*\(\s*([\w.]+)\s*AS\s+DATE\s*\)",
        r"date(\1)",
        sql,
        flags=re.I,
    )
    sql = re.sub(
        r"DATEDIFF\s*\(\s*([^,]+?)\s*,\s*([^()]+?)\s*\)",
        r"(julianday(\1) - julianday(\2))",
        sql,
        flags=re.I,
    )
    sql = re.sub(
        r"DATE_ADD\s*\(\s*([^,]+?)\s*,\s*INTERVAL\s+(\d+)\s+DAY\s*\)",
        r"date(\1, '+\g<2> day')",
        sql,
        flags=re.I,
    )
    # CURRENT_DATE()/CURRENT_TIMESTAMP() → SQLite 无括号形态
    sql = re.sub(r"CURRENT_DATE\s*\(\)", "CURRENT_DATE", sql, flags=re.I)
    sql = re.sub(r"CURRENT_TIMESTAMP\s*\(\)", "CURRENT_TIMESTAMP", sql, flags=re.I)
    return sql


def _table_short(table_name: str) -> str:
    return table_name.split(".")[-1] if "." in table_name else table_name


def _parse_source_shorts(source_tables: str | list | None) -> list[str]:
    """source_tables 是 list 或 JSON 字符串；CSV 源跳过，表源取短表名。"""
    if isinstance(source_tables, str):
        try:
            sources = json.loads(source_tables or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
    else:
        sources = source_tables or []
    out = []
    for source in sources:
        s = str(source)
        if s.lower().endswith((".csv", ".xlsx", ".xlsm", ".xls")):
            continue
        out.append(s.split(".")[-1])
    return out


def _match_csv_file(short: str, csv_files: dict[str, str]) -> str | None:
    """把 ODS 短表名匹配到 CSV 文件名。

    匹配顺序（归一化：去 ods_ 前缀 / _dataset 后缀 / olist_ 前缀 / 单复数 s）：
    1. 短表名直接命中 CSV stem；
    2. 归一化 + 单复数规整后完全一致；
    3. 归一化 token 子集（仅当 target ≥2 token，避免单 token 误匹配，取 token 最少者）。
    例：ods_customer → olist_customers_dataset.csv；ods_order → olist_orders_dataset.csv；
    ods_category_translation → product_category_name_translation.csv。
    """
    def norm(s: str) -> str:
        return re.sub(r"^(ods_|dwd_|dws_|ads_)", "", s.lower())

    def canon(s: str) -> str:
        s = re.sub(r"_dataset$", "", s)
        s = re.sub(r"^olist_", "", s)
        if s.endswith("es") and len(s) > 4:
            s = s[:-2]
        elif s.endswith("s") and len(s) > 3:
            s = s[:-1]
        return s

    target = canon(norm(short))
    candidates: list[tuple[str, int]] = []
    for stem, fname in csv_files.items():
        base = canon(norm(stem))
        if stem == short or norm(stem) == norm(short):
            return fname
        if base == target:
            return fname
        t_tokens = set(target.split("_"))
        c_tokens = set(base.split("_"))
        if len(t_tokens) >= 2 and t_tokens <= c_tokens:
            candidates.append((fname, len(c_tokens)))
    if candidates:
        candidates.sort(key=lambda x: (x[1], x[0]))
        return candidates[0][0]
    return None


def _read_plan(plan_id: str) -> tuple[dict | None, list[dict]]:
    """读 plan 元数据 + tables 列表。

    优先从 <workspace>/plans/<plan_id>.json 文件读取（新方案），
    fallback 到 DB plan/plan_table 查询（旧方案兼容）。
    """
    from jiuwenswarm.common.utils import get_user_workspace_dir
    plan_file = Path(get_user_workspace_dir()) / "plans" / f"{plan_id}.json"
    if plan_file.is_file():
        with open(plan_file, "r", encoding="utf-8") as f:
            plan_data = json.load(f)
        plan_meta = {k: v for k, v in plan_data.items() if k != "tables"}
        tables = plan_data.get("tables", [])
        return plan_meta, tables

    plan = _dwcli("plan", "get", "--plan-id", plan_id)
    tables = plan.get("tables") or _dwcli("plan", "tables", "--plan-id", plan_id).get("tables") or []
    return plan, tables


def _ensure_csv(plan: dict) -> str:
    """若 plan 的 csv_dir 缺 CSV 文件，尝试从本地 SQLite 库导出补齐。返回 csv_dir。

    SQLite 库路径从 plan.source_config.sqlite_db 读取；若未配置则跳过自动导出，
    仅在 csv_dir 有缺失文件时报错。
    """
    try:
        source_config = json.loads(plan.get("source_config") or "{}")
    except Exception:
        source_config = {}
    csv_dir = source_config.get("csv_dir")
    if not csv_dir:
        raise RuntimeError(
            f"plan {plan.get('plan_id')} 缺少 source_config.csv_dir，"
            "请在创建 plan 时指定 --csv-dir"
        )
    files = source_config.get("files") or []
    file_options = source_config.get("file_options") or {}
    Path(csv_dir).mkdir(parents=True, exist_ok=True)
    # 缺哪些文件
    missing = [f for f in files if not (Path(csv_dir) / f).is_file()]
    if not missing:
        return csv_dir

    # 尝试从 source_config.sqlite_db 导出
    sqlite_db = source_config.get("sqlite_db")
    if not sqlite_db:
        # 兜底：尝试项目资源目录下的 {biz_database}.db.orig / {biz_database}.db
        biz_db = plan.get("biz_database") or ""
        data_root = _PROJECT_ROOT / "resources" / "data"
        for cand in (f"{biz_db}.db.orig", f"{biz_db}.db"):
            candidate = data_root / cand
            if candidate.is_file():
                sqlite_db = str(candidate)
                break
    if not sqlite_db or not Path(sqlite_db).is_file():
        raise FileNotFoundError(
            f"CSV 文件缺失且无 SQLite 源库可导出：{missing}。"
            f"请准备 CSV 文件到 {csv_dir}，或在 source_config.sqlite_db 指定源库路径"
        )

    # CSV 文件名 → SQLite 表名映射规则
    # 规则：stem 去掉 _dataset 后缀；若 {biz_database}_{stem} 表存在则优先用
    import sqlite3
    src_conn = sqlite3.connect(str(sqlite_db))
    try:
        existing_tables = {r[0] for r in src_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for f in missing:
            stem = Path(f).stem
            sqlite_table = stem
            if sqlite_table.endswith("_dataset"):
                sqlite_table = sqlite_table[:-len("_dataset")]
            # 尝试带业务域前缀
            biz_prefix = f"{plan.get('biz_database', '')}_"
            if f"{biz_prefix}{sqlite_table}" in existing_tables:
                sqlite_table = f"{biz_prefix}{sqlite_table}"
            elif sqlite_table not in existing_tables:
                print(f"  [skip] {f}: SQLite 表 {sqlite_table} 不存在", file=sys.stderr)
                continue
            cur = src_conn.execute(f'SELECT * FROM "{sqlite_table}"')
            cols = [d[0] for d in cur.description]
            opts = file_options.get(f) or {}
            with open(Path(csv_dir) / f, "w",
                      encoding=str(opts.get("encoding") or "utf-8-sig"), newline="") as out:
                w = csv.writer(out, delimiter=str(opts.get("delimiter") or ","))
                w.writerow(cols)
                w.writerows(cur.fetchall())
            print(f"  [csv] 导出 {f} ({len(cols)} 列)")
    finally:
        src_conn.close()
    return csv_dir


_AGG_FN_RE = re.compile(r"\b(SUM|AVG|MAX|MIN|COUNT)\s*\(", re.IGNORECASE)


def _split_top_commas(s: str) -> list[str]:
    """按顶层逗号切分（忽略括号内逗号）。"""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _assert_etl_aggregate_output(etl_sql: str, short: str) -> None:
    """派生层 etl_sql 落任务前的静态校验（消费侧防线）。

    唯一实现在 _dw_common/scripts/dw_aggregate_check.py（dialect-convergence
    批次 1b G8 收口，此前与 render.py 各维护一份正则口径）。
    """
    dw_aggregate_check.assert_group_by_output(etl_sql, short)


def _cleanup_builder_assets() -> int:
    """按本脚本前缀清理全部演示任务与工作流（跨 plan_id），返回清理条数。

    任务按 TASK_PREFIX 全局清；工作流按 WF_PREFIX 全局清——重新设计会生成
    新 plan_id（wf_id=bld_wf_<plan_id> 随之变化），只删当前 plan 的工作流
    会残留旧 plan 工作流（其节点指向已被删的任务，成为悬空引用）。
    """
    removed = 0
    for t in _list_all("task", "tasks"):
        if (t["task_id"] or "").startswith(TASK_PREFIX):
            _dwcli("task", "delete", "--id", t["task_id"])
            removed += 1
    for wf in _list_all("workflow", "workflows"):
        if (wf["workflow_id"] or "").startswith(WF_PREFIX):
            _dwcli("workflow", "delete", "--id", wf["workflow_id"])
            removed += 1
    return removed


def seed(plan_id: str, do_publish: bool = True, do_run: bool = True) -> dict | None:
    plan, tables = _read_plan(plan_id)
    if not plan:
        raise RuntimeError(f"plan {plan_id} not found")
    biz_db = plan.get("biz_database")
    if not biz_db:
        raise RuntimeError(f"plan {plan_id} 缺少 biz_database，请检查 plan 记录")
    wf_id = f"{WF_PREFIX}{plan_id}"
    # 调度取自 plan（设计阶段由用户输入决定），缺省每天 02:00
    schedule_cron = str(plan.get("schedule_cron") or "0 2 * * *").strip() or "0 2 * * *"

    # 先清旧（本脚本前缀，跨 plan_id 全局清，见 _cleanup_builder_assets）
    _cleanup_builder_assets()

    # 1. prepare CSV（让 CSV 导入任务能读到真实文件）+ 确保业务库存在
    csv_dir = _ensure_csv(plan)
    try:
        source_config = json.loads(plan["source_config"] or "{}")
    except json.JSONDecodeError:
        source_config = {}
    csv_files = {Path(f).stem: f for f in (source_config.get("files") or [])}
    file_options = source_config.get("file_options") or {}
    _ensure_biz_db(biz_db)

    # 2. 建任务
    task_by_short: dict[str, str] = {}  # table_short → task_id
    created_task_ids: list[str] = []
    node_by_short: dict[str, str] = {}  # table_short → 数据节点 node_id（imp/etl）
    nodes = []
    for t in tables:
        short = _table_short(t["table_name"])
        layer = t["layer"]
        ddl_sql = t.get("ddl_sql") or ""
        etl_sql = t.get("etl_sql") or ""
        # DDL 任务（sqlite 后端统一走 SQLite 翻译；doris 保持原 DDL）
        ddl_tid = f"{TASK_PREFIX}ddl_{short}"
        _task_create({
            "task_id": ddl_tid,
            "name": f"{layer}CREATE TABLE-{short}",
            "description": f"{layer} 层建表 DDL：{short}",
            "executor_type": "sql",
            "code": _prep_ddl(ddl_sql, short),
            "datasource_ref": _biz_ref_for(WORKSPACE_ID[0]),
            "timeout_s": 600,
            "sla_minutes": 5,
            "layer": layer,
            "output_table": short,
        })
        created_task_ids.append(ddl_tid)
        ddl_node = f"node_ddl_{short}"
        nodes.append({
            "node_id": ddl_node,
            "task_id": ddl_tid,
            "node_name": f"CREATE TABLE-{short}",
            "group_name": layer,
            "order_index": {"ODS": 0, "DWD": 1, "DWS": 2, "ADS": 3}.get(layer, 3),
            "upstream_nodes": "",
            "wait_review": False,
        })
        # 数据任务：ODS=CSV 导入(Python)，分层=ETL(SQL)
        if layer == "ODS":
            imp_tid = f"{TASK_PREFIX}imp_{short}"
            # 找到该 ODS 表对应的 CSV 文件名（fuzzy 匹配：ods_customer→olist_customers_dataset.csv）
            csv_file = _match_csv_file(short, csv_files)
            if not csv_file:
                # source_tables 里也可能直接是 csv 文件名
                srcs = t.get("source_tables") or []
                if isinstance(srcs, str):
                    try:
                        srcs = json.loads(srcs)
                    except (json.JSONDecodeError, TypeError):
                        srcs = []
                csv_file = next(
                    (str(s) for s in srcs
                     if str(s).lower().endswith((".csv", ".xlsx", ".xlsm", ".xls"))),
                    None,
                )
            if not csv_file:
                raise RuntimeError(f"ODS 表 {short} 找不到对应 CSV 文件（source_tables={t.get('source_tables')}）")
            opts = file_options.get(csv_file) or {}
            if _biz_is_duckdb(WORKSPACE_ID[0]):
                # duckdb：单条 CTAS SQL 直跑（sql executor 在执行进程内复用共享
                # warehouse 连接；python 子进程壳会跨进程锁冲突，见
                # _build_duckdb_ctas_sql docstring）
                imp_code = _build_duckdb_ctas_sql(
                    csv_dir, csv_file, short,
                    delimiter=str(opts.get("delimiter") or ","),
                    encoding=str(opts.get("encoding") or "utf-8-sig"),
                )
                imp_executor = "sql"
                imp_timeout = 600
            else:
                imp_code = _build_csv_import_code(
                    csv_dir, csv_file, short, ddl_sql, biz_db,
                    delimiter=str(opts.get("delimiter") or ","),
                    encoding=str(opts.get("encoding") or "utf-8-sig"),
                )
                imp_executor = "python"
                imp_timeout = 1800
            _task_create({
                "task_id": imp_tid,
                "name": f"ODS LOAD-{short}",
                "description": f"CSV 导入：{csv_file} → {short}",
                "executor_type": imp_executor,
                "code": imp_code,
                "datasource_ref": _biz_ref_for(WORKSPACE_ID[0]),
                "timeout_s": imp_timeout,
                "sla_minutes": 15,
                "layer": layer,
                "output_table": short,
            })
            created_task_ids.append(imp_tid)
            imp_node = f"node_imp_{short}"
            nodes.append({
                "node_id": imp_node,
                "task_id": imp_tid,
                "node_name": f"LOAD-{short}",
                "group_name": layer,
                "order_index": {"ODS": 0, "DWD": 1, "DWS": 2, "ADS": 3}.get(layer, 3),
                "upstream_nodes": ddl_node,
                "wait_review": False,
            })
            task_by_short[short] = imp_tid
            node_by_short[short] = imp_node
        else:
            _assert_etl_aggregate_output(etl_sql, short)
            etl_sql_final = _translate_etl_sql(etl_sql, ddl_sql)
            _dry_run_etl_sql(etl_sql_final, short)
            etl_tid = f"{TASK_PREFIX}etl_{short}"
            _task_create({
                "task_id": etl_tid,
                "name": f"{layer}ETL-{short}",
                "description": f"{layer} 层 ETL：灌数 {short}（源：{t.get('source_tables')}）",
                "executor_type": "sql",
                "code": etl_sql_final,
                "datasource_ref": _biz_ref_for(WORKSPACE_ID[0]),
                "timeout_s": 600,
                "sla_minutes": 5,
                "layer": layer,
                "output_table": short,
            })
            created_task_ids.append(etl_tid)
            # 源表对应的数据节点（DWD 源 ODS → imp；DWS 源 DWD → etl；ADS 源 DWS → etl）
            src_nodes = [node_by_short[s] for s in _parse_source_shorts(t.get("source_tables")) if s in node_by_short]
            etl_node = f"node_etl_{short}"
            nodes.append({
                "node_id": etl_node,
                "task_id": etl_tid,
                "node_name": f"ETL-{short}",
                "group_name": layer,
                "order_index": {"ODS": 0, "DWD": 1, "DWS": 2, "ADS": 3}.get(layer, 3),
                "upstream_nodes": ",".join([ddl_node] + src_nodes),
                "wait_review": False,
            })
            task_by_short[short] = etl_tid
            node_by_short[short] = etl_node

    # 3. 建工作流 + 节点
    _workflow_create({
        "workflow_id": wf_id,
        "name": "数仓分层建仓工作流",
        "description": f"ODS→DWD→DWS→ADS 分层 DAG：{len(nodes)} 个任务节点（plan {plan_id}）",
        "schedule_type": "cron",
        "schedule_cron": schedule_cron,
    })
    _replace_nodes(wf_id, nodes)
    print(f"[workflow] {wf_id}  ({len(nodes)} 节点分层 DAG, cron={schedule_cron})")

    if not do_publish:
        print(f"\n任务+工作流就绪（未 publish）：{len(task_by_short)} 任务，{wf_id}")
        return None

    # 4. publish + 构建时落库血缘（plan §3.3-B）
    for tid in created_task_ids:
        _dwcli("task", "publish", "--id", tid)
    _dwcli("workflow", "publish", "--id", wf_id)
    print(f"\n数仓建仓任务化就绪：{len(task_by_short)} 任务 + 1 分层工作流（已 publish，血缘已落库）")

    if not do_run:
        print("下一步：python plan_to_tasks.py --plan-id %s（执行工作流）" % plan_id)
        return None

    # 5. 同步执行工作流（debug，阻塞等返回）
    print(f"\n同步执行工作流 {wf_id} ...")
    result, runs = _debug_and_wait(wf_id)
    print(f"[instance] {result['instance_id']} status={result['status']} error={result.get('error_message')}")
    # 各节点状态
    ok = sum(1 for r in runs if r["status"] == "success")
    failed = [r for r in runs if r["status"] not in ("success", "skipped")]
    print(f"[runs] success={ok}/{len(runs)}")
    for r in failed:
        print(f"  [failed] {r['node_name']} ({r.get('error_message')})")
    result["_instance_id"] = result["instance_id"]
    result["_runs"] = runs
    return result


def _collect_existing_node_map(wf_id: str) -> dict[str, str]:
    """读已有工作流节点，返回 short_table_name → data node_id 映射。

    从 task_id 反推 short：bld_imp_xxx / bld_etl_xxx → xxx。
    """
    workflow = _dwcli("workflow", "get", "--id", wf_id)
    nodes = workflow.get("nodes") or []
    mapping: dict[str, str] = {}
    for n in nodes:
        tid = n.get("task_id") or ""
        for prefix in (f"{TASK_PREFIX}imp_", f"{TASK_PREFIX}etl_"):
            if tid.startswith(prefix):
                short = tid[len(prefix):]
                mapping[short] = n["node_id"]
                break
    return mapping


def _resolve_source_node_ids(source_tables_json: str | list, node_by_short: dict[str, str]) -> list[str]:
    """从 source_tables（JSON list 或 Python list）解析出已有数据节点的 node_id 列表。"""
    if isinstance(source_tables_json, str):
        try:
            sources = json.loads(source_tables_json)
        except (json.JSONDecodeError, TypeError):
            sources = []
    else:
        sources = source_tables_json or []
    out = []
    for s in sources:
        short = s.split(".")[-1] if "." in s else s
        if short in node_by_short:
            out.append(node_by_short[short])
    return out


def append(plan_id: str, table_specs: list[dict], do_publish: bool = True, do_run: bool = True) -> dict:
    """增量追加：向已有工作流添加新表的任务 + 节点 + 血缘，不删旧数据。

    table_specs: [
        {
            "table_name_short": "dws_user_rfm_1d",
            "layer": "DWS",
            "ddl_sql": "CREATE TABLE IF NOT EXISTS ...",
            "etl_sql": "INSERT INTO ... SELECT ...",
            "source_tables": ["olist.dwd_order_detail"],  # fqn 或短表名
        },
        ...
    ]

    读取现有节点后经 workflow nodes API 整体保存；不删除已有任务/节点/血缘。
    """
    plan, existing_tables = _read_plan(plan_id)
    if not plan:
        raise RuntimeError(f"plan {plan_id} not found")
    biz_db = plan.get("biz_database")
    if not biz_db:
        raise RuntimeError(f"plan {plan_id} 缺少 biz_database，请检查 plan 记录")
    wf_id = f"{WF_PREFIX}{plan_id}"

    # 确认工作流存在
    wf = _workflow_get(wf_id)
    if not wf:
        raise RuntimeError(f"workflow {wf_id} not found — 先用 seed 创建")

    # 1. 读已有节点映射（short → data node_id）
    node_by_short = _collect_existing_node_map(wf_id)

    # 2. 为每张新表建任务 + 节点
    new_task_by_short: dict[str, str] = {}
    new_node_ids: list[str] = []
    new_ddl_tids: list[str] = []
    task_ids = {t.get("task_id") for t in _list_all("task", "tasks")}
    for spec in table_specs:
        short = spec["table_name_short"]
        layer = spec["layer"]
        ddl_sql = spec.get("ddl_sql") or ""
        etl_sql = spec.get("etl_sql") or ""
        source_tables = spec.get("source_tables") or []

        # 跳过已存在的任务
        existing_ddl = f"{TASK_PREFIX}ddl_{short}" in task_ids
        existing_etl = f"{TASK_PREFIX}etl_{short}" in task_ids
        existing_imp = f"{TASK_PREFIX}imp_{short}" in task_ids
        if existing_ddl or existing_etl or existing_imp:
            print(f"[skip] {short}: 任务已存在")
            continue

        # DDL 任务
        ddl_tid = f"{TASK_PREFIX}ddl_{short}"
        _task_create({
            "task_id": ddl_tid,
            "name": f"{layer}CREATE TABLE-{short}",
            "description": f"{layer} 层建表 DDL：{short}",
            "executor_type": "sql",
            "code": _prep_ddl(ddl_sql, short),
            "datasource_ref": _biz_ref_for(WORKSPACE_ID[0]),
            "timeout_s": 600,
            "sla_minutes": 5,
            "layer": layer,
            "output_table": short,
        })
        task_ids.add(ddl_tid)
        new_ddl_tids.append(ddl_tid)
        ddl_node = f"node_ddl_{short}"
        new_nodes = (wf.get("nodes") or []) + [{
            "node_id": ddl_node,
            "task_id": ddl_tid,
            "node_name": f"CREATE TABLE-{short}",
            "group_name": layer,
            "order_index": {"ODS": 0, "DWD": 1, "DWS": 2, "ADS": 3}.get(layer, 3),
            "upstream_nodes": "",
            "wait_review": False,
        }]
        new_node_ids.append(ddl_node)

        # ETL 任务（DWS/DWD/ADS 统一走 etl；ODS 追加场景走 imp，此处暂只处理分层表）
        etl_tid = f"{TASK_PREFIX}etl_{short}"
        src_node_ids = _resolve_source_node_ids(source_tables, node_by_short)
        etl_sql_final = _translate_etl_sql(etl_sql)
        _dry_run_etl_sql(etl_sql_final, short)
        _task_create({
            "task_id": etl_tid,
            "name": f"{layer}ETL-{short}",
            "description": f"{layer} 层 ETL：灌数 {short}（源：{source_tables}）",
            "executor_type": "sql",
            "code": etl_sql_final,
            "datasource_ref": _biz_ref_for(WORKSPACE_ID[0]),
            "timeout_s": 600,
            "sla_minutes": 5,
            "layer": layer,
            "output_table": short,
        })
        task_ids.add(etl_tid)
        etl_node = f"node_etl_{short}"
        new_nodes.append({
            "node_id": etl_node,
            "task_id": etl_tid,
            "node_name": f"ETL-{short}",
            "group_name": layer,
            "order_index": {"ODS": 0, "DWD": 1, "DWS": 2, "ADS": 3}.get(layer, 3),
            "upstream_nodes": ",".join([ddl_node] + src_node_ids),
            "wait_review": False,
        })
        wf["nodes"] = _replace_nodes(wf_id, new_nodes)
        new_node_ids.append(etl_node)
        new_task_by_short[short] = etl_tid
        node_by_short[short] = etl_node  # 同批次后续表可能依赖此节点

    # 3. 同步 plan：经 plan API 保存完整方案，禁止直写 plan_table。
    known = {str(table.get("table_name") or "") for table in existing_tables}
    merged_tables = list(existing_tables)
    for spec in table_specs:
        fq_name = f"{biz_db}.{spec['table_name_short']}"
        if fq_name not in known:
            merged_tables.append({
                "table_name": fq_name,
                "layer": spec["layer"],
                "ddl_sql": spec.get("ddl_sql", ""),
                "etl_sql": spec.get("etl_sql", ""),
                "source_tables": spec.get("source_tables", []),
                "status": "designed",
            })
    _save_plan(plan_id, plan, merged_tables)

    if not do_publish:
        print(f"\n增量追加就绪（未 publish）：{len(new_task_by_short)} 新任务，工作流 {wf_id}")
        return {"appended": len(new_task_by_short), "workflow": wf_id}

    # 4. publish 新任务 + 构建时血缘
    for short, tid in new_task_by_short.items():
        _dwcli("task", "publish", "--id", tid)
    for tid in new_ddl_tids:
        _dwcli("task", "publish", "--id", tid)
    print(f"\n增量追加就绪：{len(new_task_by_short)} 新任务（已 publish，血缘已落库）")

    if not do_run:
        print(f"下一步：手动触发工作流 {wf_id} 执行新增节点")
        return {"appended": len(new_task_by_short), "workflow": wf_id}

    # 5. 只执行新增节点（不重跑全量工作流）
    print(f"\n执行新增节点（{len(new_node_ids)} 个）...")
    result, runs = _debug_and_wait(wf_id, only_nodes=new_node_ids)
    print(f"[instance] {result['instance_id']} status={result['status']} error={result.get('error_message')}")
    ok = sum(1 for r in runs if r["status"] == "success")
    failed = [r for r in runs if r["status"] not in ("success", "skipped")]
    print(f"[runs] success={ok}/{len(runs)}")
    for r in failed:
        print(f"  [failed] {r['node_name']} ({r.get('error_message')})")
    result["_instance_id"] = result["instance_id"]
    result["_appended"] = len(new_task_by_short)
    return result


def clean(plan_id: str) -> None:
    # 删前收集 task_id（删除后 list_tasks 已空，血缘清理要用）
    task_ids = [t["task_id"] for t in _list_all("task", "tasks")
                if (t["task_id"] or "").startswith(TASK_PREFIX)]
    _cleanup_builder_assets()
    # 清血缘同样经 API，避免 skill 直接写元数据库。
    for tid in task_ids:
        body = {"plan_id": tid, "task_id": tid}
        _dwcli("lineage", "table-delete", "--json-file", "-", stdin_json=body)
        _dwcli("lineage", "column-delete", "--json-file", "-", stdin_json=body)
    print("cleaned builder tasks/workflow/lineage")


def main() -> int:
    ap = argparse.ArgumentParser(description="dw-warehouse-build 任务化桥接：plan → 任务 + 分层工作流")
    ap.add_argument("--plan-id", required=True)
    ap.add_argument("--action", choices=["seed", "append", "clean"], default="seed",
                    help="seed=全量建(默认); append=增量追加; clean=清理")
    ap.add_argument("--table-spec", type=str, default=None,
                    help="append 模式：JSON 数组，每项含 table_name_short/layer/ddl_sql/etl_sql/source_tables")
    ap.add_argument("--table-spec-file", type=str, default=None,
                    help="append 模式：JSON 文件路径，读取后等同 --table-spec（避免 shell 转义问题，推荐）")
    ap.add_argument("--no-run", action="store_true", help="只建任务+工作流+publish，不执行")
    ap.add_argument("--no-publish", action="store_true", help="只建任务+工作流，不 publish 不执行")
    ap.add_argument("--clean", action="store_true", help="清演示任务/工作流/血缘（等同 --action clean）")
    ap.add_argument("--workspace-id", dest="workspace_id", default=None,
                    help="工作空间 ID；仅限上层编排显式指定其他工作空间时使用，会话场景禁止传（覆盖 env 即覆盖用户选择）")
    args = ap.parse_args()

    # 多工作空间（P4-4）：执行时解析业务库/元库，不再 import 固化
    WORKSPACE_ID[0] = (args.workspace_id
                       or os.environ.get("JIUWENSWARM_WORKSPACE_ID", "")).strip()
    if WORKSPACE_ID[0]:
        # plan_to_tasks 内部还会启动 dwcli 子进程；显式参数必须写回 env 才能继续透传。
        os.environ["JIUWENSWARM_WORKSPACE_ID"] = WORKSPACE_ID[0]
    try:
        _biz_ref_for(WORKSPACE_ID[0])  # 提前大声失败（多工作空间缺上下文）
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 业务库解析失败: {exc}", file=sys.stderr)
        return 1

    action = "clean" if args.clean else args.action

    if action == "clean":
        clean(args.plan_id)
    elif action == "append":
        spec_str = args.table_spec
        if not spec_str and args.table_spec_file:
            try:
                spec_str = Path(args.table_spec_file).read_text(encoding="utf-8")
            except OSError as e:
                print(f"错误：--table-spec-file 读取失败: {e}", file=sys.stderr)
                return 1
        if not spec_str:
            print("错误：append 模式需要 --table-spec 或 --table-spec-file 参数", file=sys.stderr)
            return 1
        try:
            table_specs = json.loads(spec_str)
        except json.JSONDecodeError as e:
            print(f"错误：--table-spec JSON 解析失败: {e}", file=sys.stderr)
            return 1
        if not isinstance(table_specs, list):
            print("错误：--table-spec 必须是 JSON 数组", file=sys.stderr)
            return 1
        result = append(args.plan_id, table_specs,
                        do_publish=not args.no_publish,
                        do_run=not args.no_run and not args.no_publish)
        if isinstance(result, dict):
            print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        seed(args.plan_id, do_publish=not args.no_publish, do_run=not args.no_run and not args.no_publish)
    return 0


if __name__ == "__main__":
    sys.exit(main())
