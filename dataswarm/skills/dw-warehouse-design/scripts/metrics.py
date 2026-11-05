# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""指标驱动分层推导器 —— metrics[] 声明 → DWS/ADS 表（确定性编译）。

设计原则（top-down，业务诉求 → 指标 → 分层）：
- LLM 只负责把业务诉求翻译成指标声明（名称/口径/粒度/表达式/来源）
- 分层推导由代码完成：同表合并、粒度列自动补类型、GROUP BY 生成、层级依赖校验
- 两遍推导：先 DWS 后 ADS（ADS 指标可引用刚推导出的 DWS 表）
"""
from __future__ import annotations

import re

from schema import (
    ALLOWED_SRC_LAYERS,
    LAYER_ORDER,
    TIER_MAX_LAYER,
    Column,
    MetricSpec,
    SemanticSpec,
    TableSpec,
    check_expr_columns,
)

_LAYER_BY_PREFIX = {"dws_": "DWS", "ads_": "ADS"}


def _layer_of_table_name(table_name: str) -> str:
    for prefix, layer in _LAYER_BY_PREFIX.items():
        if table_name.startswith(prefix):
            return layer
    return ""


def _layer_of_source(full_name: str, spec: SemanticSpec, derived: list[TableSpec]) -> int | None:
    short = full_name.split(".")[-1]
    for table in spec.tables:
        if table.short_name == short:
            return LAYER_ORDER[table.layer]
    for table in derived:
        if table.short_name == short:
            return LAYER_ORDER[table.layer]
    if short.startswith("ods_"):
        return 0
    return None


def _check_group_consistency(table_name: str, group: list[MetricSpec], errors: list[str]) -> None:
    """同一张指标表内的指标必须同粒度、同来源、同过滤条件（否则无法合表）。"""
    first = group[0]
    for metric in group[1:]:
        if list(metric.grain) != list(first.grain):
            errors.append(
                f"{table_name}: 指标 {metric.metric_name} 粒度 {metric.grain} 与同表指标 "
                f"{first.metric_name} 粒度 {first.grain} 不一致，请拆表或统一粒度"
            )
        if list(metric.source_tables) != list(first.source_tables):
            errors.append(
                f"{table_name}: 指标 {metric.metric_name} 与 {first.metric_name} 来源不一致，不能合表"
            )
        if metric.where != first.where:
            errors.append(
                f"{table_name}: 指标 {metric.metric_name} 与 {first.metric_name} 过滤条件不一致，不能合表"
            )
        if metric.grain_exprs != first.grain_exprs:
            errors.append(
                f"{table_name}: 指标 {metric.metric_name} 与 {first.metric_name} grain_exprs 不一致，不能合表"
            )


def _resolve_grain_columns(
    table_name: str,
    group: list[MetricSpec],
    column_types: dict[str, dict[str, tuple[str, bool]]],
    errors: list[str],
    warnings: list[str],
) -> list[Column]:
    """粒度列 → 带类型的 Column（类型与可空性均来自来源表列定义）。

    可空性传播是执行正确性的关键：LEFT JOIN 未命中等场景下来源列可为 NULL，
    若粒度列强制 NOT NULL，Doris 严格模式写入 NULL 会触发 DATA_QUALITY_ERROR。
    """
    first = group[0]
    columns: list[Column] = []
    for grain_col in first.grain:
        info = None
        for source in first.source_tables:
            info = column_types.get(source, {}).get(grain_col)
            if info:
                break
        if not info:
            errors.append(
                f"{table_name}: 粒度列 {grain_col} 在来源表 {first.source_tables} 中不存在"
            )
            continue
        dtype, nullable = info
        if nullable and grain_col not in first.grain_exprs:
            warnings.append(
                f"{table_name}: 粒度列 {grain_col} 来源可空，NULL 将归入 NULL 分组；"
                f"如需清洗可在 grain_exprs 用 COALESCE"
            )
        columns.append(Column(name=grain_col, dtype=dtype, nullable=nullable))
    return columns


def _zero_grain_key_column(metric_names: list[str]) -> Column:
    """零粒度指标表需要非 FLOAT/DOUBLE 首列作为 Doris key。"""
    name = next(
        candidate for candidate in ("stat_date", "snapshot_date", "batch_date")
        if candidate not in metric_names
    )
    return Column(
        name=name,
        dtype="DATE",
        nullable=False,
        comment="统计日期（零粒度指标自动生成，用作 Doris key）",
    )


_AGG_CALL_RE = re.compile(r"\b(?:SUM|AVG)\s*\(", re.IGNORECASE)
_DECIMAL_TYPE_RE = re.compile(
    r"^\s*DECIMAL\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*$", re.IGNORECASE
)


def _widen_aggregate_decimal(metric: MetricSpec) -> tuple[str, str | None]:
    """聚合 DECIMAL 指标容量防线：窄精度自动加宽到 DECIMAL(38,S)。

    Doris 的 SUM/AVG(DECIMAL(P,S)) 中间类型是 DECIMAL(38,S)，目标列窄于它
    且结果超上限会运行时溢出（E-255 Arithmetic overflow）。
    返回 (落库 dtype, 加宽 warning 或 None)。
    """
    value_type = metric.value_type or "DOUBLE"
    if not _AGG_CALL_RE.search(metric.expr or ""):
        return value_type, None
    match = _DECIMAL_TYPE_RE.match(value_type)
    if not match:
        return value_type, None
    precision, scale = int(match.group(1)), int(match.group(2))
    if precision >= 38:
        return value_type, None
    widened = f"DECIMAL(38,{scale})"
    return widened, (
        f"{metric.table_name}.{metric.metric_name}: 聚合表达式 {metric.expr} 的 "
        f"value_type={value_type} 容量不足（Doris SUM/AVG 中间类型为 DECIMAL(38,S)，"
        f"窄列运行时可能 E-255 溢出），已自动加宽为 {widened}"
    )


def derive_tables_from_metrics(
    spec: SemanticSpec,
    biz_database: str,
    column_types: dict[str, dict[str, tuple[str, bool]]],
    tier: str,
) -> tuple[list[TableSpec], list[str], list[str]]:
    """metrics[] → DWS/ADS TableSpec 列表。

    column_types: 表全名 → {列名: (类型, 可空)}，含 plan ODS、复用 ODS、规格内 DWD 表；
    推导出的 DWS 会动态加入，供第二遍 ADS 引用。
    返回 (派生表, 错误清单, 警告清单)：错误即拒绝，警告供设计期提示。
    """
    errors: list[str] = []
    warnings: list[str] = []
    max_layer = TIER_MAX_LAYER.get(tier)
    if max_layer is None:
        return [], [f"未知 tier: {tier}"]

    # 基础校验 + 按落地表分组
    seen_metric: set[str] = set()
    groups: dict[str, list[MetricSpec]] = {}
    for metric in spec.metrics:
        if metric.metric_name in seen_metric:
            errors.append(f"指标名重复: {metric.metric_name}")
        seen_metric.add(metric.metric_name)
        layer = _layer_of_table_name(metric.table_name)
        if not layer:
            errors.append(f"{metric.metric_name}: table_name 必须以 dws_/ads_ 开头")
            continue
        if LAYER_ORDER[layer] > max_layer:
            errors.append(
                f"{metric.metric_name}: 落地表 {metric.table_name}（{layer}）超出 tier {tier} 允许范围"
            )
        if len(metric.source_tables) != 1:
            errors.append(
                f"{metric.metric_name}: 指标来源暂只支持单表（当前 {len(metric.source_tables)} 个）；"
                f"跨表指标请先在 tables[] 设计 DWD 宽表再引用"
            )
        missing_alias = [s for s in metric.source_tables if s not in metric.source_aliases]
        if missing_alias:
            errors.append(f"{metric.metric_name}: source_aliases 缺少 {missing_alias} 的别名")
        for key in metric.grain_exprs:
            if key not in metric.grain:
                errors.append(f"{metric.metric_name}: grain_exprs 键 {key} 不在 grain 中")
        groups.setdefault(metric.table_name, []).append(metric)

    for table_name, group in groups.items():
        _check_group_consistency(table_name, group, errors)

    # 与规格内显式表重名检查
    spec_shorts = {table.short_name for table in spec.tables}
    for table_name in groups:
        if table_name in spec_shorts:
            errors.append(f"指标表 {table_name} 与规格内显式表重名")

    derived: list[TableSpec] = []
    # 两遍推导：DWS 先（可源 ODS/DWD），ADS 后（可引用刚推导出的 DWS）
    for pass_layer in ("DWS", "ADS"):
        for table_name, group in groups.items():
            layer = _layer_of_table_name(table_name)
            if layer != pass_layer:
                continue
            first = group[0]
            # 来源存在性 + 层级依赖
            for source in first.source_tables:
                if source not in column_types:
                    errors.append(
                        f"{table_name}: 指标来源 {source} 不在可用表中"
                        "（plan ODS / 复用 ODS / 规格 DWD / 已推导 DWS）"
                    )
                    continue
                src_layer = _layer_of_source(source, spec, derived)
                if src_layer is None or src_layer not in ALLOWED_SRC_LAYERS[layer]:
                    allowed = "/".join(
                        name for name, idx in LAYER_ORDER.items()
                        if idx in ALLOWED_SRC_LAYERS[layer]
                    )
                    errors.append(
                        f"{table_name}: {layer} 层指标来源 {source} 跳层或同层引用（允许源层: {allowed}）"
                    )
            # 表达式列引用校验：expr / where / grain_exprs 中的 别名.列 必须真实
            # 存在于来源表列定义，拦截 ADS 引用 DWS 短列名（实际落列名为
            # metric_name）导致的 Doris 执行期 Unknown column
            effective_aliases = {
                source: first.source_aliases.get(source, source.split(".")[-1])
                for source in first.source_tables
            }
            for metric in group:
                check_expr_columns(
                    f"{table_name}.{metric.metric_name}",
                    metric.expr,
                    first.source_tables,
                    effective_aliases,
                    column_types,
                    errors,
                )
            check_expr_columns(
                f"{table_name}.where",
                first.where,
                first.source_tables,
                effective_aliases,
                column_types,
                errors,
            )
            for grain_col, grain_expr in first.grain_exprs.items():
                check_expr_columns(
                    f"{table_name}.{grain_col}",
                    grain_expr,
                    first.source_tables,
                    effective_aliases,
                    column_types,
                    errors,
                )
            grain_columns = _resolve_grain_columns(table_name, group, column_types, errors, warnings)
            metric_columns: list[Column] = []
            for metric in group:
                dtype, widen_warning = _widen_aggregate_decimal(metric)
                if widen_warning:
                    warnings.append(widen_warning)
                metric_columns.append(
                    Column(
                        name=metric.metric_name,
                        dtype=dtype,
                        nullable=True,
                        comment=f"{metric.display_name}：{metric.caliber}".rstrip("："),
                    )
                )
            key_columns = grain_columns
            key_exprs = dict(first.grain_exprs)
            if not grain_columns:
                key_columns = [_zero_grain_key_column([m.metric_name for m in group])]
                key_exprs = {key_columns[0].name: "CURRENT_DATE"}
            derived.append(TableSpec(
                short_name=table_name,
                layer=layer,
                columns=key_columns + metric_columns,
                source_tables=list(first.source_tables),
                source_aliases=dict(first.source_aliases),
                joins=[],
                column_exprs={**key_exprs,
                              **{metric.metric_name: metric.expr for metric in group}},
                group_by=list(first.grain),
                where=first.where,
                comment="指标表: " + ", ".join(metric.metric_name for metric in group),
            ))
            # 动态注册列类型，供第二遍 ADS 解析粒度列
            column_types[f"{biz_database}.{table_name}"] = {
                column.name: (column.dtype, column.nullable)
                for column in key_columns + metric_columns
            }

    # 维度可解析性：每个维度必须落在粒度或来源表列中
    for metric in spec.metrics:
        resolvable = set(metric.grain)
        for source in metric.source_tables:
            resolvable |= set(column_types.get(source, {}).keys())
        for dimension in metric.dimensions:
            if dimension not in resolvable:
                errors.append(
                    f"{metric.metric_name}: 维度 {dimension} 不在粒度或来源表列中，无法下钻"
                )
    return derived, errors, warnings
