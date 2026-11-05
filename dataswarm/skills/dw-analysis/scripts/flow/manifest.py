#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""manifest.py —— 分析账本：原子写、跨进程锁、进程耗时记账。

从 analysis_flow.py 抽出（Phase 2a 纯搬运，行为零变化）。manifest 是
四层链路的唯一账本（预算/轮次/检查结果以它为准），本模块负责它的
全部读写可靠性：temp+os.replace 原子写、flock/msvcrt 跨进程锁防
lost update、exec_ms 进程级一次性记账。
"""
from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import sys

# dw_paths：运行时根目录解析（_dw_common/scripts，env 优先）
_DW_COMMON_DIR = (Path(__file__).resolve().parent.parent.parent.parent
                  / "_dw_common" / "scripts")
if str(_DW_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_DW_COMMON_DIR))
import dw_paths  # noqa: E402


ARTIFACT_ROOT = Path(os.environ.get(
    "JIUWENSWARM_ARTIFACT_DIR",
    dw_paths.workspace_root() / "artifact" / "analysis",
)).expanduser().resolve()


def _dir(analysis_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", analysis_id):
        raise SystemExit("[analysis_flow] analysis_id 格式非法")
    return ARTIFACT_ROOT / analysis_id


def _manifest_path(analysis_id: str) -> Path:
    return _dir(analysis_id) / "manifest.json"


def _load(analysis_id: str) -> dict:
    path = _manifest_path(analysis_id)
    if not path.exists():
        raise SystemExit(f"[analysis_flow] analysis_id 不存在: {analysis_id}（先 init）")
    return json.loads(path.read_text("utf-8-sig"))


def _save(manifest: dict) -> None:
    """原子写 manifest：先写临时文件再 rename。

    非原子 write_text 在并发写（重试风暴 / 多用例抢同一 analysis_id）时会截断或
    交错，产出含非 UTF-8 字节 /「Extra data」的损坏账本——实测 agent 被迫自写
    repair_manifest.py 修。temp+os.replace 保证读者永远看到完整内容。

    同时把本次进程耗时累进 budget.exec_ms（只记一次）：deadline 语义 =
    脚本累计执行时间，而非会话墙钟——对话式编排每轮 LLM/工具往返 5-15s，
    墙钟判据下 5 个成分编译完必报 deadline_exhausted（思考链实证：模型被逼
    用单条巨型命令串完整条链路来绕过轮耗）。
    """
    global _PROC_ACCOUNTED
    if not _PROC_ACCOUNTED:
        budget = manifest.setdefault("budget", {})
        budget["exec_ms"] = int(budget.get("exec_ms", 0) + (time.monotonic() - _PROC_START) * 1000)
        _PROC_ACCOUNTED = True
    path = _manifest_path(manifest["analysis_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest, ensure_ascii=False, indent=2)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(payload, "utf-8")
        # Windows 上目标文件被杀软/索引器/并发读者短暂占用时 os.replace 抛
        # WinError 5（smoke 实证：trace 记账失败重试一次即过）——退避重试，
        # 连续失败才放弃，避免一次偶发竞态烧掉编排层一整轮。
        for attempt in range(3):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.2 * (attempt + 1))
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _component(manifest: dict, component_id: str) -> dict:
    components = manifest.setdefault("components", {})
    if component_id not in components:
        components[component_id] = {
            "status": "planned",
            "attempts": [],
            "explore_rounds": [],
            "sufficiency_trace": [],
            "retries": 0,
        }
    return components[component_id]


def _planned(manifest: dict, component_id: str) -> dict:
    """从 plan.components 取成分定义（Phase 2b 自 analysis_flow 迁入）。"""
    for item in (manifest.get("plan") or {}).get("components") or []:
        if item["component_id"] == component_id:
            return item
    raise SystemExit(f"[analysis_flow] 成分 {component_id} 不在计划内")


def _skip(manifest: dict, component_id: str, reason: str) -> None:
    """optional 成分显式跳过并写 uncovered（Phase 2b 自 analysis_flow 迁入）。"""
    state = _component(manifest, component_id)
    state.update(status="skipped", skip_reason=reason)
    manifest.setdefault("uncovered", [])
    note = f"{component_id}: {reason}"
    if note not in manifest["uncovered"]:
        manifest["uncovered"].append(note)


# --------------------------------------------------------------------------
# 跨进程 manifest 账本锁（lost update 防护）
# --------------------------------------------------------------------------
if os.name == "nt":
    import msvcrt

    def _lock_try(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _lock_release(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_try(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _lock_release(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _manifest_lock(analysis_id: str, timeout: float = 300.0):
    """跨进程 manifest 账本锁：编排层一轮并行发起多个 analysis_flow 进程
    （如 G1 通道并行 compile c1/c2/c3）时，各进程「load → 改 → save」
    全程互斥，防止后落盘进程拿过期快照整体覆盖先落盘进程的
    attempt/evidence（lost update——实测三成分并行后账本只剩最后落盘
    者的记录，被迫串行重跑补账多烧 6+ 轮 LLM）。

    内核级锁随进程退出自动释放，lock 文件常驻无残留死锁；超时抛错而非
    挂死。获得锁后重置 _PROC_START：锁排队等待不计入 budget.exec_ms
    （三进程串行排队累计会误撞 deadline_ms 红线）。
    """
    global _PROC_START
    lock_path = Path(str(_manifest_path(analysis_id)) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, mode=0o600)
    locked = False
    deadline = time.monotonic() + timeout
    try:
        while not _lock_try(fd):
            if time.monotonic() >= deadline:
                raise SystemExit(
                    f"[analysis_flow] manifest 锁等待超时 {timeout:.0f}s"
                    f"（analysis_id={analysis_id}）——检查是否有并行 analysis_flow 进程挂死")
            time.sleep(0.2)
        locked = True
        _PROC_START = time.monotonic()
        yield
    finally:
        if locked:
            try:
                _lock_release(fd)
            except OSError:
                # 未持有/已失效（超时路径从未锁定、或内核已回收）——内核级锁
                # 随进程退出自动释放，此处吞掉不掩盖业务异常。
                pass
        os.close(fd)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# 本次进程起点与记账标志（budget.exec_ms 累计用，见 _save docstring）
_PROC_START = time.monotonic()
_PROC_ACCOUNTED = False


def proc_elapsed_ms() -> float:
    """本次进程自 _PROC_START 起的毫秒数（_manifest_lock 获锁时会重置起点）。

    跨模块读取口：_work_allowed 等判定方经此函数读，避免 from-import
    快照旧值——测试 monkeypatch 本模块 _PROC_START 后此处即时生效。
    """
    return (time.monotonic() - _PROC_START) * 1000
