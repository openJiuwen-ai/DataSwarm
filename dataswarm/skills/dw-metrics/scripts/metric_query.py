#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""metric_query.py —— dw-metrics skill 唯一脚本（确定性 CLI，V2 语义层契约）。

子命令（全部经 dwcli `metric-v2` 组走 web API，要求 web 服务常驻）:
    list          指标清单（含当前发布修订摘要）
    get           指标详情（修订历史 / 口径 / 候选映射待确认数）
    context       指标语义上下文（概念 / 依赖角色 / 字段，需已发布）
    releases      发布批次列表
    health        语义层健康（含能力协商）
    import-ossie  导入 Ossie 语义模型 → candidate 修订（写操作，需用户确认）
    publish       发布批次（投影到语义图；失败保旧版本）
    confirm       确认 / 拒绝候选业务映射（ratio 的分子分母角色必经此步）
    query         结构化取数（QuerySpec → 服务端确定性编译，结果落 CSV）
    ask           自然语言问答（服务端意图解析；主通路权威，兜底标记非权威）
    feedback      查询反馈（verified 会把权威 SQL 固化进复用池）

设计（主方案 docs 阶段 5/6）:
- 取数与问答结果落 CSV artifact（path-mode），stdout 只回路径 + 计数
- 兜底通路结果必带 authoritative=false 与 degraded_reason，回答时必须向用户标明
- 失败信息原样回传供 Agent 自修正（最多 2 次）
- 导入 / 发布 / 确认是写操作：只在用户明确要求时执行

退出码: 0 成功; 1 参数/校验错误; 2 服务不可达/服务端错误
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# dw_paths：运行时根目录解析（_dw_common/scripts，env 优先）
_DW_COMMON_DIR = (Path(__file__).resolve().parent.parent.parent
                  / "_dw_common" / "scripts")
if str(_DW_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_DW_COMMON_DIR))
import dw_paths  # noqa: E402


for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

ARTIFACT_DIR = Path(os.environ.get(
    "JIUWENSWARM_ARTIFACT_DIR",
    dw_paths.workspace_root() / "artifact" / "metrics",
)).expanduser().resolve()

DWCLI_TIMEOUT = int(os.environ.get("DWCLI_TIMEOUT_S", "600"))


def _dwcli(args: list[str], *, json_body: dict | None = None) -> dict:
    """调 dwcli metric-v2 子命令；json_body 经 stdin 传给 --json-file -。

    subprocess 必须显式 ``encoding="utf-8"``：Windows 下 ``text=True`` 默认
    GBK，dwcli 输出含中文（指标名/口径）时 reader 线程直接 UnicodeDecodeError。
    """
    cmd = [sys.executable, "-m", "jiuwenswarm.cli.dwcli", *args]
    stdin_text = json.dumps(json_body, ensure_ascii=False) if json_body is not None else None
    try:
        proc = subprocess.run(cmd, input=stdin_text, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=DWCLI_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"dwcli 超时（{DWCLI_TIMEOUT}s）") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError(detail[-1] if detail else f"dwcli exit {proc.returncode}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"dwcli 输出非法 JSON: {proc.stdout[:200]}") from e


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _write_csv(rows: list[dict], prefix: str) -> str:
    """结果落 CSV，返回路径（path-mode，M3：绝不内联数据正文）。"""
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = ARTIFACT_DIR / f"{prefix}_{ts}.csv"
    if rows:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        csv_path.write_text("", encoding="utf-8")
    return str(csv_path)


def _run(fn, args: argparse.Namespace) -> int:
    """统一异常出口：RuntimeError → exit 2（错误信息回传供自修正）。"""
    try:
        return fn(args)
    except RuntimeError as e:
        _emit({"error": str(e),
               "hint": "服务端错误信息可回喂：先 get 拿指标定义核对维度/粒度后重试（最多 2 次）"})
        return 2


# =========================================================================
# 只读子命令
# =========================================================================
def cmd_list(_args: argparse.Namespace) -> int:
    payload = _dwcli(["metric-v2", "list"])
    cards = [{
        "metric_key": m.get("metric_key"),
        "name": (m.get("current_revision") or {}).get("display_name") or m.get("name"),
        "status": m.get("status"),
        "unit": (m.get("current_revision") or {}).get("unit"),
        "metric_type": (m.get("current_revision") or {}).get("metric_type"),
        "published": m.get("current_revision") is not None,
    } for m in payload.get("metrics") or []]
    _emit({"count": len(cards), "metrics": cards})
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    payload = _dwcli(["metric-v2", "get", "--key", args.key])
    if payload.get("error"):
        _emit({"error": payload.get("detail") or payload.get("error")})
        return 1
    _emit(payload)
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    payload = _dwcli(["metric-v2", "context", "--key", args.key])
    if payload.get("error"):
        _emit({"error": payload.get("detail") or payload.get("error")})
        return 2
    _emit(payload)
    return 0


def cmd_releases(_args: argparse.Namespace) -> int:
    payload = _dwcli(["metric-v2", "releases"])
    cards = [{
        "release_id": r.get("release_id"), "label": r.get("label"),
        "status": r.get("status"), "projection_status": r.get("projection_status"),
        "revisions": len(r.get("revision_ids_json") or "[]") > 2,
        "published_at": r.get("published_at"),
        "error_code": r.get("error_code") or None,
    } for r in payload.get("releases") or []]
    _emit({"count": len(cards), "releases": cards})
    return 0


def cmd_health(_args: argparse.Namespace) -> int:
    _emit(_dwcli(["metric-v2", "health"]))
    return 0


def cmd_queries(args: argparse.Namespace) -> int:
    payload = _dwcli(["metric-v2", "queries", "--limit", str(args.limit)])
    rows = payload.get("queries") or []
    cards = [{
        "query_id": q.get("query_id"), "question": (q.get("question") or "")[:80],
        "path": q.get("path"), "authoritative": bool(q.get("authoritative")),
        "row_count": q.get("row_count"), "error_code": q.get("error_code") or None,
        "feedback": q.get("feedback"), "created_at": q.get("created_at"),
    } for q in rows]
    _emit({"count": len(cards), "queries": cards})
    return 0


# =========================================================================
# 写子命令（导入 / 发布 / 确认 / 反馈）
# =========================================================================
def cmd_import(args: argparse.Namespace) -> int:
    path = Path(args.file).expanduser()
    if not path.is_file():
        _emit({"error": f"Ossie 文件不存在: {path}"})
        return 1
    content = path.read_text(encoding="utf-8")
    payload = _dwcli(["metric-v2", "import-ossie", "--json-file", "-"],
                     json_body={"content": content, "fmt": "yaml"})
    _emit({
        "imported_revisions": payload.get("revisions"),
        "bindings": len(payload.get("bindings") or []),
        "candidate_mappings": len(payload.get("candidate_mappings") or []),
        "created": payload.get("created"),
        "hint": "候选映射（numerator/denominator/measuresConcept）需 confirm 后才进正式投影",
    })
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    revision_ids = [r.strip() for r in args.revision_ids.split(",") if r.strip()]
    if not revision_ids:
        _emit({"error": "revision_ids 不能为空（逗号分隔的修订 ID）"})
        return 1
    payload = _dwcli(["metric-v2", "publish", "--json-file", "-"],
                     json_body={"revision_ids": revision_ids,
                                "label": args.label or "",
                                "published_by": args.by or "agent"})
    if payload.get("error"):
        _emit(payload)
        return 2
    _emit(payload)
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    body = {"confirmed_by": args.by or "agent"}
    if args.reject:
        body["action"] = "reject"
    payload = _dwcli(["metric-v2", "confirm", "--id", args.mapping_id,
                      "--json-file", "-"], json_body=body)
    if payload.get("error"):
        _emit(payload)
        return 2
    _emit(payload)
    return 0


def cmd_terms(args: argparse.Namespace) -> int:
    """指标已绑定的业务术语（``resolved=false`` 的引用连不出图边）。"""
    payload = _dwcli(["metric-v2", "terms", "--key", args.key])
    if payload.get("error"):
        _emit({"error": payload.get("detail") or payload.get("error")})
        return 1
    _emit(payload)
    return 0


def cmd_bind_term(args: argparse.Namespace) -> int:
    """绑定 / 解绑业务术语（绑定直接生效，不需要再 confirm）。"""
    body: dict = {"term_id": args.term_id}
    if args.unbind:
        body["action"] = "unbind"
    else:
        body["bound_by"] = args.by or "agent"
    payload = _dwcli(["metric-v2", "bind-term", "--key", args.key,
                      "--json-file", "-"], json_body=body)
    if payload.get("error"):
        _emit({"error": payload.get("detail") or payload.get("error")})
        return 2
    _emit(payload)
    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    if args.feedback not in ("verified", "rejected", "none"):
        _emit({"error": "feedback 只接受 verified/rejected/none"})
        return 1
    payload = _dwcli(["metric-v2", "feedback", "--id", args.query_id,
                      "--json-file", "-"],
                     json_body={"feedback": args.feedback, "confirmed_by": args.by or "agent"})
    if payload.get("error"):
        _emit(payload)
        return 2
    _emit(payload)
    return 0


# =========================================================================
# 取数：query（结构化） / ask（自然语言）
# =========================================================================
def cmd_query(args: argparse.Namespace) -> int:
    body: dict = {"metrics": [args.key]}
    if args.group_by:
        body["group_by"] = [g.strip() for g in args.group_by.split(",") if g.strip()]
    if args.filters:
        body["filters"] = []
        for chunk in args.filters.split(";"):
            parts = chunk.split("=", 1)
            if len(parts) == 2:
                body["filters"].append(
                    {"field": parts[0].strip(), "op": "=", "value": parts[1].strip()})
    if args.start and args.end:
        time_obj: dict = {"dimension": args.time_dimension or "",
                          "start": args.start, "end_exclusive": args.end}
        if args.grain:
            time_obj["grain"] = args.grain
        body["time"] = time_obj
    if args.compile_only:
        # 执行语义拆分：只编译出权威口径 SQL（对话链路深化取数），不执行
        body["compile_only"] = True
        result = _dwcli(["metric-v2", "query", "--json-file", "-"], json_body=body)
        if result.get("error"):
            _emit(result)
            return 2
        _emit({
            "sql": result.get("sql"), "sql_executable": result.get("sql_executable"),
            "metric_keys": result.get("metric_keys"), "path": result.get("path"),
            "authoritative": result.get("authoritative"),
            "release_id": result.get("release_id"), "query_id": result.get("query_id"),
            "hint": "权威口径 SQL 已编译（未执行）——交执行通道跑：nl2sql execute_sql.py 或 dwcli sql run",
        })
        return 0
    result = _dwcli(["metric-v2", "query", "--json-file", "-"], json_body=body)
    if result.get("error"):
        _emit(result)
        return 2
    csv_path = _write_csv(result.get("rows") or [], f"metric_{args.key.replace('.', '_')}")
    _emit({
        "csv_path": csv_path, "row_count": result.get("row_count"),
        "path": result.get("path"), "authoritative": result.get("authoritative"),
        "sql": result.get("sql"), "query_id": result.get("query_id"),
        "release_id": result.get("release_id"),
    })
    return 0


def _ask_context(
    question: str,
    *,
    route: str,
    answered: dict | None,
    recall: dict | None,
    subgraph_path: str = "",
    clarifications: list | None = None,
    semantic_refs: list | None = None,
) -> dict:
    """组装 ask 的 context 契约块（dw-analysis 第二段的判断输入）。

    四种 route 同构（缺的填 null/[]），字段固定（定稿见归档方案
    metrics-semantic-layer-v2-hybrid-retrieval-design.md §context 契约）：
    - question          用户原话回显（第二段对照锚点）
    - route             compiler / llm_fallback / exploratory_no_metric / clarification
                        （语义速查在 dw-analysis SKILL.md，不在 JSON 内解释）
    - answered          已解析出的权威口径 SQL（执行语义拆分：ask 不执行，
                        数据不落地）；clarification / exploratory 时为 None。
                        metric_keys+authoritative 表达「命中」；sql 是参数
                        内联的自包含 SQL——既是执行通道的直接输入，也是
                        补数口径对齐的唯一依据
    - recall_summary    指标召回全貌按 score 降序 top 5（含未选中候选）——
                        帮模型确认语义层召回了什么，不做补数分流依据；
                        recall_total 为收窄后候选总数（截断可感知）
    - semantic_refs     语义层锚点（source_id+snapshot_id，深集成后 schema 供给
                        的来源标识；空列表=源未采集，schema 召回缺席）
    - schema_subgraph_path / clarifications  exploratory / clarification 各自产物
    """
    entries = [e for e in (recall or {}).get("entries", []) if isinstance(e, dict)]
    summary = sorted(
        ({"metric_key": e.get("metric_key"),
          "label": e.get("display_name") or e.get("name"),
          "score": e.get("score") or e.get("confidence")} for e in entries),
        key=lambda s: s["score"] or 0, reverse=True)
    return {
        "question": question,
        "route": route,
        "answered": answered,
        "recall_summary": summary[:5],
        "recall_total": len(summary),
        "semantic_refs": semantic_refs or [],
        "schema_subgraph_path": subgraph_path or None,
        "clarifications": clarifications or [],
    }



def cmd_explore(args: argparse.Namespace) -> int:
    body: dict = {"question": args.question}
    if args.scope:
        scope_path = Path(args.scope).expanduser()
        if not scope_path.is_file():
            _emit({"error": f"scope 文件不存在: {scope_path}"})
            return 1
        body["subgraph_scope"] = json.loads(scope_path.read_text(encoding="utf-8"))
    result = _dwcli(["metric-v2", "explore", "--json-file", "-"], json_body=body)
    if result.get("error"):
        _emit(result)
        return 2
    # 执行语义拆分：explore 只生成探索 SQL，执行交对话链路执行通道
    _emit({
        "route": "exploratory", "authoritative": result.get("authoritative"),
        "sql": result.get("sql"), "sql_executable": result.get("sql_executable"),
        "query_id": result.get("query_id"),
        "hint": "探索口径（schema 子图范围内 LLM 生成），回答用户时必须标明非权威；"
                "执行交 nl2sql execute_sql.py 或 dwcli sql run",
    })
    return 0


# =========================================================================
# argparse
# =========================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="dw-metrics V2 指标语义层 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="指标清单").set_defaults(func=cmd_list)

    p = sub.add_parser("get", help="指标详情")
    p.add_argument("--key", required=True, help="metric_key，如 commerce.gmv")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("context", help="指标语义上下文（需已发布）")
    p.add_argument("--key", required=True)
    p.set_defaults(func=cmd_context)

    sub.add_parser("releases", help="发布批次列表").set_defaults(func=cmd_releases)
    sub.add_parser("health", help="语义层健康").set_defaults(func=cmd_health)

    p = sub.add_parser("queries", help="查询追溯列表")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_queries)

    p = sub.add_parser("import-ossie", help="导入 Ossie 语义模型（写操作）")
    p.add_argument("--file", required=True, help="Ossie YAML 文件路径")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("publish", help="发布批次（写操作）")
    p.add_argument("--revision-ids", required=True, help="逗号分隔的修订 ID")
    p.add_argument("--label", default="")
    p.add_argument("--by", default="")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("confirm", help="确认/拒绝候选业务映射（写操作）")
    p.add_argument("--mapping-id", required=True)
    p.add_argument("--reject", action="store_true", help="拒绝而非确认")
    p.add_argument("--by", default="")
    p.set_defaults(func=cmd_confirm)

    p = sub.add_parser("terms", help="指标已绑定的业务术语")
    p.add_argument("--key", required=True)
    p.set_defaults(func=cmd_terms)

    p = sub.add_parser("bind-term", help="绑定/解绑业务术语（写操作，绑定直接生效）")
    p.add_argument("--key", required=True)
    p.add_argument("--term-id", required=True, help="术语 ID（不是术语名）")
    p.add_argument("--unbind", action="store_true", help="解绑而非绑定")
    p.add_argument("--by", default="")
    p.set_defaults(func=cmd_bind_term)

    p = sub.add_parser("feedback", help="查询反馈（verified 固化复用 SQL）")
    p.add_argument("--query-id", required=True)
    p.add_argument("--feedback", required=True, choices=["verified", "rejected", "none"])
    p.add_argument("--by", default="")
    p.set_defaults(func=cmd_feedback)

    p = sub.add_parser("query", help="结构化取数：默认执行落 CSV；--compile-only 只出权威口径 SQL")
    p.add_argument("--key", required=True, help="metric_key")
    p.add_argument("--group-by", default="", help="逗号分隔维度")
    p.add_argument("--time-dimension", default="", help="时间维度（默认用指标声明的）")
    p.add_argument("--start", default="", help="YYYY-MM-DD（含）")
    p.add_argument("--end", default="", help="YYYY-MM-DD（不含，开区间上界）")
    p.add_argument("--grain", default="", help="day/week/month/year")
    p.add_argument("--filters", default="", help="分号分隔 field=value")
    p.add_argument("--compile-only", action="store_true",
                   help="只编译出 SQL 不执行（对话链路；执行交 nl2sql/dwcli sql run）")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("explore", help="R3 受控探索：schema 子图内 LLM 生成 SQL（非权威）")
    p.add_argument("--question", required=True)
    p.add_argument("--scope", default="", help="可选：ask 落盘的 schema_subgraph JSON 路径")
    p.set_defaults(func=cmd_explore)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return _run(args.func, args)


if __name__ == "__main__":
    sys.exit(main())
