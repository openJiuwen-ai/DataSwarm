#!/usr/bin/env python
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""复测 EMB 召回的余弦分布，校验 metrics/embedding.py 的校准常量。

校准常量（``EMB_SIM_FLOOR`` / ``EMB_SIM_CEIL``）是**模型相关**的：换 embed
模型、换语料形态（列注释有无）都会移动余弦分布，常量必须重测。本脚本用
真实端点 + 真实工作空间文档跑一遍，打印分布分位、off-topic 上界与「强命中
能否越过 0.60 澄清阈值」的判定。

用法::

    python scripts/verify_metrics_emb_calibration.py --workspace-id metrics-demo
    python scripts/verify_metrics_emb_calibration.py --questions "各渠道成交额,库存周转天数"

只读：不写向量缓存、不改任何表。embed 端点不可用时**大声失败**（exit 2），
不静默退化成 mock——mock 向量是哈希随机数，量出来的分布毫无意义。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jiuwenswarm.channels.web.dataagent.metrics import embedding  # noqa: E402

# 无关问题（负对照）：它们的余弦上界决定 floor 能压到多低
OFF_TOPIC = ("今天天气怎么样", "帮我写一首诗", "怎么申请年假")
# 业务问题（正对照）：真实问法，用来看强命中落在哪
BUSINESS = ("每个地区卖了多少钱", "订单的支付状态有哪些取值", "客单价")

FALLBACK_DOCS = (
    ("metric:demo.gmv", "支付成交额 commerce.gmv GMV 成交额 已支付订单含税成交额 CNY"),
    ("metric:demo.aov", "支付客单价 commerce.aov AOV 平均每单金额 CNY/order"),
    ("col:orders.region", "orders.region 地区 订单所属销售地区"),
    ("col:orders.amount", "orders.amount_cny 订单金额 订单金额（人民币，含税）"),
    ("col:orders.channel", "orders.channel 渠道 下单渠道来源"),
    ("col:orders.bare", "orders.pay_status pay_status"),
)


def _real_docs(workspace_id: str) -> tuple[list[tuple[str, str]], str]:
    """取工作空间的真实文档；取不到就退回内置语料（并说明原因）。"""
    from jiuwenswarm.channels.web.dataagent.metrics import store

    docs: list[tuple[str, str]] = list(embedding.metric_docs(workspace_id))
    row = store.current_schema_snapshot(workspace_id)
    snapshot_id = str((row or {}).get("snapshot_id") or "")
    if snapshot_id:
        docs.extend(embedding.schema_docs(workspace_id, snapshot_id))
    if not docs:
        return list(FALLBACK_DOCS), "工作空间无已发布指标/schema 文档，改用内置语料"
    return docs, f"真实文档（指标 + schema snapshot {snapshot_id[:12]}）"


def _percentiles(values: list[float]) -> str:
    ordered = sorted(values)
    if not ordered:
        return "（无数据）"
    parts = []
    for pct in (0, 25, 50, 75, 90, 100):
        idx = min(len(ordered) - 1, int(pct / 100 * (len(ordered) - 1)))
        parts.append(f"p{pct}={ordered[idx]:.3f}")
    return "  ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace-id", default="", help="工作空间 ID（默认取当前上下文）")
    parser.add_argument("--questions", default="", help="逗号分隔的追加探测问题")
    parser.add_argument("--top", type=int, default=3, help="每个问题打印的命中数")
    args = parser.parse_args()

    # config.yaml 的 embed: 段用 ${EMBED_API_BASE} 这类占位符，值在 runtime .env 里；
    # 独立脚本不经 app.py 启动，必须自己加载（--dotenv 优先，否则用默认 config/.env）
    from jiuwenswarm.common.utils import get_env_file
    from jiuwenswarm.dotenv_early import load_dotenv_runtime, parse_dotenv_early

    if not parse_dotenv_early("verify-metrics-emb"):
        env_file = get_env_file()
        if env_file.is_file():
            load_dotenv_runtime(env_file, override=False)

    if not embedding.emb_available():
        print("[FAIL] EMB 通道不可用：检查 config.yaml 的 embed: 段（api_key/base_url/model）"
              "或 .env 的 EMBED_API_KEY/EMBED_API_BASE/EMBED_MODEL；"
              f"也可能是 {embedding.EMB_ENABLED_ENV}=false 显式关闭了", file=sys.stderr)
        return 2

    workspace_id = args.workspace_id
    from jiuwenswarm.common import workspace_context

    if workspace_id:
        # store/schema_docs 都经 current_workspace() 解析元库 ref，
        # 光传参数不切上下文会读到别的工作空间的指标
        workspace_context.set_current(workspace_id)
    else:
        workspace_id = workspace_context.current_workspace().workspace_id
    docs, source = _real_docs(workspace_id)
    extra = [q.strip() for q in args.questions.split(",") if q.strip()]
    questions = [*BUSINESS, *extra]

    print(f"模型: {embedding.emb_model()}   文档源: {source}   文档数: {len(docs)}")
    print(f"校准常量: floor={embedding.EMB_SIM_FLOOR} ceil={embedding.EMB_SIM_CEIL} "
          f"澄清阈值=0.60\n")

    outcome = embedding.embed_texts([t for _, t in docs])
    if not outcome.ok:
        print(f"[FAIL] 文档嵌入失败: {outcome.error}", file=sys.stderr)
        return 2
    doc_vectors = outcome.vectors
    print(f"文档嵌入完成: dims={outcome.dims} provider_model={outcome.model}\n")

    all_cos: list[float] = []
    off_topic_max = 0.0
    strong_hit_max = 0.0
    for group, label in ((questions, "业务问题"), (list(OFF_TOPIC), "负对照")):
        print(f"===== {label} =====")
        for question in group:
            # 与生产同路径：整句 + jieba 短语多向量，逐文档取最大校准分
            # （整句/短语各用自己的 floor，见 embedding.EMB_PHRASE_FLOOR）
            q_outcome = embedding.query_vectors(question)
            if not q_outcome.ok:
                print(f"[FAIL] 问题嵌入失败: {q_outcome.error}", file=sys.stderr)
                return 2
            qvecs = q_outcome.vectors
            scored = []
            for key, dv in zip([k for k, _ in docs], doc_vectors):
                score, _idx, raw = embedding.score_against(qvecs, dv)
                scored.append((score, raw, key))
            scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
            all_cos.extend(raw for _s, raw, _k in scored)
            top = scored[:args.top]
            if label == "负对照":
                off_topic_max = max(off_topic_max, top[0][0] if top else 0.0)
            else:
                strong_hit_max = max(strong_hit_max, top[0][0] if top else 0.0)
            shown = "  ".join(
                f"{key}=校准 {score:.2f}(余弦 {raw:.3f})"
                f"{'✓过阈值' if score >= 0.60 else ''}"
                for score, raw, key in top)
            print(f"Q: {question}（{len(qvecs)} 个查询向量）\n   {shown}")
        print()

    print(f"===== 余弦分布（n={len(all_cos)}）=====")
    print(f"  {_percentiles(all_cos)}")
    print(f"\n负对照校准上界 = {off_topic_max:.2f}   业务问题校准上界 = {strong_hit_max:.2f}")

    ok = True
    if off_topic_max > 0.0:
        print(f"[WARN] 负对照校准分 {off_topic_max:.2f} > 0：无关问题拿到了 EMB 分，"
              f"应上调 {embedding.EMB_FLOOR_ENV} / {embedding.EMB_PHRASE_FLOOR_ENV}")
        ok = False
    if strong_hit_max < 0.60:
        print(f"[WARN] 最强业务命中校准 {strong_hit_max:.2f} < 0.60：EMB 无法单独触发路由，"
              "只能作为排序信号（裸列名语料的典型表现，见 embedding.py 模块 docstring）")
    if ok:
        print(f"[OK] floor（整句 {embedding.EMB_SIM_FLOOR} / 短语 {embedding.EMB_PHRASE_FLOOR}）"
              f"对该模型/语料有效：负对照（校准上界 {off_topic_max:.2f}）全部压零")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
