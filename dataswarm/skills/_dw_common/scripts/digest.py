#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""digest.py —— 规范化序列化与摘要（dw-governance 共享契约，identity_version 绑定）。

供 finding_identity / capability / inspection_persist 等共用：
    canonical_json(obj)  规范化 JSON 字符串（键排序、紧凑分隔符、固定数字与 Unicode 规则）
    sha256_hex(data)     稳定 hex 摘要（默认取前 16 位短格式）

规范性约束（v1，改动即破坏 UID/fingerprint 兼容，必须升 identity_version）：
    - dict 键按 Unicode 码点排序（递归）。
    - 分隔符紧凑：',' 与 ':'，无空白。
    - 字符串 UTF-8 直接编码（ensure_ascii=False），不接受非常规 NaN/Inf。
    - 数值：int 原样；float 用 repr(x) 文本化，避免平台 dumps 差异；
      bool 先于 int 序列化语义不变（JSON true/false）。
    - None -> null，list/tuple 一律按 JSON 数组处理（tuple 转 list，避免 `(1,)` 差异）。
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from typing import Any

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _canonicalize(value: Any) -> Any:
    """把任意结构规整成「可直接 json.dumps」的确定性形态。"""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # NaN/Infinity 不是合法 JSON，统一折叠成字符串占位（不该出现在契约数据里）
        if math.isnan(value) or math.isinf(value):
            return f"<invalid-float:{value!r}>"
        return repr(value)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return {str(k): _canonicalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    # 其余类型（datetime/Decimal/set…）不允许进入契约载荷：显式报错而不是静默 str()
    # ——静默 str() 会随类型实现漂移，破坏指纹稳定性。
    raise TypeError(f"canonical_json 不支持的类型: {type(value).__name__}")


def canonical_json(obj: Any) -> str:
    """规范化 JSON 字符串。相同逻辑内容（dict 键序无关）产出逐字节一致的文本。"""
    return json.dumps(_canonicalize(obj), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: str | bytes, short: bool = True) -> str:
    """sha256 hex 摘要。short=True 取前 16 位（UID/fingerprint 后缀惯例）。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    h = hashlib.sha256(data).hexdigest()
    return h[:16] if short else h


def digest_obj(obj: Any, short: bool = True) -> str:
    """canonical_json + sha256 一步到位。"""
    return sha256_hex(canonical_json(obj), short=short)
