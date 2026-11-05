# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""flow —— analysis_flow.py 的分层包（重构 Phase 2 起逐层拆出）。

调用形态不变：入口仍是 `python scripts/analysis_flow.py <cmd>`，本包只在
脚本目录内就近解析（与 _dw_common 同一原则），dev（resources/.../scripts/）
与 runtime（~/.dataswarm/.../scripts/）布局一致。

分层（Phase 2 目标态，逐子提交落位）:
    dwcli_client    ① dwcli/analysis 子进程封装（服务端调用唯一出口）
    manifest        ② 账本：原子写/跨进程锁/进程耗时记账
    budget          预算判定（统一后的 _work_allowed，单一判据）
    execute_core    ③ 单成分执行核心（执行→记账→evidence 管线）
    batch           ④ 批量编排（线程池并行度 2/容量裁剪）
    commands        子命令薄实现
    observability   ⑤ hints 注入 + llm_trace/stdout_trace 采集
    cli_registry    ⑥ 声明式子命令注册表
"""
