"""模型生成 spec 的归一化容忍层。

spec 由 LLM 生成，数组字段偶发被写成字符串或单对象。直接迭代字符串会把内容
逐字符拆条（如「结论摘要」只剩首字），单对象字段则被逐 key 迭代。这里复用
``_dw_common/llm_json.py`` 的收敛规则：单值当一条，非对象条目丢弃；无法兼容
的结构交给 plan_schema 大声失败。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_DW_COMMON = Path(__file__).resolve().parents[3] / "_dw_common" / "scripts"
if str(_DW_COMMON) not in sys.path:
    sys.path.insert(0, str(_DW_COMMON))

from llm_json import as_dicts, as_items, as_text_items, record_warning  # noqa: E402

_TEXT_FIELDS = ("summary", "insights", "actions", "limitations")
_SECTION_LIST_FIELDS = (
    "narrative",
    "alternatives",
    "alternative_checked",
    "drivers",
    "annotations",
    "evidence_refs",
)


def _note_drift(warnings: list[str] | None, path: str, value: Any, unit: str) -> None:
    """字段本应是数组却收到单值：记录一次容忍告警（幂等归一化后不会重复）。"""
    if isinstance(value, str):
        kind = "字符串"
    elif isinstance(value, dict):
        kind = "对象"
    else:
        return
    record_warning(warnings, f"{path}: 期望数组，收到{kind}（已按{unit}处理）")


def _normalize_table(table: Any, warnings: list[str] | None, path: str) -> None:
    if not isinstance(table, dict):
        return
    for field in ("columns", "rows"):
        if field in table:
            _note_drift(warnings, f"{path}.{field}", table[field], "单条")
            table[field] = as_items(table[field])


def normalize_spec(spec: dict[str, Any], warnings: list[str] | None = None) -> dict[str, Any]:
    """就地归一化 spec 的列表字段，返回同一对象。

    幂等；只改类型不裁剪内容，语义由渲染层决定。发生类型漂移时记录告警：
    调用方传 ``warnings`` 则追加进去，否则写 ``jiuwen.llm_json`` 日志。
    """
    _note_drift(warnings, "sections", spec.get("sections"), "单节")
    _note_drift(warnings, "kpis", spec.get("kpis"), "单条")
    spec["sections"] = as_dicts(spec.get("sections"))
    spec["kpis"] = as_dicts(spec.get("kpis"))
    for field in _TEXT_FIELDS:
        if field in spec:
            _note_drift(warnings, field, spec[field], "单条")
            spec[field] = as_text_items(spec[field])

    for index, section in enumerate(spec["sections"]):
        prefix = f"sections[{index}]"
        for field in _SECTION_LIST_FIELDS:
            if field in section:
                _note_drift(warnings, f"{prefix}.{field}", section[field], "单条")
                section[field] = as_items(section[field])
        compare = section.get("compare")
        if isinstance(compare, dict):
            for side in ("a", "b"):
                item = compare.get(side)
                if isinstance(item, dict) and "items" in item:
                    _note_drift(warnings, f"{prefix}.compare.{side}.items", item["items"], "单条")
                    item["items"] = as_items(item["items"])
        for key in ("table", "_table"):
            _normalize_table(section.get(key), warnings, f"{prefix}.{key}")
    return spec
