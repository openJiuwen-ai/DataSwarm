# -*- coding: utf-8 -*-
"""DataSwarm P1/P2/P3 端到端冒烟（真实 MySQL 元库 + 真实业务库）。

LLM 判定边界打桩（网关当前不可达）；其余全部走真实服务代码与 MySQL。
"""
from __future__ import annotations

import json
import os
import re
import sys
from types import SimpleNamespace

import pymysql

from jiuwenswarm.channels.web.dataagent.analysis.model import RecallHit, RoadResult
from jiuwenswarm.channels.web.dataagent.analysis.rerank import mark_ambiguity
from jiuwenswarm.channels.web.dataagent.analysis.service import _collect_ambiguities
from jiuwenswarm.channels.web.dataagent.analysis.shortcut import term_view
from jiuwenswarm.channels.web.dataagent.glossary import db as gdb
from jiuwenswarm.channels.web.dataagent.glossary import recall
from jiuwenswarm.channels.web.dataagent.knowledge import (
    conflict_judge, schema_candidates, schema_enrichment, store,
)

MYSQL_HOST = os.environ.get("E2E_MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("E2E_MYSQL_PORT", "3307"))
MYSQL_USER = os.environ.get("E2E_MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("E2E_MYSQL_PASSWORD", "")
META_DB = os.environ.get("E2E_META_DB", "dataswarm_meta")
BIZ_A = "e2e_p1p2p3_a"
BIZ_B = "e2e_p1p2p3_b"
WORKSPACE = os.environ.get("E2E_WORKSPACE", "e2e-smoke")
LLM_MODE = os.environ.get("E2E_LLM_MODE", "stub").strip().lower()
if LLM_MODE not in {"stub", "real"}:
    raise SystemExit("E2E_LLM_MODE must be stub or real")

if not MYSQL_PASSWORD:
    raise SystemExit("E2E_MYSQL_PASSWORD required")

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    RESULTS.append((bool(ok), name, str(detail)[:500]))
    print(("PASS" if ok else "FAIL"), name, "|", str(detail)[:300])


def raw(db: str | None = None):
    return pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
                           password=MYSQL_PASSWORD, database=db,
                           charset="utf8mb4", autocommit=True, connect_timeout=5)


def setup_business_dbs() -> None:
    conn = raw()
    cur = conn.cursor()
    for db in (BIZ_A, BIZ_B):
        cur.execute(f"DROP DATABASE IF EXISTS {db}")
        cur.execute(f"CREATE DATABASE {db} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    cur.execute(f"USE {BIZ_A}")
    cur.execute("CREATE TABLE orders(id INT PRIMARY KEY, user_id VARCHAR(16), status DATE, amount DECIMAL(10,2))")
    cur.executemany("INSERT INTO orders VALUES (%s,%s,%s,%s)",
                    [(1, "C0001", "2026-01-01", 100), (2, "C0002", "2026-01-02", 200)])
    cur.execute("CREATE TABLE crm(id INT PRIMARY KEY, customer_no VARCHAR(16), name VARCHAR(32))")
    cur.executemany("INSERT INTO crm VALUES (%s,%s,%s)",
                    [(1, "C0001", "张三"), (2, "C0002", "李四")])
    cur.execute("CREATE TABLE approvals(id INT PRIMARY KEY, status VARCHAR(16), approver VARCHAR(16))")
    cur.executemany("INSERT INTO approvals VALUES (%s,%s,%s)",
                    [(1, "通过", "张三"), (2, "驳回", "李四")])
    cur.execute("CREATE TABLE sales(id INT PRIMARY KEY, amount DECIMAL(10,2), net_amount DECIMAL(10,2))")
    cur.executemany("INSERT INTO sales VALUES (%s,%s,%s)", [(1, 500, 400), (2, 300, 250)])
    cur.execute(f"USE {BIZ_B}")
    cur.execute(
        "CREATE TABLE orders(id INT PRIMARY KEY, user_id VARCHAR(16), "
        "status VARCHAR(16), amount DECIMAL(10,2))"
    )
    cur.executemany("INSERT INTO orders VALUES (%s,%s,%s,%s)",
                    [(1, "U001", "待发货", 120), (2, "U002", "已签收", 80)])
    conn.close()


def cleanup() -> None:
    try:
        conn = raw(META_DB)
        cur = conn.cursor()
        cur.execute("SELECT asset_id FROM dw_business_knowledge WHERE workspace_id=%s", (WORKSPACE,))
        ids = [r[0] for r in cur.fetchall()]
        if ids:
            ph = ",".join(["%s"] * len(ids))
            cur.execute(f"DELETE FROM dw_knowledge_events WHERE asset_id IN ({ph})", ids)
        # 存量巡检事件以 term_id 为 payload，不在 workspace asset 集合里，单独清
        cur.execute("DELETE FROM dw_knowledge_events "
                    "WHERE payload_json LIKE '%e2e_term_%' OR payload_json LIKE '%e2e_p1p2p3%'")
        cur.execute("DELETE FROM dw_business_knowledge WHERE workspace_id=%s", (WORKSPACE,))
        cur.execute("DELETE FROM dw_ontology_terms WHERE term_id LIKE 'term_knowledge_e2e\\_%' "
                    "OR term_id LIKE 'e2e\\_term\\_%'")
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print("cleanup(meta) warn:", exc)
    try:
        conn = raw()
        cur = conn.cursor()
        for db in (BIZ_A, BIZ_B):
            cur.execute(f"DROP DATABASE IF EXISTS {db}")
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print("cleanup(biz) warn:", exc)


# --------------------------------------------------------------------------
# 服务入口（真实模块）
# --------------------------------------------------------------------------

store._workspace = lambda: WORKSPACE
gdb._ensured_refs.clear()


def hit_from_match(match: dict) -> RecallHit:
    term = match["term"]
    payload = {
        **term,
        "term_id": term["term_id"],
        "name": term["name"],
        "definition": term["definition"],
        "synonyms": term.get("synonyms_list") or [],
        "matched_token": match.get("matched_token", ""),
        "qualified_name": match.get("qualified_name"),
        "source": match.get("source"),
        "scope": term.get("scope"),
    }
    return RecallHit(
        kind="term", entity_id=term["term_id"], label=term["name"],
        channels=[(match["match_type"], match["score"])],
        matched_on=list(match.get("matched_on") or []),
        payload=payload,
    )


def make_candidate(asset_id: str, title: str, content: str, source_id: str,
                   table: str, column: str, column_type: str,
                   samples: dict | None = None) -> dict:
    scope = {
        "source_ids": [source_id] if source_id else [],
        "table_ids": [table], "column_ids": [f"{table}.{column}"],
    }
    # 模拟 schema 派生候选（发布校验要求 origin=schema_auto 且带表列，或 manual）
    source = {"origin": "schema_auto", "schema": {"table": table, "column": column,
                                                  "column_type": column_type, "rule": "e2e"}}
    if samples:
        source["samples"] = samples
    return store.create_candidate({"asset_id": asset_id, "asset_type": "term",
                                   "title": title, "content": content,
                                   "scope": scope, "source": source})


def publish(asset_id: str) -> tuple[dict, dict]:
    asset = store.get_asset(asset_id)
    source = dict(asset.get("source") or {})
    source["alignment_decision"] = "create_new_term"
    store.update_candidate(asset_id, {"source": source},
                           expected_revision=int(asset["revision"]))
    store.transition(asset_id, "publish", actor="e2e-smoke")
    return store.get_asset(asset_id), gdb.get_term(f"term_knowledge_{asset_id}")


TARGET_TERM: str | None = None


def stub_llm(prompt: str) -> str:
    ids = list(dict.fromkeys(re.findall(r'"other_id"\s*:\s*"([^"]+)"', prompt)))
    if not ids:
        ids = list(dict.fromkeys(re.findall(r'other_id[^A-Za-z0-9_]+([A-Za-z0-9_:.-]+)', prompt)))
    verdicts = []
    for oid in ids:
        relation = "exact_match" if TARGET_TERM and oid == TARGET_TERM else "independent"
        verdicts.append({"other_id": oid, "relation": relation,
                         "confidence": 0.95, "reason": "e2e stub"})
    return json.dumps({"verdicts": verdicts}, ensure_ascii=False)


if LLM_MODE != "real":
    conflict_judge._call_llm = stub_llm


def main() -> int:
    cleanup()
    setup_business_dbs()
    gdb._ensured_refs.clear()

    # ---------- L1 派生（真实 MySQL 源） ----------
    ref_a = f"mysql://{MYSQL_HOST}:{MYSQL_PORT}/{BIZ_A}"
    ref_b = f"mysql://{MYSQL_HOST}:{MYSQL_PORT}/{BIZ_B}"
    r_a = schema_candidates.derive_candidates(ref_a, source_id="e2e-src-a")
    r_b = schema_candidates.derive_candidates(ref_b, source_id="e2e-src-b")
    check("L1 derive A", r_a["created_count"] > 0, f"created={r_a['created_count']}")
    check("L1 derive B", r_b["created_count"] > 0, f"created={r_b['created_count']}")
    derived = [a for a in store.list_assets() if (a.get("source") or {}).get("schema")]
    check("L1 provenance", all((a.get("scope") or {}).get("column_ids") for a in derived),
          f"assets={len(derived)}")

    # ---------- P1: 同名异义（DATE vs 枚举），实例证据 + 自动发布 ----------
    p1a = make_candidate("e2e_p1_a", "状态", "下单日期字段", "e2e-src-a",
                         "orders", "status", "DATE",
                         {"status": ["2026-01-01", "2026-01-02"]})
    p1b = make_candidate("e2e_p1_b", "状态", "审批结果字段", "e2e-src-a",
                         "approvals", "status", "TEXT",
                         {"status": ["通过", "驳回"]})
    evidence = conflict_judge.judge_instance_evidence(
        p1a["source"]["schema"], p1b["source"]["schema"],
        p1a["source"].get("samples"), p1b["source"].get("samples"))
    check("P1 instance evidence=different", evidence == "different", evidence)

    real_samples = schema_enrichment._sample_rows(ref_a, "orders")
    check("P1 real DB sampling", bool(real_samples.get("status")), real_samples)

    schema_enrichment._auto_publish([p1a["asset_id"], p1b["asset_id"]])
    p1a2, p1b2 = store.get_asset(p1a["asset_id"]), store.get_asset(p1b["asset_id"])
    check("P1 not auto-merged", p1a2["status"] == "candidate" and p1b2["status"] == "candidate",
          f"{p1a2['status']}/{p1b2['status']}")
    sugg = (
        (p1a2.get("source") or {}).get("alignment_suggestion")
        or (p1b2.get("source") or {}).get("alignment_suggestion")
    )
    check("P1 split suggestion recorded", bool(sugg), sugg)

    # ---------- P2: 发布投影 + 检索 scope ----------
    c1 = make_candidate("e2e_p2_a", "订单状态", "订单支付状态", "e2e-src-a",
                        "orders", "status", "DATE")
    c2 = make_candidate("e2e_p2_b", "物流状态", "订单物流状态", "e2e-src-b",
                        "orders", "status", "TEXT")
    c3 = make_candidate("e2e_p2_c", "通用状态", "通用状态口径", "e2e-src-c", "crm", "status",
                        "TEXT")
    published_a, term_a = publish(c1["asset_id"])
    published_b, term_b = publish(c2["asset_id"])
    published_c, term_c = publish(c3["asset_id"])
    check("P2 publish projections", all(t and (t.get("scope") is not None) for t in (term_a, term_b, term_c)),
          {k: (v or {}).get("scope") for k, v in (("a", term_a), ("b", term_b), ("c", term_c))})
    check("P2 term A scope", term_a and term_a["scope"].get("source_ids") == ["e2e-src-a"], term_a and term_a["scope"])

    scoped = recall.resolve("状态", scope={"source_ids": ["e2e-src-a"], "confidence": 0.9})
    ids = {m["term"]["term_id"] for m in scoped["matches"]}
    check("P2 scope excludes B", term_b["term_id"] not in ids, ids)
    check("P2 scope keeps A", term_a["term_id"] in ids, ids)

    hit_a = next((hit_from_match(m) for m in scoped["matches"]
                  if m["term"]["term_id"] == term_a["term_id"]), None)
    view_a = term_view(hit_a) if hit_a else {}
    check("P2 term_view qualified_name", view_a.get("qualified_name") == "orders.status", view_a)

    low = recall.resolve("状态", scope={"source_ids": ["e2e-src-a"], "confidence": 0.1})
    low_ids = {m["term"]["term_id"] for m in low["matches"]}
    check("P2 low-confidence no filter", term_b["term_id"] in low_ids, low_ids)
    check("P2 low-confidence carries qualified",
          all(m.get("qualified_name") for m in low["matches"]), [m.get("qualified_name") for m in low["matches"]])

    # ---------- P1 运行时歧义（mapping 轴） ----------
    all_status = recall.resolve("状态", limit=50)
    hits = [hit_from_match(m) for m in all_status["matches"]
            if m["term"]["term_id"] in {term_a["term_id"], term_b["term_id"], term_c["term_id"]}]
    ranked = mark_ambiguity(hits)
    entries = [term_view(h) for h in ranked]
    packs = SimpleNamespace(glossary=entries, rules=[], mappings=[], examples=[],
                            metrics=[], metrics_selected=[])
    recall_results = {"terms": RoadResult("term", tuple(ranked), "ok"),
                      "values": RoadResult("value", (), "no_hit")}
    ambs = _collect_ambiguities(recall_results, packs)
    calibers = [a for a in ambs if a.get("kind") == "term_caliber"]
    check("P1 clarify single entry", len(calibers) == 1, ambs)
    check("P1 clarify mapping axis",
          bool(calibers) and calibers[0].get("ambiguous_kind") == "mapping", calibers)
    check("P1 clarify options carry qualified names",
          bool(calibers) and len(calibers[0].get("options") or []) >= 2
          and all(o.get("qualified_name") for o in calibers[0]["options"]), calibers)

    # ---------- P1 reasoning 轴（同表不同列） ----------
    d1 = make_candidate("e2e_reason_a", "商品交易总额", "商品成交金额合计", "e2e-src-a",
                        "sales", "amount", "DECIMAL")
    d2 = make_candidate("e2e_reason_b", "净交易总额", "剔除退款后的交易额", "e2e-src-a",
                        "sales", "net_amount", "DECIMAL")
    _, term_d = publish(d1["asset_id"])
    _, term_e = publish(d2["asset_id"])
    res_reason = recall.resolve("交易总额", limit=50)
    reason_hits = [hit_from_match(m) for m in res_reason["matches"]
                   if m["term"]["term_id"] in {term_d["term_id"], term_e["term_id"]}]
    ranked_r = mark_ambiguity(reason_hits)
    entries_r = [term_view(h) for h in ranked_r]
    packs_r = SimpleNamespace(glossary=entries_r, rules=[], mappings=[], examples=[],
                              metrics=[], metrics_selected=[])
    rr = {"terms": RoadResult("term", tuple(ranked_r), "ok"),
          "values": RoadResult("value", (), "no_hit")}
    ambs_r = _collect_ambiguities(rr, packs_r)
    cal_r = [a for a in ambs_r if a.get("kind") == "term_caliber"]
    check("P1 clarify reasoning axis",
          bool(cal_r) and cal_r[0].get("ambiguous_kind") == "reasoning", ambs_r)

    # ---------- P3: 写入查重 allow + 别名召回 ----------
    canonical = gdb.create_term({
        "term_id": "e2e_term_customer", "name": "客户编号", "definition": "客户唯一编号",
        "synonyms": ["customer_no"],
        "scope": {"source_ids": ["e2e-src-a"], "table_ids": ["crm"],
                  "column_ids": ["crm.customer_no"]},
    })
    before_terms = {t["term_id"] for t in gdb.list_terms(status="active")}
    cand3 = make_candidate("e2e_p3_a", "客户编号", "客户唯一编号", "e2e-src-a",
                           "crm", "customer_no", "VARCHAR")
    globals()["TARGET_TERM"] = "e2e_term_customer"
    schema_enrichment._auto_publish([cand3["asset_id"]])
    after_terms = {t["term_id"] for t in gdb.list_terms(status="active")}
    cand3_after = store.get_asset(cand3["asset_id"])
    check("P3 no duplicate term", after_terms == before_terms, after_terms - before_terms)
    check("P3 aligned to canonical",
          (cand3_after.get("source") or {}).get("alignment_ref") == "term:e2e_term_customer",
          cand3_after.get("source"))
    check("P3 aliases recorded on candidate",
          "customer_no" in ((cand3_after.get("source") or {}).get("synonyms") or []),
          (cand3_after.get("source") or {}).get("synonyms"))
    alias_hits = recall.resolve("customer_no", limit=20)
    check("P3 alias recall canonical",
          canonical["term_id"] in {m["term"]["term_id"] for m in alias_hits["matches"]},
          [m["term"]["term_id"] for m in alias_hits["matches"]])

    print("\n==== SUMMARY ====")
    failed = [r for r in RESULTS if not r[0]]
    print(f"PASS {len(RESULTS) - len(failed)} / {len(RESULTS)}")
    for ok, name, detail in failed:
        print("FAIL:", name, "|", detail)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        cleanup()
    sys.exit(code)
