#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""capability.py —— 库/表能力三态建模 v1（dw-governance 共享契约）。

回答一个问题：**对这张表，这个操作现在能不能做**。每个操作返回：

    {"state": "supported" | "unsupported" | "unknown",
     "reason": "...",                       # 面向人展示（UI/修复方案说明）
     "evidence": {...}}                     # 判定依据快照（version/key_model/MoW…）

三态语义：
    supported   数据库明确支持，允许自动执行（仍受 risk 分级约束）
    unsupported 数据库明确不支持（如 DUPLICATE KEY 表 UPDATE），只出人工方案
    unknown     探测失败/规则未覆盖 —— 默认按不支持处理并提示补 probe

探测注入：compute_capability 的所有 DB 读取都走 `probes` 参数（show_create /
version / engine 等惰性取值函数），单测可注入假数据离线验证；默认实现接 db_connector。
判定输入集合变化 = 契约变更 → 升 CAPABILITY_SCHEMA_VERSION。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, Path(__file__).resolve().parent)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

CAPABILITY_SCHEMA_VERSION = 1


def op(state: str, reason: str = "", evidence: dict | None = None) -> dict:
    """构造单个操作的 capability 三态项。state 越界即抛错防静默错标。"""
    if state not in ("supported", "unsupported", "unknown"):
        raise ValueError(f"非法 capability state: {state!r}")
    return {"state": state, "reason": reason or state,
            "evidence": evidence or {}}


_KEY_MODEL_RE = re.compile(r"\b(UNIQUE|AGGREGATE|DUPLICATE)\s+KEY\b", re.I)
_MOW_RE = re.compile(r'"enable_unique_key_merge_on_write"\s*=\s*"(true)"', re.I)


def parse_key_model(show_create_sql: str) -> tuple[str | None, bool]:
    """SHOW CREATE TABLE 文本 → (key_model, merge_on_write)。"""
    m = _KEY_MODEL_RE.search(show_create_sql or "")
    model = m.group(1).lower() if m else None
    mow = bool(_MOW_RE.search(show_create_sql or ""))
    return model, mow


def _norm_version(v: str | None) -> tuple[int, ...] | None:
    if not v:
        return None
    m = re.search(r"(\d+(?:\.\d+)+)", str(v))
    if not m:
        return None
    try:
        return tuple(int(x) for x in m.group(1).split("."))
    except ValueError:
        return None


def compute_capability(db_ref: str, table: str, *,
                       probes: dict[str, Callable[[], Any]] | None = None,
                       connector: Any = None) -> dict:
    """计算某表 capability。所有探测经 probes 注入；缺省实现连真实库。

    probes 键：
      show_create: () -> str            SHOW CREATE TABLE 全文
      version:     () -> str            SELECT VERSION()
      engine:      () -> str            mysql 存储引擎名（sqlite/doris 可缺省）
    探测函数抛异常 = unknown（不静默当 unsupported——两者 reason 不同）。
    """
    p = dict(probes or {})
    db_type = _detect_db_type(db_ref, connector)

    def call(key: str) -> tuple[Any, bool]:
        fn = p.get(key)
        if fn is None:
            fn = _default_probe(db_ref, table, key, connector)
        try:
            return fn(), True
        except Exception as exc:  # noqa: BLE001 —— 探测失败必须被看见
            return exc, False

    show_create, ok_sc = call("show_create")
    version_raw, ok_ver = call("version")

    ver = _norm_version(version_raw if ok_ver else None)
    evidence_common: dict = {"db_type": db_type}
    if ok_ver:
        evidence_common["version"] = str(version_raw)

    cap: dict = {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "db_type": db_type,
        "table": table,
        "ops": {},
    }

    if db_type == "doris":
        model, mow = ((None, False) if not ok_sc else parse_key_model(str(show_create)))
        evidence_common.update({"key_model": model, "merge_on_write": mow})
        if not ok_sc:
            # DDL 读不到 → 表模型未知，必须 unknown（不伪装成明确不支持）
            unk = op("unknown", "SHOW CREATE TABLE 不可读，表模型未知", evidence_common)
            upd, del_cond = unk, unk
        else:
            dml_ok = (model == "unique") and mow
            upd = op("supported" if dml_ok else "unsupported",
                     "UNIQUE KEY + MoW" if dml_ok
                     else f"Doris {model} 不支持 UPDATE"
                          f"{'' if mow or model != 'unique' else '（缺 MoW）'}",
                     evidence_common)
            del_cond = op("supported" if dml_ok else "unsupported",
                          "UNIQUE KEY + MoW" if dml_ok
                          else f"Doris {model} 条件 DELETE 受限",
                          evidence_common)
        cap["ops"] = {
            "update": upd,
            "delete_condition": del_cond,
            "insert_overwrite": (
                op("supported", "Doris >=2.0 INSERT OVERWRITE", evidence_common)
                if (ver or (0,)) >= (2, 0)
                else op("unknown", "版本无法解析或 <2.0，INSERT OVERWRITE 不确定",
                        evidence_common)),
            "add_column_auto": op(
                "supported" if ok_sc else "unknown",
                "light schema change ADD COLUMN" if ok_sc else "DDL 不可读",
                evidence_common),
            "modify_type": op("unsupported", "类型变更需人工（重写数据段）", evidence_common),
            "add_index": op("supported", "CREATE INDEX", evidence_common),
            "add_unique_key": op("unsupported", "唯一约束仅建表时可定", evidence_common),
        }
        return cap

    if db_type == "mysql":
        engine_raw, ok_eng = call("engine")
        eng = str(engine_raw).lower() if ok_eng else ""
        ev = {**evidence_common, "engine": eng if ok_eng else "probe-failed"}
        innodb = eng == "innodb"
        cap["ops"] = {
            "update": op("supported" if innodb else "unknown",
                         "InnoDB 支持 UPDATE" if innodb else "非 InnoDB/引擎未知", ev),
            "delete_condition": op("supported" if innodb else "unknown",
                                   "InnoDB 支持条件 DELETE" if innodb else "非 InnoDB/引擎未知", ev),
            "insert_overwrite": op("unsupported", "MySQL 无 INSERT OVERWRITE", ev),
            "add_column_auto": op("supported", "ALTER TABLE ADD COLUMN", ev),
            "modify_type": op("supported", "ALTER TABLE MODIFY", ev),
            "add_index": op("supported", "CREATE INDEX", ev),
            "add_unique_key": op("supported", "ADD UNIQUE KEY（锁表风险由 risk 层管）", ev),
        }
        return cap

    # sqlite
    ev = {"db_type": "sqlite"}
    cap["ops"] = {
        "update": op("supported", "SQLite 支持 UPDATE", ev),
        "delete_condition": op("supported", "SQLite 支持条件 DELETE", ev),
        "insert_overwrite": op("unsupported", "SQLite 无 INSERT OVERWRITE", ev),
        "add_column_auto": op("supported", "ALTER TABLE ADD COLUMN", ev),
        "modify_type": op("unsupported", "改类型需重建表", ev),
        "add_index": op("supported", "CREATE INDEX", ev),
        "add_unique_key": op("unsupported", "仅建表时定义", ev),
    }
    return cap


def _detect_db_type(db_ref: str, connector: Any) -> str:
    if connector is None:
        try:
            from jiuwenswarm.common import db_connector  # type: ignore  # noqa: E402
            connector = db_connector
        except Exception:
            if "://" in db_ref:
                return db_ref.split("://", 1)[0]
            return "sqlite"
    return (connector.parse_db_ref(db_ref).get("type") or "sqlite")


def _default_probe(db_ref: str, table: str, key: str, connector: Any):
    """默认探测实现：懒加载连接器，按 key 返回取值闭包（thread-safe：无共享可变态）。"""
    if connector is None:
        from jiuwenswarm.common import db_connector as connector  # type: ignore  # noqa: E402
    if key == "show_create":
        return lambda: connector.show_create_table(db_ref, table)["create_sql"]
    if key == "version":
        def _probe_version():
            # 实测：Doris MySQL 兼容层 VERSION() 恒返回 5.7.x，不代表真实内核版本。
            # 优先取非"纯 x.y.z"形态的 @@version_comment（云/社区版真实串），回退 VERSION()。
            try:
                rows = connector.query_dict(db_ref, "SELECT @@version_comment AS v", limit=10)
                s = str((rows[0] or {}).get("v") or "").strip()
                if s and not re.fullmatch(r"\d+\.\d+\.\d+\S*", s):
                    return s
            except Exception:
                pass
            rows = connector.query_dict(db_ref, "SELECT VERSION() AS v", limit=10)
            return str((rows[0] or {}).get("v") or "")
        return _probe_version
    if key == "engine":
        return lambda: ""
    raise KeyError(key)
