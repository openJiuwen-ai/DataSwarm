#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""batch.py —— 批量编排：线程池并行调服务端（Phase 2c 迁入）。

对 dwcli 的调用经模块属性（``_dc._analysis``）——测试打桩 seam。
"""
from __future__ import annotations

from . import dwcli_client as _dc


BATCH_PARALLELISM = 2


def _run_batch(items: list[dict], action: str, parallelism: int = BATCH_PARALLELISM) -> dict[int, dict]:
    """线程池并行调服务端（每项一个 dwcli 子进程）；返回 {序号: 响应}。

    失败的项以 {"ok": False, "error": ...} 占位——批量语义下逐项独立成败。
    """
    from concurrent.futures import ThreadPoolExecutor

    results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = {
            index: pool.submit(_dc._analysis, "analysis", action, item)
            for index, item in enumerate(items)
        }
        for index, future in futures.items():
            try:
                results[index] = future.result()
            except Exception as exc:  # noqa: BLE001 —— 单项失败不拖垮整批
                results[index] = {"ok": False, "error": str(exc)}
    return results
