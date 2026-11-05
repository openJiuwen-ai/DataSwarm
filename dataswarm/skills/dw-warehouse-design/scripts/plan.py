# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""方案构建主入口 —— 意图 → 快照 → 语义规格 → 确定性渲染 → plan 落盘。

L1：全自动（快照 → ODS plan），零 LLM 设计、零 SQL 生成。
L2/L3：LLM 只写语义规格（--spec-file），SQL 由渲染器生成，校验失败整体拒绝。

用法：
    python plan.py --action plan --csv-dir D:/olist --tier L1
    python plan.py --action plan --csv-dir D:/olist --tier L3 --spec-file _spec.json
    python plan.py --action spec-template --csv-dir D:/olist --tier L3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import discovery
import infer
import intent as intent_mod
import metrics as metrics_mod
import render
import schema as schema_mod

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _split_files(raw: str) -> list[str] | None:
    files = [f.strip() for f in (raw or "").split(",") if f.strip()]
    return files or None


_TIER_ALIASES = {"L1": "L1_INGEST", "L2": "L2_MODELING", "L3": "L3_FULL_DW"}


def _normalize_tier(raw: str) -> str:
    return _TIER_ALIASES.get((raw or "").strip().upper(), raw or "")


def _determine_tier(args) -> tuple[str, dict]:
    """tier 优先级：显式 --tier > 意图分类 > 默认 L1（最小路径）。"""
    tier = _normalize_tier(args.tier)
    if tier and tier != "auto":
        return tier, {}
    if args.text:
        result = intent_mod.classify(args.text, args.plan_id or "")
        if result["action"] == "BUILD":
            return result["tier"], result
        return "", result
    return "L1_INGEST", {"note": "未提供意图文本，默认最小路径 L1"}


def _register_plan(plan_id: str, name: str, business: str, purpose: str) -> dict:
    return discovery.dwcli(
        "plan",
        "save",
        "--plan-id",
        plan_id,
        "--name",
        name,
        "--business",
        business,
        "--purpose",
        purpose,
    )


def _summary_markdown(plan: dict, reuse: list[dict], warnings: list[str]) -> str:
    """方案摘要（Markdown）：agent 必须先原样呈现给用户，再请求确认。"""
    lines = [
        f"## 数仓建设方案（{plan['lifecycle_tier']}）",
        "",
        f"- **plan_id**：`{plan['plan_id']}`",
        f"- **业务库**：`{plan['biz_database']}`",
        f"- **建设范围**：{plan['purpose']}",
        f"- **调度**：`{plan.get('schedule_cron') or '0 2 * * *'}`（cron：分 时 日 月 周）",
        f"- **表数量**：{len(plan['tables'])}",
        "",
        "| 表名 | 层级 | 数据来源 | 源行数 |",
        "|---|---|---|---|",
    ]
    for table in plan["tables"]:
        sources = "、".join(f"`{source}`" for source in table.get("source_tables", []))
        rows = table.get("row_count")
        lines.append(
            f"| `{table['table_name']}` | {table['layer']} | {sources} | "
            f"{rows if rows is not None else '—'} |"
        )
    if reuse:
        lines += ["", "**已存在、默认跳过（复用）**："]
        lines += [f"- `{entry['ods_table']}` ← `{entry['file']}`" for entry in reuse]
    if warnings:
        lines += ["", "**注意事项**："]
        lines += [f"- {warning}" for warning in warnings]
    return "\n".join(lines)


def _confirm_question_markdown(summary_markdown: str, plan_id: str) -> str:
    """执行确认文案（内嵌完整方案正文）——供 agent 原样输出到聊天正文后等待用户下一轮确认。"""
    return (
        f"{summary_markdown}\n\n---\n\n"
        f"**是否确认按方案 `{plan_id}` 执行建仓？**\n\n"
        "确认后将创建上述表并导入数据；如需调整请回复「调整方案：<调整内容>」。"
    )


def action_spec_template(args) -> None:
    snap = discovery.snapshot(args.csv_dir, _split_files(args.files), args.sample_rows)
    if not snap.get("ok"):
        _emit(snap)
        sys.exit(1)
    biz = snap["biz_database"]
    new_ods = [f"{biz}.{e['ods_table']}" for e in snap["create"]]
    reused_ods = [f"{biz}.{e['ods_table']}" for e in snap["reuse"]]
    example_source = (
        new_ods[0] if new_ods else (reused_ods[0] if reused_ods else f"{biz}.ods_<表>")
    )
    template = {
        "name": args.name or f"{biz}_layered",
        "business": biz,
        "purpose": args.purpose or "",
        "schedule_cron": "0 2 * * *",
        "tables": [
            {
                "short_name": "dwd_<示例_替换我>",
                "layer": "DWD",
                "comment": "明细宽表（示例）",
                "columns": [
                    {"name": "id", "dtype": "BIGINT", "nullable": False},
                    {"name": "biz_ts", "dtype": "DATETIME"},
                ],
                "source_tables": [example_source],
                "source_aliases": {example_source: "a"},
                "joins": [],
                "column_exprs": {"id": "a.`id`", "biz_ts": "a.`biz_ts`"},
                "group_by": [],
                "where": "",
            }
        ],
        "_available_ods_sources": new_ods + reused_ods,
        "_rules": [
            "调度用顶层 schedule_cron（5 段 cron：分 时 日 月 周）；用户提出调度诉求时按其原话换算写入"
            "（如『每天凌晨 3 点』→ '0 3 * * *'），缺省 0 2 * * *（每天 02:00）",
            "源表只能是 _available_ods_sources 列出的 ODS 或本规格内更下层的表（DWD←ODS，DWS←DWD，ADS←DWD/DWS）",
            "ODS 已是 DATE/DATETIME 的时间列直接引用；仅 VARCHAR 时间列转 DATE/DATETIME 时包 NULLIF(col, '')",
            "非 group_by 列必须给 column_exprs；聚合表达式直接写在 column_exprs（如 COUNT(DISTINCT a.`id`)）",
            "只写语义，不要写 DDL/INSERT SQL —— SQL 由渲染器生成",
        ],
    }
    _emit({"ok": True, "action": "spec_template", "tier": args.tier, "spec": template})


def action_plan(args) -> None:
    tier, intent_info = _determine_tier(args)
    if not tier:
        _emit(
            {
                "ok": False,
                "error": f"意图不是 BUILD：{intent_info.get('action')}（{intent_info.get('reason')}）",
                "intent": intent_info,
            }
        )
        sys.exit(1)

    snap = discovery.snapshot(
        args.csv_dir, _split_files(args.files), args.sample_rows, compact=False
    )
    if not snap.get("ok"):
        _emit(snap)
        sys.exit(1)
    biz = snap["biz_database"]
    dialect = str(snap.get("dialect") or "doris")
    # ODS：默认只建新表（复用优先）；--include-reuse 时包含复用表（重导）
    selected = list(snap["create"])
    if args.include_reuse:
        selected += list(snap["reuse"])
    if not selected and (tier == "L1_INGEST" or not args.spec_file):
        _emit(
            {
                "ok": False,
                "error": "所有 CSV 对应的 ODS 表已存在（全部复用）；如需重建/重导请加 --include-reuse",
                "reuse": snap["reuse"],
            }
        )
        sys.exit(1)
    files_by_name = {f["file"]: f for f in snap["csv_files"]}
    ods_file_infos = [
        files_by_name[e["file"]] for e in selected if e["file"] in files_by_name
    ]
    ods_tables = [
        render.render_ods_plan_table(biz, info, dialect=dialect) for info in ods_file_infos
    ]

    # 派生层（L2/L3）：读语义规格并强校验
    spec = None
    derived_tables: list[dict] = []
    design_warnings: list[str] = []
    if tier != "L1_INGEST":
        if not args.spec_file:
            _emit(
                {
                    "ok": False,
                    "error": f"tier={tier} 需要 --spec-file（语义规格，见 references/semantic-spec.md）；"
                    "可先用 --action spec-template 生成模板",
                }
            )
            sys.exit(1)
        try:
            spec = schema_mod.load_spec(args.spec_file)
        except ValueError as exc:
            _emit({"ok": False, "error": f"语义规格解析失败: {exc}"})
            sys.exit(1)

        # 指标驱动模式（推荐）：metrics[] → DWS/ADS 分层表，确定性推导（top-down）
        combined_tables = list(spec.tables)
        metric_warnings: list[str] = []
        ods_types = {
            f"{biz}.ods_{infer.sanitize_stem(str(info['stem']))}": {
                c["name"]: (c["dtype"], c.get("nullable", True))
                for c in info["columns"]
            }
            for info in snap["csv_files"]
        }
        spec_types = {
            f"{biz}.{t.short_name}": {
                c.name: (c.dtype, c.nullable) for c in t.columns
            }
            for t in spec.tables
        }
        # derive 会把推导出的 DWS 列动态注册进 column_types，供 ADS 推导与
        # 显式表表达式列引用校验解析
        column_types = {**ods_types, **spec_types}
        if spec.metrics:
            derived_metric_tables, metric_errors, metric_warnings = (
                metrics_mod.derive_tables_from_metrics(
                    spec,
                    biz,
                    column_types,
                    tier,
                )
            )
            if metric_errors:
                _emit(
                    {"ok": False, "error": "指标推导校验失败", "errors": metric_errors}
                )
                sys.exit(1)
            combined_tables += derived_metric_tables
        combined_spec = schema_mod.SemanticSpec(
            spec.name,
            spec.business,
            spec.purpose,
            combined_tables,
        )
        design_warnings = metric_warnings + schema_mod.spec_warnings(combined_spec)
        available = {t["table_name"] for t in ods_tables}
        available |= {f"{biz}.{e['ods_table']}" for e in snap["reuse"]}
        available |= {f"{biz}.{t.short_name}" for t in combined_tables}
        # 列级校验数据源：ODS 用 CSV 推断列，规格内表用声明列（防执行期 Unknown column）
        source_columns: dict[str, set[str]] = {}
        for info in snap["csv_files"]:
            full = f"{biz}.ods_{infer.sanitize_stem(str(info['stem']))}"
            source_columns[full] = {str(c["name"]) for c in info["columns"]}
        for e in snap["reuse"]:
            full = f"{biz}.{e['ods_table']}"
            source_columns.setdefault(full, set())  # 复用 ODS 无 CSV 列清单，跳过列校验
        for t in combined_tables:
            source_columns[f"{biz}.{t.short_name}"] = {c.name for c in t.columns}
        errors = schema_mod.validate_spec(combined_spec, tier, available,
                                          source_columns=source_columns)
        # 显式表表达式列校验（where / group_by 缺省引用 / join.on 列名级），
        # 与 validate_spec 的 source_columns 校验互补：后者只查 joins.on 表级与 column_exprs
        errors += schema_mod.validate_table_expr_columns(spec.tables, column_types)
        if errors:
            _emit({"ok": False, "error": "语义规格校验失败", "errors": errors})
            sys.exit(1)
        derived_tables = [
            render.render_derived_plan_table(biz, t, dialect=dialect) for t in combined_tables
        ]

    if not ods_tables and not derived_tables:
        _emit(
            {
                "ok": False,
                "error": "全部 ODS 复用且语义规格没有可建的派生层表；如需重导 ODS 请加 --include-reuse",
                "reuse": snap["reuse"],
            }
        )
        sys.exit(1)

    plan_id = args.plan_id or f"plan_{uuid.uuid4().hex[:12]}"
    name = args.name or (spec.name if spec else f"{biz}_{tier.lower()}")
    business = spec.business if spec else biz
    purpose = args.purpose or (spec.purpose if spec else f"{tier} 数仓建设")
    files = [info["file"] for info in ods_file_infos]
    file_options = {
        info["file"]: {
            "delimiter": info.get("delimiter") or ",",
            "encoding": info.get("encoding") or "utf-8-sig",
        }
        for info in ods_file_infos
    }
    # 调度优先级：显式 --schedule-cron > spec.schedule_cron（用户输入） > 默认每天 02:00
    schedule_cron = (
        getattr(args, "schedule_cron", "")
        or (spec.schedule_cron if spec else "")
        or "0 2 * * *"
    )
    plan = render.build_plan(
        render.PlanSpec(
            plan_id=plan_id,
            name=name,
            business=business,
            biz_database=biz,
            purpose=purpose,
            tier=tier,
            csv_dir=snap["csv_dir"],
            files=files,
            ods_tables=ods_tables,
            derived_tables=derived_tables,
            schedule_cron=schedule_cron,
            file_options=file_options,
            dialect=dialect,
        )
    )
    summary_markdown = _summary_markdown(plan, snap["reuse"], design_warnings)
    confirm_question = _confirm_question_markdown(summary_markdown, plan_id)
    # confirm_question 的干净真源是 plan 文件：Windows 终端输出可能被 GBK 解码显示乱码，
    # read_file 读 plan 文件（UTF-8）始终可靠；agent 不得依据乱码输出重建问句。
    plan["confirm_question"] = confirm_question

    if args.dry_run:
        _emit(
            {
                "ok": True,
                "action": "plan",
                "dry_run": True,
                "plan": plan,
                "warnings": design_warnings,
                "reuse_skipped": snap["reuse"],
                "summary_markdown": summary_markdown,
                "confirm_question": confirm_question,
            }
        )
        return

    from jiuwenswarm.common.utils import get_user_workspace_dir

    plans_dir = Path(get_user_workspace_dir()) / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_file = plans_dir / f"{plan_id}.json"
    tmp_file = plans_dir / f"_{plan_id}.json.tmp"
    tmp_file.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp_file, plan_file)

    registered = _register_plan(plan_id, name, business, purpose)
    if not registered.get("ok"):
        _emit(
            {
                "ok": False,
                "error": f"plan DB 索引注册失败: {registered.get('error')}",
                "hint": "plan 文件已落盘但 DB 无索引，下游 --plan-id 会查不到；检查 dataagent 服务后重跑",
                "plan_file": str(plan_file),
            }
        )
        sys.exit(1)

    counts: dict[str, int] = {}
    for table in plan["tables"]:
        counts[table["layer"]] = counts.get(table["layer"], 0) + 1
    _emit(
        {
            "ok": True,
            "action": "plan",
            "plan_id": plan_id,
            "plan_file": str(plan_file),
            "lifecycle_tier": tier,
            "table_counts": counts,
            "tables": [
                {
                    "table": t["table_name"],
                    "layer": t["layer"],
                    "rows": t.get("row_count"),
                }
                for t in plan["tables"]
            ],
            "metrics": [
                {
                    "metric": m.metric_name,
                    "display_name": m.display_name,
                    "table": f"{biz}.{m.table_name}",
                }
                for m in spec.metrics
            ]
            if spec
            else [],
            "warnings": design_warnings,
            "reuse_skipped": snap["reuse"],
            "summary_markdown": summary_markdown,
            "confirm_question": confirm_question,
            "next": f"read_file 读取 plan 文件的 confirm_question 字段，原样输出到当前 assistant 回复正文后结束本轮；"
            f"用户下一轮明确回复「确认执行」后才 handoff 到 dw-warehouse-build: "
            f"execute_pipeline.py --action run --plan-id {plan_id}",
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="数仓方案构建（意图 → 快照 → 渲染 → 落盘）"
    )
    parser.add_argument("--action", required=True, choices=["plan", "spec-template"])
    parser.add_argument("--csv-dir", required=True)
    parser.add_argument("--files", default="", help="逗号分隔的文件名（可选）")
    parser.add_argument("--sample-rows", type=int, default=200)
    parser.add_argument(
        "--tier",
        default="auto",
        choices=["auto", "L1", "L2", "L3", "L1_INGEST", "L2_MODELING", "L3_FULL_DW"],
    )
    parser.add_argument(
        "--text",
        default="",
        help="用户原话（用于意图分类，配合 auto tier；"
        "含空格/中文时必须整体用双引号包裹）",
    )
    parser.add_argument("--plan-id", default="", help="指定 plan_id（缺省自动生成）")
    parser.add_argument(
        "--spec-file", default="", help="语义规格 JSON 文件（L2/L3 必填）"
    )
    parser.add_argument("--name", default="")
    parser.add_argument("--purpose", default="")
    parser.add_argument(
        "--schedule-cron",
        default="",
        help="调度 cron（5 段：分 时 日 月 周）；优先于 spec.schedule_cron，"
        "缺省 0 2 * * *（每天 02:00）。用户提出调度诉求时必填",
    )
    parser.add_argument("--dry-run", action="store_true", help="只预览不落盘")
    parser.add_argument(
        "--include-reuse", action="store_true", help="包含已存在的 ODS 表（重导）"
    )
    args = parser.parse_args()

    if args.action == "spec-template":
        action_spec_template(args)
    else:
        action_plan(args)


if __name__ == "__main__":
    main()
