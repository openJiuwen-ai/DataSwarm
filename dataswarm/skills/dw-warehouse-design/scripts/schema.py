# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""语义规格契约 —— LLM 只写语义，脚本生成 SQL。

契约分层：
- SemanticSpec：LLM 交付物（L2/L3 时），只含表/列/JOIN/表达式等语义信息
- validate_spec：plan.py 渲染前强制校验，失败即整体拒绝（不稳定输出在设计期拦截）
"""
from __future__ import annotations

import json
import re
import difflib
from dataclasses import dataclass, field
from pathlib import Path

LAYER_ORDER = {"ODS": 0, "DWD": 1, "DWS": 2, "ADS": 3}
TIER_MAX_LAYER = {"L1_INGEST": 0, "L2_MODELING": 1, "L3_FULL_DW": 3}
# 目标层 → 允许的源层（禁止跳层、禁止同层引用）
ALLOWED_SRC_LAYERS = {"DWD": {0}, "DWS": {0, 1}, "ADS": {1, 2}}
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LAYER_PREFIX = {"DWD": "dwd_", "DWS": "dws_", "ADS": "ads_"}
_CRON_FIELD_RE = re.compile(
    r"^(\*|\d{1,2}|\*/\d{1,2}|\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2}/\d{1,2})"
    r"(,(\*|\d{1,2}|\*/\d{1,2}|\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2}/\d{1,2}))*$"
)
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_QUALIFIED_REF_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\.(?:`([^`]+)`|([A-Za-z_][A-Za-z0-9_]*))"
)


def _valid_cron(expr: str) -> bool:
    """5 段 cron（分 时 日 月 周）结构校验；范围语义由调度器兜底。"""
    parts = expr.split()
    return len(parts) == 5 and all(_CRON_FIELD_RE.match(p) for p in parts)


@dataclass
class Column:
    name: str
    dtype: str
    nullable: bool = True
    comment: str = ""


@dataclass
class JoinOn:
    left_col: str
    right_col: str


@dataclass
class Join:
    left: str
    right: str
    on: list[JoinOn]
    kind: str = "left"


@dataclass
class TableSpec:
    short_name: str
    layer: str
    columns: list[Column]
    source_tables: list[str]
    source_aliases: dict[str, str] = field(default_factory=dict)
    joins: list[Join] = field(default_factory=list)
    column_exprs: dict[str, str] = field(default_factory=dict)
    group_by: list[str] = field(default_factory=list)
    where: str = ""
    comment: str = ""


@dataclass
class MetricSpec:
    """指标声明 —— 指标驱动模式的一等公民。

    LLM 只负责把业务诉求翻译成指标声明（名称/口径/粒度/表达式/来源），
    DWS/ADS 分层表由 metrics.py 确定性推导。
    """
    metric_name: str
    display_name: str
    value_type: str = "DOUBLE"
    expr: str = ""
    grain: list[str] = field(default_factory=list)
    grain_exprs: dict[str, str] = field(default_factory=dict)
    source_tables: list[str] = field(default_factory=list)
    source_aliases: dict[str, str] = field(default_factory=dict)
    table_name: str = ""
    where: str = ""
    caliber: str = ""
    dimensions: list[str] = field(default_factory=list)


@dataclass
class SemanticSpec:
    name: str
    business: str
    purpose: str
    tables: list[TableSpec] = field(default_factory=list)
    metrics: list[MetricSpec] = field(default_factory=list)
    schedule_cron: str = ""


def load_spec(path: str | Path) -> SemanticSpec:
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"语义规格文件不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"语义规格不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("语义规格根节点必须是 JSON 对象")
    return spec_from_json(data)


def spec_from_json(data: dict) -> SemanticSpec:
    """dict → SemanticSpec（含基础解析校验，抛 ValueError）。"""
    name = str(data.get("name") or "").strip()
    business = str(data.get("business") or "").strip()
    if not name:
        raise ValueError("spec.name 必填")
    if not business:
        raise ValueError("spec.business 必填")
    schedule_cron = str(data.get("schedule_cron") or "").strip()
    if schedule_cron and not _valid_cron(schedule_cron):
        raise ValueError(
            f"spec.schedule_cron 不是合法的 5 段 cron（分 时 日 月 周），如 '0 2 * * *'：{schedule_cron!r}"
        )

    tables: list[TableSpec] = []
    for i, t in enumerate(data.get("tables") or []):
        if not isinstance(t, dict):
            raise ValueError(f"tables[{i}] 必须是 JSON 对象")
        short = str(t.get("short_name") or "").strip()
        layer = str(t.get("layer") or "").strip().upper()
        if not short:
            raise ValueError(f"tables[{i}].short_name 必填")
        if layer not in ("DWD", "DWS", "ADS"):
            raise ValueError(f"tables[{i}].layer 必须是 DWD/DWS/ADS（ODS 由脚本自动生成）")
        columns = [
            Column(
                name=str(c.get("name") or "").strip(),
                dtype=str(c.get("dtype") or "").strip(),
                nullable=bool(c.get("nullable", True)),
                comment=str(c.get("comment") or ""),
            )
            for c in (t.get("columns") or [])
        ]
        joins = [
            Join(
                left=str(j.get("left") or ""),
                right=str(j.get("right") or ""),
                on=[JoinOn(str(obj.get("left_col") or ""), str(obj.get("right_col") or ""))
                    for obj in (j.get("on") or [])],
                kind=str(j.get("type") or "left").lower(),
            )
            for j in (t.get("joins") or [])
        ]
        tables.append(TableSpec(
            short_name=short,
            layer=layer,
            columns=columns,
            source_tables=[str(s) for s in (t.get("source_tables") or [])],
            source_aliases={str(k): str(v) for k, v in (t.get("source_aliases") or {}).items()},
            joins=joins,
            column_exprs={str(k): str(v) for k, v in (t.get("column_exprs") or {}).items()},
            group_by=[str(g) for g in (t.get("group_by") or [])],
            where=str(t.get("where") or "").strip(),
            comment=str(t.get("comment") or ""),
        ))

    metrics: list[MetricSpec] = []
    for i, m in enumerate(data.get("metrics") or []):
        if not isinstance(m, dict):
            raise ValueError(f"metrics[{i}] 必须是 JSON 对象")
        metric_name = str(m.get("metric_name") or "").strip()
        table_name = str(m.get("table_name") or "").strip()
        expr = str(m.get("expr") or "").strip()
        if not metric_name:
            raise ValueError(f"metrics[{i}].metric_name 必填")
        if not _IDENT_RE.match(metric_name):
            raise ValueError(f"metrics[{i}].metric_name 非法: {metric_name!r}")
        if not table_name:
            raise ValueError(f"metrics[{i}].table_name 必填（dws_/ads_ 前缀）")
        if not (table_name.startswith("dws_") or table_name.startswith("ads_")):
            raise ValueError(f"metrics[{i}].table_name 必须以 dws_/ads_ 开头: {table_name}")
        if not expr:
            raise ValueError(f"metrics[{i}].expr 必填（聚合表达式）")
        sources = [str(s) for s in (m.get("source_tables") or [])]
        if not sources:
            raise ValueError(f"metrics[{i}].source_tables 必填")
        metrics.append(MetricSpec(
            metric_name=metric_name,
            display_name=str(m.get("display_name") or metric_name),
            value_type=str(m.get("value_type") or "DOUBLE"),
            expr=expr,
            grain=[str(g) for g in (m.get("grain") or [])],
            grain_exprs={str(k): str(v) for k, v in (m.get("grain_exprs") or {}).items()},
            source_tables=sources,
            source_aliases={str(k): str(v) for k, v in (m.get("source_aliases") or {}).items()},
            table_name=table_name,
            where=str(m.get("where") or "").strip(),
            caliber=str(m.get("caliber") or ""),
            dimensions=[str(d) for d in (m.get("dimensions") or [])],
        ))
    return SemanticSpec(name=name, business=business,
                        purpose=str(data.get("purpose") or ""),
                        tables=tables, metrics=metrics,
                        schedule_cron=schedule_cron)


def _source_layer(full_name: str, spec: SemanticSpec) -> int | None:
    short = full_name.split(".")[-1]
    for t in spec.tables:
        if t.short_name == short:
            return LAYER_ORDER[t.layer]
    if short.startswith("ods_"):
        return 0
    return None


_COL_REF_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")


def validate_column_refs(t: "TableSpec", source_columns: dict[str, set[str]],
                         errors: list[str]) -> None:
    """列级校验：joins 的 ON 列与 column_exprs 里 `别名.列` 引用必须存在于源表。

    背景（真实事故）：模型在 spec 里写 `orders(o) JOIN sellers ON o.seller_id`，
    但 orders 源表没有 seller_id（它在 order_items 表）——此前只查表级引用，
    列级错误要到 ETL 执行期才报 Unknown column，DAG 下游全部 skipped。
    source_columns: 源表全名 → 可用列集合（ODS 取 CSV 推断列，规格内表取声明列）。
    """
    alias_to_table = {alias: full for full, alias in (t.source_aliases or {}).items()}

    def _check(expr: str, where: str) -> None:
        for alias, col in _COL_REF_RE.findall(expr or ""):
            full = alias_to_table.get(alias)
            if full is None or full not in source_columns:
                continue  # 表级引用已由 validate_spec 报过
            cols = source_columns[full]
            if col.lower() not in {c.lower() for c in cols}:
                hint = ", ".join(sorted(cols)[:12])
                more = f" 等 {len(cols)} 列" if len(cols) > 12 else ""
                errors.append(
                    f"{t.short_name}: {where} 引用了源表 {full} 不存在的列 "
                    f"`{alias}.{col}`；该表可用列: {hint}{more}"
                )

    for j in t.joins or []:
        _check(j.left, "joins.on.left")
        _check(j.right, "joins.on.right")
    for col, expr in (t.column_exprs or {}).items():
        _check(expr, f"column_exprs[{col}]")


def validate_spec(spec: SemanticSpec, tier: str, available_sources: set[str],
                  source_columns: dict[str, set[str]] | None = None) -> list[str]:
    """渲染前校验。available_sources = plan 新建 ODS 全名 ∪ 复用 ODS 全名 ∪ 规格内表全名。

    source_columns（可选）= 源表全名 → 可用列集合；传入时做列级校验
    （joins.on / column_exprs 引用列必须存在，防执行期 Unknown column）。
    """
    errors: list[str] = []
    max_layer = TIER_MAX_LAYER.get(tier)
    if max_layer is None:
        errors.append(f"未知 tier: {tier}")
        return errors

    seen_short: set[str] = set()
    for t in spec.tables:
        prefix = _LAYER_PREFIX.get(t.layer, "")
        if prefix and not t.short_name.startswith(prefix):
            errors.append(f"{t.short_name}: {t.layer} 层表名必须以 {prefix} 开头")
        if t.short_name in seen_short:
            errors.append(f"表名重复: {t.short_name}")
        seen_short.add(t.short_name)
        if LAYER_ORDER[t.layer] > max_layer:
            errors.append(f"{t.short_name}: 层级 {t.layer} 超出 tier {tier} 允许范围")
        if not t.columns:
            errors.append(f"{t.short_name}: columns 不能为空")
        col_names = [c.name for c in t.columns]
        for c in t.columns:
            if not _IDENT_RE.match(c.name):
                errors.append(f"{t.short_name}: 非法列名 {c.name!r}")
            if not c.dtype:
                errors.append(f"{t.short_name}: 列 {c.name} 缺少 dtype")
        if len(col_names) != len(set(col_names)):
            errors.append(f"{t.short_name}: 列名重复")
        if not t.source_tables:
            errors.append(f"{t.short_name}: source_tables 不能为空")

        # 列级校验：joins.on / column_exprs 引用列必须存在于源表（source_columns 传入时）
        if source_columns is not None:
            validate_column_refs(t, source_columns, errors)

        # 源表存在性 + 层级依赖
        for s in t.source_tables:
            if s not in available_sources:
                close = difflib.get_close_matches(s, available_sources, n=1, cutoff=0.6)
                hint = f"；最接近的可用源: {close[0]}，请改为它后重试" if close else ""
                errors.append(
                    f"{t.short_name}: 源表 {s} 不在可用源中（plan ODS / 复用 ODS / 规格内更下层表）{hint}"
                )
                continue
            src_layer = _source_layer(s, spec)
            if src_layer is None or src_layer not in ALLOWED_SRC_LAYERS[t.layer]:
                allowed = "/".join(
                    name for name, idx in LAYER_ORDER.items() if idx in ALLOWED_SRC_LAYERS[t.layer]
                )
                errors.append(f"{t.short_name}: {t.layer} 层源表 {s} 跳层或同层引用（允许源层: {allowed}）")

        # 别名覆盖与合法性
        if t.joins or t.column_exprs:
            missing = [s for s in t.source_tables if s not in t.source_aliases]
            if missing:
                errors.append(f"{t.short_name}: source_aliases 缺少 {missing} 的别名")
        for s, alias in t.source_aliases.items():
            if s not in t.source_tables:
                errors.append(f"{t.short_name}: source_aliases 定义了非源表 {s}")
            if not _IDENT_RE.match(alias):
                errors.append(f"{t.short_name}: 非法别名 {alias!r}")

        # JOIN 校验
        for j in t.joins:
            if j.left not in t.source_tables or j.right not in t.source_tables:
                errors.append(f"{t.short_name}: join 引用了非源表 {j.left} / {j.right}")
            if not j.on:
                errors.append(f"{t.short_name}: join.on 不能为空")
            if j.kind not in ("left", "inner"):
                errors.append(f"{t.short_name}: join.type 只支持 left/inner")
            for obj in j.on:
                if not obj.left_col or not obj.right_col:
                    errors.append(f"{t.short_name}: join.on 的 left_col/right_col 不能为空")

        # 表达式覆盖：非 group_by 列必须有 expr；group_by 列可缺省（默认 别名.列）
        for target in t.column_exprs:
            if target not in col_names:
                errors.append(f"{t.short_name}: column_exprs 目标列 {target} 不在 columns 中")
        for cname in col_names:
            if cname not in t.group_by and cname not in t.column_exprs:
                errors.append(f"{t.short_name}: 非 group_by 列 {cname} 缺少 column_exprs 表达式")
        for g in t.group_by:
            if g not in col_names:
                errors.append(f"{t.short_name}: group_by 列 {g} 不在 columns 中")
    return errors


def check_expr_columns(
    label: str,
    expr: str,
    source_tables: list[str],
    source_aliases: dict[str, str],
    column_types: dict[str, dict[str, tuple[str, bool]]],
    errors: list[str],
) -> None:
    """表达式列引用校验：别名.列 必须真实存在于来源表列定义。

    只校验限定引用（alias.col / alias.`col`）：裸标识符无法在无 SQL 解析器的
    前提下与函数名/关键字/字面量无歧义区分。字符串字面量（如 'a.b'）不参与
    引用提取；来源表缺失由上层存在性校验负责，此处跳过避免重复报错。
    """
    if not expr:
        return
    alias_to_table = {alias: table for table, alias in source_aliases.items()}
    seen: set[tuple[str, str]] = set()
    for match in _QUALIFIED_REF_RE.finditer(_STRING_LITERAL_RE.sub("''", expr)):
        alias = match.group(1)
        column = match.group(2) or match.group(3)
        if (alias, column) in seen:
            continue
        seen.add((alias, column))
        table = alias_to_table.get(alias)
        if table is None:
            declared = "、".join(sorted(set(source_aliases.values()))) or "无"
            errors.append(
                f"{label}: 表达式 {expr!r} 引用了未在 source_aliases 声明的别名 {alias}"
                f"（可用别名: {declared}）"
            )
            continue
        if table not in source_tables or table not in column_types:
            continue
        available_columns = column_types[table]
        if column not in available_columns:
            close = difflib.get_close_matches(column, available_columns, n=1, cutoff=0.6)
            hint = f"；最接近的可用列: {close[0]}，请改为它后重试" if close else ""
            errors.append(
                f"{label}: 表达式 {expr!r} 引用了来源表 {table} 中不存在的列 "
                f"{alias}.{column}{hint}"
            )


def _check_join_column(
    table_name: str,
    source: str,
    column: str,
    side: str,
    column_types: dict[str, dict[str, tuple[str, bool]]],
    errors: list[str],
) -> None:
    if not column or source not in column_types:
        return
    available_columns = column_types[source]
    if column not in available_columns:
        close = difflib.get_close_matches(column, available_columns, n=1, cutoff=0.6)
        hint = f"；最接近的可用列: {close[0]}，请改为它后重试" if close else ""
        errors.append(f"{table_name}: join.on {side}表 {source} 不存在列 {column}{hint}")


def validate_table_expr_columns(
    tables: list[TableSpec],
    column_types: dict[str, dict[str, tuple[str, bool]]],
) -> list[str]:
    """显式表表达式列引用校验（column_exprs / where / join.on / group_by 缺省引用）。

    与渲染器缺省逻辑一致：group_by 列未给表达式时默认取「首个来源别名.同名列」，
    该列必须真实存在于首个来源表，否则渲染出的 SQL 会在 Doris 执行期报 Unknown column。
    """
    errors: list[str] = []
    for t in tables:
        if not t.source_tables:
            continue
        effective_aliases = {
            source: t.source_aliases.get(source, source.split(".")[-1])
            for source in t.source_tables
        }
        for target, expr in t.column_exprs.items():
            check_expr_columns(
                f"{t.short_name}.{target}",
                expr,
                t.source_tables,
                effective_aliases,
                column_types,
                errors,
            )
        check_expr_columns(
            f"{t.short_name}.where",
            t.where,
            t.source_tables,
            effective_aliases,
            column_types,
            errors,
        )
        for group_col in t.group_by:
            if group_col in t.column_exprs:
                continue
            first = t.source_tables[0]
            default_expr = f"{effective_aliases[first]}.{group_col}"
            check_expr_columns(
                f"{t.short_name}.{group_col}",
                default_expr,
                t.source_tables,
                effective_aliases,
                column_types,
                errors,
            )
        for join in t.joins:
            for on in join.on:
                _check_join_column(
                    t.short_name, join.left, on.left_col, "左", column_types, errors
                )
                _check_join_column(
                    t.short_name, join.right, on.right_col, "右", column_types, errors
                )
    return errors


def spec_warnings(spec: SemanticSpec) -> list[str]:
    """设计期启发式警告：LEFT JOIN 右表表达式喂 NOT NULL 列。

    这类列在 Doris 严格模式下写入 NULL 会触发 DATA_QUALITY_ERROR（与粒度列同类根因），
    在设计期提示作者改为可空或用 COALESCE 清洗。
    """
    warnings: list[str] = []
    for t in spec.tables:
        right_aliases: set[str] = set()
        for j in t.joins:
            if j.kind != "left":
                continue
            alias = t.source_aliases.get(j.right)
            if alias:
                right_aliases.add(f"{alias}.")
        if not right_aliases:
            continue
        for c in t.columns:
            if c.nullable:
                continue
            expr = t.column_exprs.get(c.name, "")
            if expr and any(marker in expr for marker in right_aliases):
                warnings.append(
                    f"{t.short_name}: NOT NULL 列 {c.name} 的表达式引用了 LEFT JOIN 右表，"
                    f"可能产生 NULL（Doris 严格模式 DATA_QUALITY_ERROR）；建议列改可空或表达式包 COALESCE"
                )
    return warnings
