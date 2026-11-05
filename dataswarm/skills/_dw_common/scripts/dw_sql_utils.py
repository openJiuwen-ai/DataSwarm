#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""dw_sql_utils.py —— skill 侧 db-ref 解析与标识符引号纯函数（无连接能力）。

方案 docs/skill-cli-web-api-plan.md §7.1：连接层 db_connector 上收服务端后，
skill 侧需要的 db-ref 结构解析与标识符引号抽到本文件——纯函数、无网络、
无 import jiuwenswarm，供 skill 侧 import。

与 jiuwenswarm.common.db_connector 的同名函数语义一致（服务端是真相源，
本文件是其纯函数子集的 skill 侧副本，避免 skill 因 SQL 文本处理又引入连接层）。
（占位符适配/只读判定/LIMIT 护栏的镜像副本经全仓核实零消费方，已删——
真相源在服务端 db_connector。）

不提供：connect/query/tables/schema/execute 等任何 IO 函数——这些一律经 dwcli。
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote


def _duckdb_table_name(location: str) -> str:
    """location → 合法 VIEW 名（与服务端 db_connector._duckdb_table_name 一致）。

    单文件取 stem（orders.csv → orders）；目录/S3 取 data。
    非法标识符（数字开头/特殊字符）回退 data，保证 ref 稳定可比较。
    """
    name = ""
    if "://" not in location:
        name = Path(location).stem
    name = re.sub(r"[^A-Za-z0-9_]", "", name or "")
    if not name or not name[0].isalpha():
        return "data"
    return name


def _parse_duckdb_ref(rest: str) -> dict:
    """duckdb ref 的 rest → spec（与服务端 db_connector._parse_duckdb_ref 一致）。

    rest 形如 <quoted-location>?source_type=csv&table_name=x；query 用首个 '?'
    切分（location 内 '?' 已被 quote 转义成 %3F，不会误切）。
    database 字段 = location 文件/目录名，供表召回等展示场景对齐。
    """
    raw_location, _, query = rest.partition("?")
    params: dict[str, str] = {}
    if query:
        for pair in query.split("&"):
            key, _, value = pair.partition("=")
            if key:
                params[key] = unquote(value)
    location = unquote(raw_location)
    if not location:
        raise ValueError("duckdb:// ref 缺少 location")
    source_type = params.get("source_type") or "csv"
    return {
        "type": "duckdb",
        "location": location,
        "source_type": source_type,
        "table_name": params.get("table_name") or _duckdb_table_name(location),
        "database": Path(location).name,
    }


# ==========================================================================
# db-ref 解析（纯字符串，不连库）
# ==========================================================================
def parse_db_ref(db_ref: str) -> dict:
    """解析 db-ref 成统一结构 {type, host, port, user, password, database, path}。

    与服务端 db_connector.parse_db_ref 语义一致，但不读 config 段（skill 侧
    不应直接读凭据）——doris://mysql: 段的补全由服务端做。本函数只做结构解析，
    空 rest 时不补 config，返回空字段（供 skill 拼展示用，不用于真连库）。

    - sqlite://path/to/x.db  或 裸路径 → sqlite
    - doris://host:port/db   → doris
    - mysql://host:port/db   → mysql
    - duckdb://<location>?source_type=csv&table_name=x → 文件/对象存储源
    """
    if not db_ref:
        raise ValueError("db_ref 为空")
    if "://" not in db_ref:
        return {"type": "sqlite", "path": db_ref}
    scheme, rest = db_ref.split("://", 1)
    if scheme == "duckdb":
        return _parse_duckdb_ref(rest)
    if scheme == "sqlite":
        if len(rest) >= 3 and rest[0] == "/" and rest[2] == ":":
            rest = rest[1:]
        return {"type": "sqlite", "path": rest}
    if scheme in ("doris", "mysql"):
        meta_flag = rest.strip() == "meta"
        body = "" if meta_flag else rest
        if body and "/" not in body:
            # doris://dbname（无 / 无 :）：指针自带库名，结构解析直接取
            return {"type": scheme, "host": "", "port": 9030 if scheme == "doris" else 3306,
                    "user": "", "password": "", "database": body}
        if not body or "/" not in body:
            # 空 rest（doris://）：结构解析拿不到库名（config 段补全在 dw_client.parse_db_ref 做）
            return {"type": scheme, "host": "", "port": 9030 if scheme == "doris" else 3306,
                    "user": "", "password": "", "database": "", "is_metadata": meta_flag}
        host_port, _, db = body.partition("/")
        host, _, port = host_port.partition(":")
        return {"type": scheme, "host": host, "port": int(port) if port else 3306,
                "user": "", "password": "", "database": db}
    raise ValueError(f"不支持的 db-ref scheme: {scheme}")


def quote_ident(db_ref: str, name: str) -> str:
    """按库类型给标识符加引号：sqlite/duckdb 用双引号，doris/mysql 用反引号。"""
    db_type = parse_db_ref(db_ref).get("type", "sqlite")
    if db_type in ("sqlite", "duckdb"):
        return f'"{name}"'
    return f"`{name}`"
