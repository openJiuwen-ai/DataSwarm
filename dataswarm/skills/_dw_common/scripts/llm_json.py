"""LLM 生成 JSON 的标准化入口。

模型产出的 JSON 有三类典型漂移：

1. 包裹 markdown 围栏或在前后附带解释文字；
2. 尾逗号、单引号、被截断等语法问题；
3. 字段类型漂移（数组写成单个对象 / 单个字符串）。

``loads()`` 负责 1、2（``json_repair`` 兜底），``as_items`` / ``as_dicts`` /
``as_text_items`` 负责 3；无法兼容的结构走 ``LLMJsonError``，由调用方决定
是「大声失败」还是回喂给模型重试。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

_FENCE_RE = re.compile(r"^\s*```[ \t]*(?:json|json5)?[ \t]*\n?(.*?)\n?[ \t]*```\s*$", re.S | re.I)
_TOP_LEVEL = ("dict", "list", "any")
_MISSING = object()  # json.loads 的哨兵：None 是合法 JSON 值（null）

logger = logging.getLogger("jiuwen.llm_json")


class LLMJsonError(ValueError):
    """LLM 生成的 JSON 无法解析或不符合类型契约。"""


def record_warning(warnings: list[str] | None, message: str) -> None:
    """记录一次「靠容错才继续」的告警。

    调用方持有告警列表时追加进去（可随结果返回给上层）；否则写 stderr 日志。
    绝不写 stdout——skill 脚本的 stdout 是与 agent 的契约。
    """
    if warnings is None:
        logger.warning("[llm_json] %s", message)
    else:
        warnings.append(message)


def _strip_fence(text: str) -> str:
    match = _FENCE_RE.match(text)
    return match.group(1) if match else text


def _outermost_span(text: str) -> str:
    """截取首个 ``{`` / ``[`` 到最后一个配对闭合符之间的片段。"""
    starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
    if not starts:
        return text
    start = min(starts)
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    return text[start:end + 1] if end > start else text


def _repair(text: str) -> tuple[Any, str | None]:
    try:
        from json_repair import loads as repair_loads  # noqa: PLC0415
    except ImportError:
        return None, "json_repair 不可用"
    try:
        return repair_loads(text), None
    except Exception as exc:  # noqa: BLE001 - 第三方库错误类型不稳定
        return None, str(exc)


def _try_load(text: str) -> tuple[Any, str | None]:
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return _MISSING, exc.msg


def _check(parsed: Any, expect: str | None) -> Any:
    if expect == "dict" and not isinstance(parsed, dict):
        raise LLMJsonError(f"顶层必须是对象，实际是 {type(parsed).__name__}")
    if expect == "list" and not isinstance(parsed, list):
        raise LLMJsonError(f"顶层必须是数组，实际是 {type(parsed).__name__}")
    return parsed


def loads(text: str, expect: str | None = None,
          warnings: list[str] | None = None) -> Any:
    """把模型输出标准化为 Python 对象。

    每次启用容错手段（剥围栏 / 截取片段 / json_repair）都会记录告警，
    便于追踪模型输出的稳定性。

    Args:
        text: 模型原始输出，可以是裸 JSON、围栏包裹或带前后说明文字。
        expect: ``"dict"`` / ``"list"`` / ``None``，校验顶层类型。
        warnings: 告警收集列表；不传则写 ``jiuwen.llm_json`` 的 warning 日志。

    Returns:
        解析后的对象。

    Raises:
        LLMJsonError: 三种解析手段都失败，或顶层类型不符。
    """
    if expect is not None and expect not in _TOP_LEVEL:
        raise ValueError(f"expect 只支持 {_TOP_LEVEL}")
    raw = (text or "").strip()
    if not raw:
        raise LLMJsonError("JSON 内容为空")
    if raw.startswith("﻿"):  # stdin / 非 utf-8-sig 读取路径残留的 BOM
        raw = raw.lstrip("﻿").lstrip()
    if raw.startswith("```"):
        raw = _strip_fence(raw)
        record_warning(warnings, "输入带 markdown 围栏，已剥离后解析")

    parsed, error = _try_load(raw)
    if parsed is not _MISSING:
        return _check(parsed, expect)

    span = _outermost_span(raw)
    if span != raw:
        parsed, _ = _try_load(span)
        if parsed is not _MISSING:
            record_warning(warnings, "输入混有说明文字，已截取 JSON 片段解析")
            return _check(parsed, expect)

    repaired, repair_error = _repair(raw)
    if repair_error:
        raise LLMJsonError(
            "不是合法 JSON：" + "；".join(item for item in (error, repair_error) if item)
        )
    record_warning(warnings, f"JSON 语法不合法（{error}），已用 json_repair 修复")
    return _check(repaired, expect)


def load_file(path: str | Path, expect: str | None = None,
              warnings: list[str] | None = None) -> Any:
    """读取 JSON 文件并标准化（utf-8-sig 兼容 BOM）。"""
    file_path = Path(path).expanduser()
    return loads(file_path.read_text(encoding="utf-8-sig"), expect=expect, warnings=warnings)


def as_items(value: Any) -> list[Any]:
    """把「可能是单值」的字段归一化为列表。"""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def as_dicts(value: Any) -> list[dict[str, Any]]:
    """归一化为对象列表，非对象条目丢弃。"""
    return [item for item in as_items(value) if isinstance(item, dict)]


def as_text_items(value: Any) -> list[Any]:
    """文本条目：字符串按单条处理，空值丢弃。"""
    return [item for item in as_items(value) if item not in (None, "")]


def require(value: Any, expect: type | tuple[type, ...], path: str) -> Any:
    """类型断言，失败时报出字段路径（供「大声失败」场景使用）。"""
    if not isinstance(value, expect):
        names = " / ".join(item.__name__ for item in (expect if isinstance(expect, tuple) else (expect,)))
        raise LLMJsonError(f"{path}: 必须是 {names}，实际是 {type(value).__name__}")
    return value
