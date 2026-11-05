"""spec 数据解析：evidence / sql / inline 三种来源统一成可渲染结构。"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from charts.options import ChartRequest, chart_option, json_safe, normalize_option

_SKILLS_DIR = Path(__file__).resolve().parents[2]
_DW_COMMON = _SKILLS_DIR / "_dw_common" / "scripts"

# dev 源码形态：从仓库根运行时补 sys.path，保证 report_gate 可导入；
# 运行时形态（skill 在用户目录、jiuwenswarm 已安装）不会命中。
for _parent in Path(__file__).resolve().parents:
    if (_parent / "jiuwenswarm" / "__init__.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break


def load_json(path: str | Path) -> dict[str, Any]:
    """读取 JSON 文件（utf-8-sig 兼容 BOM）。"""
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8-sig"))


def load_spec(path: str | Path) -> dict[str, Any]:
    """读取报告 spec。"""
    spec = load_json(path)
    if not isinstance(spec, dict):
        raise ValueError("spec 必须是 JSON 对象")
    return spec


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(
    spec: dict[str, Any], warnings: list[str], strict: bool
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None, Path | None]:
    raw = spec.get("manifest_path")
    if not raw:
        return {}, None, None
    path = Path(str(raw)).expanduser()
    if not path.is_file():
        message = f"manifest 不存在，跳过证据校验: {path}"
        if strict:
            raise ValueError(message)
        warnings.append(message)
        return {}, None, None
    manifest = load_json(path)
    evidence = {
        str(item.get("evidence_id")): item
        for item in manifest.get("evidence") or []
        if isinstance(item, dict) and item.get("evidence_id")
    }
    for index, section in enumerate(spec.get("sections") or []):
        for ref in section.get("evidence_refs") or []:
            if ref not in evidence:
                message = f"sections[{index}] 引用的证据不存在: {ref}"
                if strict:
                    raise ValueError(message)
                warnings.append(message)
    return evidence, manifest, path


def _write_json_atomic(path: Path, payload: Any) -> None:
    """原子写 JSON，避免中断留下半截文件。"""
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _persist_evidence_inputs(
    spec: dict[str, Any],
    manifest: dict[str, Any] | None,
    manifest_path: Path | None,
    gate: dict[str, Any] | None,
) -> None:
    """把可重判输入写回 analysis 目录：原始 spec 与带门禁结果的 manifest。

    报告正文之外的重判依据（证据引用、门禁结论）只落文件，不进用户产物。
    """
    if manifest is None or manifest_path is None:
        return
    _write_json_atomic(manifest_path.parent / "report_spec.json", spec)
    if gate is not None:
        _write_json_atomic(manifest_path, {**manifest, "report_gate": gate})


def _read_evidence(
    evidence: dict[str, dict[str, Any]],
    ref: str,
    manifest_dir: Path | None,
    warnings: list[str],
    strict: bool,
) -> pd.DataFrame | None:
    record = evidence.get(ref)
    if not record:
        return None
    raw = record.get("artifact")
    if not raw:
        warnings.append(f"证据 {ref} 没有 artifact 路径")
        return None
    path = Path(str(raw)).expanduser()
    if not path.is_absolute() and manifest_dir:
        path = manifest_dir / path
    path = path.resolve()
    if manifest_dir and not path.is_relative_to(manifest_dir.resolve()):
        raise ValueError(f"证据文件不属于本次分析: {ref}")
    if not path.is_file():
        message = f"证据 CSV 不存在: {path}"
        if strict:
            raise ValueError(message)
        warnings.append(message)
        return None
    expected = record.get("artifact_hash")
    if expected and _sha256(path) != expected:
        raise ValueError(f"证据 CSV 内容已变更: {path}")
    return pd.read_csv(path, encoding="utf-8-sig")


def _from_sql(db: str, sql: str) -> pd.DataFrame:
    if str(_DW_COMMON) not in sys.path:
        sys.path.insert(0, str(_DW_COMMON))
    import dw_client  # noqa: PLC0415

    result = dw_client.query(db, sql, limit=100000)
    return pd.DataFrame(result.get("rows") or [], columns=result.get("columns") or [])


def _chart_dataframe(
    section: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    manifest_dir: Path | None,
    db: str | None,
    warnings: list[str],
    strict: bool,
) -> pd.DataFrame | None:
    if evidence:
        refs = section.get("evidence_refs") or []
        primary = section.get("data_evidence_ref") or (refs[0] if refs else "")
        if primary:
            frame = _read_evidence(evidence, primary, manifest_dir, warnings, strict)
            if frame is not None:
                return frame
    sql = section.get("sql")
    source = section.get("db") or db
    if sql and source:
        return _from_sql(str(source), str(sql))
    if sql and not source:
        warnings.append(
            f"section {section.get('title', '')!r} 有 sql 但没有 --db 或 section.db"
        )
    return None


def _resolve_chart(
    section: dict[str, Any],
    index: int,
    inline: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    manifest_dir: Path | None,
    db: str | None,
    warnings: list[str],
    strict: bool,
) -> dict[str, Any] | None:
    chart = section.get("chart")
    if chart in (None, "", {}):
        return None
    if isinstance(chart, str):
        option = (inline.get("charts") or {}).get(chart)
        if option is None:
            warnings.append(f"sections[{index}] 的 chart id 在 data.json 中不存在: {chart}")
            return None
        return normalize_option(option)
    if not isinstance(chart, dict):
        warnings.append(f"sections[{index}].chart 类型不支持")
        return None
    if "baseOption" in chart or "series" in chart:
        return normalize_option(chart)
    frame = _chart_dataframe(section, evidence, manifest_dir, db, warnings, strict)
    if frame is None:
        warnings.append(
            f"sections[{index}] 需要从 CSV 构图，但拿不到数据（缺 manifest / --db / data.json）"
        )
        return None
    return chart_option(frame, ChartRequest.from_mapping(chart))


def _select_rows(
    frame: pd.DataFrame, selector: Any, index: int, warnings: list[str]
) -> pd.DataFrame:
    if isinstance(selector, dict):
        mask = pd.Series(True, index=frame.index)
        for key, value in selector.items():
            if key not in frame.columns:
                warnings.append(f"kpis[{index}].row 引用的列不存在: {key}")
                return frame.iloc[0:0]
            mask &= frame[key].astype(str) == str(value)
        return frame[mask]
    if isinstance(selector, int):
        return frame.iloc[[selector]]
    return frame


def _resolve_kpis(
    kpis: list[dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    manifest_dir: Path | None,
    warnings: list[str],
    strict: bool,
) -> None:
    for index, kpi in enumerate(kpis):
        column = kpi.get("value_column")
        refs = kpi.get("evidence_refs") or []
        if not column or not refs:
            continue
        frame = _read_evidence(evidence, refs[0], manifest_dir, warnings, strict)
        if frame is None or column not in frame.columns or frame.empty:
            warnings.append(f"kpis[{index}] 无法从证据读取 {column!r}")
            continue
        frame = _select_rows(frame, kpi.get("row"), index, warnings)
        if frame.empty:
            warnings.append(f"kpis[{index}].row 没有匹配行")
            continue
        if len(frame) > 1:
            warnings.append(
                f"kpis[{index}] 证据含 {len(frame)} 行，默认取最后一行；"
                "建议用 row 指定筛选条件"
            )
        value = json_safe(frame.iloc[-1][column])
        if kpi.get("value") is not None and str(kpi["value"]) != str(value):
            warnings.append(
                f"kpis[{index}] 的 value 与证据不一致，已按证据覆盖为 {value!r}"
            )
        kpi["value"] = value


def resolve(
    spec: dict[str, Any],
    data_path: str | Path | None = None,
    db: str | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """解析 spec，返回带内部字段的副本。

    Returns:
        原 spec 的深拷贝，额外带 ``_charts``（块 id → option）与 ``_warnings``。
    """
    resolved = copy.deepcopy(spec)
    warnings: list[str] = []
    inline = load_json(data_path) if data_path else {}
    evidence, manifest, manifest_path = _load_manifest(resolved, warnings, strict)
    manifest_dir = manifest_path.parent if manifest_path else None

    charts: dict[str, dict[str, Any]] = {}
    for index, section in enumerate(resolved.get("sections") or [], 1):
        sid = str(section.get("id") or f"s{index}")
        option = _resolve_chart(
            section, index, inline, evidence, manifest_dir, db, warnings, strict
        )
        if option:
            charts[sid] = option
        table = section.get("table")
        if not isinstance(table, dict):
            table_id = section.get("table_id")
            table = (inline.get("tables") or {}).get(table_id) if table_id else None
        if isinstance(table, dict):
            section["_table"] = table

    _resolve_kpis(resolved.get("kpis") or [], evidence, manifest_dir, warnings, strict)
    if manifest:
        try:
            from jiuwenswarm.channels.web.dataagent.analysis.open_analysis import (
                report_gate,
            )

            gate = report_gate(spec, manifest)
            resolved["report_gate"] = gate
            _persist_evidence_inputs(spec, manifest, manifest_path, gate)
            if not gate.get("ok"):
                warnings.append("report_gate 未通过，报告未满足证据门禁")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"report_gate 不可用: {exc}")
    resolved["_charts"] = charts
    resolved["_warnings"] = warnings
    return resolved
