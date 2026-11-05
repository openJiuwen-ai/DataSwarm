#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""finding_identity.py —— finding 身份契约 v1（dw-governance 共享契约）。

背景：检查器的 finding.id（NUL-001/UNI-001…）在每个维度/每张表内重新计数，
不是 run 内唯一键，不能用于前端勾选、fix --confirm-items、API 定位或数据库主键。

本模块生成三个字段，由 run_pipeline._normalize_artifact 写进每条 finding：

    identity_version   算法版本（v1）；读取方支持版本低于此值时必须报错，不静默重算
    source_finding_id  原始检查器 ID，仅展示/排障
    finding_uid        run 内唯一且确定性：<dim>:<category>:<subject...>:<source_id>:<hash16>
                       输入含无凭据 db_identity/dimension/category/规范化 subject/原始 ID；
                       内容碰撞时叠加 evidence+detail 的稳定内容摘要（不用数组下标）
    finding_fingerprint  跨 run 稳定：<hash16 of>
                       {identity_version, db_identity, dimension, category,
                        rule_family, subject}
                       不含 severity/summary/evidence/时间戳/run_id；
                       含 db_identity —— 不同库的同名表不会得到相同 fingerprint

规则变更即破坏 UID 兼容：任何输入集合调整都必须升 IDENTITY_VERSION 并保留旧实现。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from digest import canonical_json, sha256_hex  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

IDENTITY_VERSION = 1

# subject 白名单：允许进入身份字段的定位器。新增字段=扩白名单并升版本。
_SUBJECT_KEYS = (
    "table", "column", "ref_table", "index", "pk_columns",
    "task_id", "metric_id", "workflow_id", "instance_id", "time_col",
)
# uid 可读段取的定位顺序（前 3 个值）
_UID_LOC_KEYS = ("table", "column", "task_id", "metric_id", "workflow_id")

_RULE_FAMILY_RE = re.compile(r"^([A-Za-z]+)-")


# =========================================================================
# db-ref 脱敏身份
# =========================================================================
def _metadata_database_name() -> str:
    """config metadata_db 解析出的元数据库名（用于 :meta 指纹判定）。

    指针化形态（doris://data_swarm）与 meta 标记形态（doris://meta）解析出的
    database 相同，指纹保持一致，历史 findings 不失配。
    """
    try:
        from dw_client import metadata_db_ref, parse_db_ref, resolve_workspace_id
        ref = metadata_db_ref(resolve_workspace_id())
        if ref:
            return parse_db_ref(ref).get("database") or ""
    except Exception:
        pass
    return ""


def _spec_to_identity(spec: dict) -> str:
    """从已解析 spec 构造无凭据身份串：<type>@<host>:<port>/<database>[:meta]。

    :meta 判定双通道：is_metadata 标记（doris://meta 旧形态）或 database 名
    等于 config 元数据库名（doris://dbname 指针化形态）——两种写法同一指纹。
    """
    t = spec.get("type", "?")
    if t == "sqlite":
        # 绝对路径规整到统一形态（Windows 盘符大小写/分隔符差异消除）
        p = Path(spec.get("path", "")).expanduser()
        norm = os.path.normcase(str(p.resolve()))
        return f"sqlite:{norm}"
    host = spec.get("host") or ""
    port = spec.get("port") or ""
    db = spec.get("database") or ""
    is_meta = bool(spec.get("is_metadata")) or (
        bool(db) and db == _metadata_database_name()
    )
    meta = ":meta" if is_meta else ""
    return f"{t}@{host}:{port}/{db}{meta}"


def db_identity(db_ref: str) -> str:
    """db-ref → 无凭据的身份标识。

    strip 用户名/密码/query 参数；doris://meta 等 shorthand 解析出真实库。
    连接器不可用时退化为字符串级剥离（保底，测试环境用）。
    """
    raw = (db_ref or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        return f"sqlite:{os.path.normcase(str(Path(raw).expanduser().resolve()))}"
    scheme, rest = raw.split("://", 1)
    if scheme == "sqlite":
        rest = rest[1:] if len(rest) >= 3 and rest[0] == "/" and rest[2] == ":" else rest
        return f"sqlite:{os.path.normcase(str(Path(rest).expanduser().resolve()))}"
    # doris/mysql：优先走连接器解析（处理 doris://meta 与 config 补全）
    try:
        from dw_client import parse_db_ref as _client_parse  # 经 dwcli 服务端解析
        return _spec_to_identity(_client_parse(raw))
    except Exception:
        pass
    # 字符串级兜底：剥凭据 user:pwd@ 与 query 参数
    body = rest.split("?", 1)[0]
    if "@" in body:
        body = body.split("@", 1)[1]
    return f"{scheme}@{body}"


# =========================================================================
# subject 规范化
# =========================================================================
def _sanitize_token(v: Any) -> str:
    s = str(v)
    s = re.sub(r"[\s:|]+", "_", s).strip("_")
    return s[:80]


def normalize_subject(obj: dict | None) -> dict:
    """finding.object → 白名单定位器（键序固定；pk_columns 排序）。"""
    if not isinstance(obj, dict):
        return {}
    out: dict = {}
    for k in _SUBJECT_KEYS:
        if k not in obj or obj[k] in (None, "", []):
            continue
        v = obj[k]
        if isinstance(v, list):
            out[k] = sorted(_sanitize_token(x) for x in v)
        else:
            out[k] = _sanitize_token(v)
    return out


def _uid_loc_segments(subject: dict) -> list[str]:
    segs: list[str] = []
    for k in _UID_LOC_KEYS:
        v = subject.get(k)
        if v is None:
            continue
        vals = v if isinstance(v, list) else [v]
        for item in vals:
            if len(segs) >= 3:
                return segs
            segs.append(_sanitize_token(item))
    return segs


def _rule_family(source_id: str) -> str:
    m = _RULE_FAMILY_RE.match(source_id or "")
    return (m.group(1) if m else (source_id or ""))[:32]


# =========================================================================
# UID / fingerprint
# =========================================================================
def make_finding_uid(*, dimension: str, category: str, subject: dict,
                     source_finding_id: str, db_identity_str: str,
                     content_extra: Any = None) -> str:
    """run 内唯一 UID。content_extra 用于碰撞兜底（传 evidence/detail 摘要负载）。"""
    payload = {
        "v": IDENTITY_VERSION,
        "db": db_identity_str,
        "dim": dimension,
        "cat": category,
        "subject": subject,
        "sid": source_finding_id,
    }
    if content_extra is not None:
        payload["extra"] = content_extra
    digest = sha256_hex(canonical_json(payload))
    locs = _uid_loc_segments(subject) or ["na"]
    parts = [_sanitize_token(x) for x in (dimension, category, *locs, source_finding_id)]
    return ":".join([*parts, digest])


def make_finding_fingerprint(*, dimension: str, category: str, subject: dict,
                             source_finding_id: str, db_identity_str: str) -> str:
    """跨 run 稳定指纹：规则家族 + 目标 + 库身份；不含 severity/evidence/时间戳/run_id。"""
    payload = {
        "v": IDENTITY_VERSION,
        "db": db_identity_str,
        "dim": dimension,
        "cat": category,
        "rule": _rule_family(source_finding_id),
        "subject": subject,
    }
    return sha256_hex(canonical_json(payload))


# =========================================================================
# 批量标注（run_pipeline._normalize_artifact 调用入口）
# =========================================================================
def annotate_findings(findings: list[dict], *, dimension: str,
                      db_ref: str, id_key: str = "id") -> list[dict]:
    """为一批同维度 findings 补身份四字段。返回新列表，不原地改。

    同批内 UID 冲突时逐级叠入稳定消解因子：重复序号 n → 内容摘要 → 组合；
    全部尝试仍冲突则显式报错（身份算法 bug，宁可炸不给静默错 ID）。
    """
    out: list[dict] = []
    emitted: set[str] = set()
    dup_counter: dict[str, int] = {}
    for f in findings:
        fid = str(f.get(id_key) or "")
        cat = str(f.get("category") or "")
        subj = normalize_subject(f.get("object"))
        n = dup_counter.get(fid, 0)
        dup_counter[fid] = n + 1
        content_digest = sha256_hex(canonical_json(
            {"ev": f.get("evidence"), "dt": f.get("detail")}))
        ident = _lazy_ident(db_ref)

        base = dict(f)
        base["identity_version"] = IDENTITY_VERSION
        base["source_finding_id"] = fid

        def build(extra: Any) -> str:
            return make_finding_uid(dimension=dimension, category=cat, subject=subj,
                                    source_finding_id=fid,
                                    db_identity_str=ident, content_extra=extra)

        # 消解因子梯度：无 → 重复序号 → 内容摘要 → 组合。下标永不进入 UID 输入。
        uid = build(None)
        if uid in emitted:
            for extra in ({"dup_n": n}, {"cd": content_digest},
                          {"dup_n": n, "cd": content_digest}):
                cand = build(extra)
                if cand not in emitted:
                    uid = cand
                    break
            else:
                raise ValueError(f"finding_uid 碰撞无法消解: fid={fid} dim={dimension}")
        emitted.add(uid)
        base["finding_uid"] = uid
        base["finding_fingerprint"] = make_finding_fingerprint(
            dimension=dimension, category=cat, subject=subj,
            source_finding_id=fid, db_identity_str=ident)
        out.append(base)
    return out


_IDENT_CACHE: dict[str, str] = {}


def _lazy_ident(db_ref: str) -> str:
    if db_ref not in _IDENT_CACHE:
        _IDENT_CACHE[db_ref] = db_identity(db_ref)
    return _IDENT_CACHE[db_ref]
