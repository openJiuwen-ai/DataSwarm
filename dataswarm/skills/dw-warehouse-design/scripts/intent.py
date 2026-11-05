# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""意图分类器 —— 规则优先的确定性意图识别。

设计目标：
- 纯函数、零外部依赖、零 LLM 调用：明确意图毫秒级分类，结果稳定可单测。
- 输出 action + 最小生命周期 tier，驱动"只导入不分层"等最小路径。

用法：
    python intent.py --text "把 D:/data 的 CSV 导入进来"
    python intent.py --text "确认执行" --plan-id plan_abc123
"""
from __future__ import annotations

import argparse
import json
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TIER_LAYERS = {
    "L1_INGEST": ["ODS"],
    "L2_MODELING": ["ODS", "DWD"],
    "L3_FULL_DW": ["ODS", "DWD", "DWS", "ADS"],
}

_EXECUTE_VERBS = {"执行": 3, "跑一下": 3, "开始建表": 3, "运行": 2, "上线": 2, "run": 2, "publish": 3}
_PLAN_REFS = {"方案": 2, "plan": 2, "plan_id": 3, "计划": 1}
_APPEND_WORDS = {"追加": 3, "加表": 3, "补一张": 3, "补表": 3, "加一张": 3, "新增表": 3, "append": 3, "再加": 2}

# 强层级关键词：明确点名某层/某种建设深度
_TIER_STRONG = {
    "L1_INGEST": {"ods": 3, "贴源层": 3, "贴源": 2, "只导入": 4, "仅导入": 4, "只要导入": 4},
    "L2_MODELING": {"dwd": 3, "明细层": 3, "明细": 2, "建模": 2, "清洗": 2, "模型层": 2},
    "L3_FULL_DW": {
        "建仓": 3, "建数仓": 4, "搭建数仓": 4, "建设数仓": 4, "数仓分层": 4, "分层": 3,
        "数据仓库": 3, "dws": 3, "ads": 3, "汇总层": 3, "应用层": 3, "主题域": 2, "指标层": 2,
        "指标": 2, "metric": 2, "看板": 2, "报表": 2,
    },
}
# 弱信号："数仓"一词在导入语境下通常只是目的地，不代表要全量分层
_L3_WEAK = {"数仓": 1, "仓库": 1, "warehouse": 1}
_INGEST_WORDS = {
    "导入": 2, "导数据": 2, "导进来": 2, "装载": 2, "灌数": 1, "入库": 1,
    "load": 2, "ingest": 3, "import": 2,
    "接入": 2, "接进来": 2, "搬进来": 2, "导表": 2, "上载": 1, "upload": 2,
}


def _score(text: str, words: dict[str, int]) -> int:
    return sum(weight for word, weight in words.items() if word in text)


def classify(text: str, plan_id: str = "") -> dict:
    """分类用户意图。plan_id 非空表示会话中已有方案（提升 EXECUTE 判定置信度）。"""
    t = (text or "").strip().lower()

    append_score = _score(t, _APPEND_WORDS)
    exec_score = _score(t, _EXECUTE_VERBS)
    plan_score = _score(t, _PLAN_REFS)
    if plan_id:
        plan_score = max(plan_score, 3)

    if append_score >= 3:
        return _result("APPEND", "", 0.95, "检测到追加表意图（走设计流程生成只含新表的 plan，确认后转 append）")
    if exec_score >= 2 and plan_score >= 2:
        return _result("EXECUTE", "", 0.95, "检测到执行既有方案的意图")

    tier_scores = {tier: _score(t, words) for tier, words in _TIER_STRONG.items()}
    ingest_score = _score(t, _INGEST_WORDS)
    weak_dw = _score(t, _L3_WEAK) > 0

    # 强层级词命中：取用户提到的最高层（说 ODS+DWD → L2）
    if any(tier_scores.values()):
        if tier_scores["L3_FULL_DW"]:
            tier, confidence = "L3_FULL_DW", 0.95
        elif tier_scores["L2_MODELING"]:
            tier, confidence = "L2_MODELING", 0.95
        else:
            tier, confidence = "L1_INGEST", 0.95
        reason = f"命中层级关键词 → {tier}"
        if ingest_score and tier != "L1_INGEST":
            confidence, reason = 0.75, f"导入词与层级词并存，按更高层级 {tier} 处理"
        return _result("BUILD", tier, confidence, reason)

    # 弱层级词（"数仓"）+ 导入词 → 通常只是"导入到数仓"，走最小路径
    if weak_dw and ingest_score:
        return _result(
            "BUILD", "L1_INGEST", 0.8,
            "『数仓』在导入语境下按仅导入处理（L1）；如需 DWD/DWS/ADS 分层，用户会显式说明",
        )
    if weak_dw:
        return _result("BUILD", "L3_FULL_DW", 0.8, "仅提到数仓建设（无导入细节），按全量分层处理")

    if ingest_score:
        return _result("BUILD", "L1_INGEST", 0.95, "仅检测到导入意图 → 最小路径 L1（只建 ODS，不分层）")

    if exec_score >= 3:
        return _result("UNKNOWN", "", 0.0, "仅有执行动词、无方案引用；请确认要执行哪个 plan_id")
    result = _result(
        "UNKNOWN", "", 0.0,
        "未识别到数仓建设意图；请澄清：仅导入 / 建模到 DWD / 全量分层 / 执行方案 / 追加表",
    )
    result["llm_fallback"] = (
        "规则未命中。初始请求可由执行 agent 基于语义预判意图（仅导入/建模到 DWD/全量分层/执行方案/追加表/无关），"
        "但已有待确认方案和唯一 plan_id 时，确认回复由大模型结合上一轮方案上下文直接判断；"
        "确认、执行、确认执行、OK 等同义确认可直接 handoff，不要求本脚本命中 EXECUTE。"
    )
    return result


def _result(action: str, tier: str, confidence: float, reason: str) -> dict:
    out: dict = {
        "action": action,
        "tier": tier,
        "confidence": confidence,
        "reason": reason,
        "ask_user": action == "UNKNOWN" or confidence < 0.6,
    }
    if tier:
        out["target_layers"] = TIER_LAYERS[tier]
        if action == "BUILD":
            out["next"] = {
                "L1_INGEST": "discovery → build plan（自动，无需语义规格）",
                "L2_MODELING": "discovery → 写语义规格（含 DWD）→ build plan",
                "L3_FULL_DW": "discovery → 写语义规格（DWD/DWS/ADS）→ build plan",
            }[tier]
    if action == "EXECUTE":
        out["next"] = "转 dw-warehouse-build 执行: execute_pipeline.py --action run --plan-id <id>（需用户已确认）"
    if action == "APPEND":
        out["next"] = ("走本 skill 设计流程：spec → build → 只含新表的 plan → 确认门；"
                       "确认后从新 plan 提取 tables 转 table-spec，用旧 plan_id 转 dw-warehouse-build append（严禁 run 新 plan_id）")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="数仓建设意图分类器（规则优先）")
    parser.add_argument("--text", required=True,
                        help="用户原话（含空格/中文时必须整体用双引号包裹；"
                             "单引号是 POSIX 专用，Windows cmd 会把参数拆散）")
    parser.add_argument("--plan-id", default="", help="会话中已有 plan_id（提升 EXECUTE 置信度）")
    args = parser.parse_args()
    print(json.dumps(classify(args.text, args.plan_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
