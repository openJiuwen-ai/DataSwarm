# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import argparse
import re
import sys
import os
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_client import (emit, emit_error, database_name, execution_context,
                        source_type, glossary_resolve, semantic_vector_recall,
                        schema_columns, value_matches, metric_recall, classify_error, field_context)

# ---- 语义增强通道参数（一期接线：术语扩展 + 向量混合召回，schema 向量设计 §6 P1）----
GLOSSARY_MIN_SCORE = 0.6      # 弱命中术语不扩展（与指标侧 GLOSSARY_EXPAND_MIN_SCORE 同刻度）
GLOSSARY_WORD_CAP = 5         # 扩展词上限：过多会稀释词法 OR 条件
LEXICAL_NAME_SCORE = 0.9     # 词法命中表名（与向量校准分同 0~1 刻度，max 合并）
LEXICAL_COMMENT_SCORE = 0.7  # 词法仅命中注释/列名
LEXICAL_BASE_SCORE = 0.5     # 无词可配时基线（查询词为空的全表罗列场景）
VALUE_MATCH_SCORE = 0.8      # 问话里出现了该表某列的真实取值（值字典精确命中）


def _glossary_expand(query_text: str) -> tuple[list[str], list[dict], list[str]]:
    """术语库同义扩展：resolve 命中术语的规范名/别名作为额外词法召回词。

    「各品类的成交规模」经术语「商品交易总额(GMV)」扩展出 GMV/商品交易总额
    后，LIKE 通道才能命中 gmv 相关表。通道失败（服务未起/术语未建）静默
    降级返回空——术语扩展是召回增益不是依赖，**不是错误**。
    """
    try:
        resolved = glossary_resolve(query_text, limit=10)
    except Exception:
        return [], [], ["glossary_unavailable"]
    words: list[str] = []
    terms: list[dict] = []
    lowered = query_text.lower()
    for match in (resolved or {}).get("matches") or []:
        score = float(match.get("score") or 0.0)
        if score < GLOSSARY_MIN_SCORE:
            continue  # 弱命中（LIKE definition 档 0.25）注入等于倒噪声
        term = match.get("term") or {}
        name = str(term.get("name") or "").strip()
        if not name:
            continue
        terms.append({"term_id": term.get("term_id"), "name": name,
                      "definition": term.get("definition") or "",
                      "synonyms": term.get("synonyms_list") or [], "score": round(score, 4),
                      "match_type": str(match.get("match_type") or "")})
        for word in (name, *(str(s) for s in term.get("synonyms_list") or [])):
            word = str(word).strip()
            if word and word.lower() not in lowered and word not in words:
                words.append(word)
    return words[:GLOSSARY_WORD_CAP], terms, []


def _vector_recall_tables(query_text: str, top_k: int) -> tuple[dict[str, float], list[str]]:
    """schema 向量通道（服务端 FAISS）：返回 {表名: 校准分}。

    已注册文件源与原生库同样可能有语义索引，由服务端解析当前绑定源。
    服务端降级契约：通道关闭/未绑定返回空 tables + warning；
    本地调用失败（服务未起）同样降级——词法通道照跑。
    """
    try:
        result = semantic_vector_recall(query_text, limit=top_k)
    except Exception:
        return {}, ["vector_recall_unavailable"]
    warnings = [str(w) for w in (result or {}).get("warnings") or []]
    tables = {}
    for entry in (result or {}).get("tables") or []:
        name = str(entry.get("table_name") or "")
        if name:
            tables[name] = float(entry.get("score") or 0.0)
    return tables, warnings


def _value_matches(query_text: str) -> tuple[dict[str, float], list[dict], list[dict], list[str]]:
    """Value-dictionary channel: tables whose column values the question names literally.

    A question like "Washington 的销售额" shares no word with any table or column
    name, so only the stored values connect it to a table. ``ambiguous`` carries
    values found under several columns; the caller must ask which one is meant.
    Failure degrades to an empty channel with a warning, like the other channels.
    """
    try:
        result = value_matches(query_text, limit=20) or {}
    except Exception:
        return {}, [], [], ["value_matches_unavailable"]
    values = list(result.get("values") or [])
    tables = {str(m.get("table")): VALUE_MATCH_SCORE
              for m in values if m.get("match_type") == "exact" and m.get("table")}
    return tables, values, list(result.get("ambiguous") or []), [str(w) for w in result.get("warnings") or []]


def _lex_score_via(table_name: str, comment: str, words: list[str]) -> tuple[float, str]:
    """词法命中档位 → ``(分, 命中面)``。无命中返回 (0.0, "")。

    与向量校准分同 0~1 刻度，供两通道 max 合并；命中面供 matched_on 归因。
    空查询（words 拆完为空、LIKE '%%' 全表罗列场景）给基线分不标命中面。
    """
    words = [w for w in words if w]
    if not words:
        return LEXICAL_BASE_SCORE, ""
    name_l = table_name.lower()
    if any(w.lower() in name_l for w in words):
        return LEXICAL_NAME_SCORE, "name"
    comment_l = (comment or "").lower()
    if any(w.lower() in comment_l for w in words):
        return LEXICAL_COMMENT_SCORE, "comment"
    return 0.0, ""


def _search_duckdb(query_text: str, top_k: int,
                   extra_words: list[str] | None = None,
                   value_tables: dict[str, float] | None = None,
                   vector_tables: dict[str, float] | None = None) -> dict:
    """duckdb 文件源召回：csv 目录多视图，表名/列名命中查询词即返回。

    文件视图和原生库不复用 Doris 的 information_schema 查询；对象列表经
    统一批量schema接口一次读取全部列，不逐表启动dwcli、不读取行数。
    保留原生库字段注释；已注册源的向量结果参与排序。
    """
    words = [w.lower() for w in re.split(r"[\s_,]+", query_text) if w]
    for w in extra_words or []:
        if w.lower() not in words:
            words.append(w.lower())
    items = []
    try:
        entries = schema_columns().get("tables", [])
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        kind, why, _ = classify_error(exc)
        emit_error(kind, why)
    failures = [{"table": entry["table"], "error": entry["error"]} for entry in entries if entry.get("error")]
    if entries and len(failures) == len(entries):
        emit_error("SCHEMA_ERROR", "All catalog tables failed schema reflection")
    table_names = [entry["table"] for entry in entries]
    metadata = {entry["table"]: entry for entry in entries}

    def normalized(name):
        return str(name).replace('"', '').casefold()

    leaf_counts: dict[str, int] = {}
    for name in table_names:
        leaf = normalized(name).rsplit(".", 1)[-1]
        leaf_counts[leaf] = leaf_counts.get(leaf, 0) + 1
    for table in table_names:
        schema_result = metadata[table]
        if schema_result.get("error"):
            continue
        fields = [
            {"name": c["name"], "type": c["type"], "comment": c.get("comment") or "",
             "nullable": c.get("nullable", c.get("null", True))}
            for c in (schema_result or {}).get("columns", [])
        ]
        value_score = (value_tables or {}).get(table, 0.0)
        vector_score = (vector_tables or {}).get(table, 0.0)
        if not vector_score:
            canonical = normalized(table)
            leaf = canonical.rsplit(".", 1)[-1]
            vector_score = max((score for name, score in (vector_tables or {}).items()
                                if normalized(name) == canonical or (
                                    "." not in normalized(name) and normalized(name) == leaf
                                    and leaf_counts[leaf] == 1)), default=0.0)
        field_l = " ".join((f["name"] + " " + f["comment"]).lower() for f in fields)
        if words and any(w in table.lower() for w in words):
            score, via = LEXICAL_NAME_SCORE, "name"
        elif words and any(w in field_l for w in words):
            score, via = LEXICAL_COMMENT_SCORE, "column"
        elif words:
            score, via = 0.0, ""
        else:
            score, via = LEXICAL_BASE_SCORE, ""
        if not score and not value_score and not vector_score:
            continue
        matched_on = ["lexical:" + via] if via else []
        if value_score:
            matched_on.append("value")
        if vector_score:
            matched_on.append("emb")
        items.append({
            "name": table,
            "comment": schema_result.get("comment", ""),
            "row_count_estimated": True,
            "row_count": 0,
            "score": max(score, value_score, vector_score),
            "matched_on": matched_on,
            "fields": fields,
        })
    items.sort(key=lambda item: (-item["score"], item["name"]))
    items = items[:top_k]
    return {"database": database_name(), "source_type": source_type(), "query": query_text,
            "items": items, "count": len(items), "catalog_complete": not failures,
            "schema_errors": failures, "warnings": ["catalog_schema_incomplete"] if failures else []}


def recall_tables(query_text: str, top_k: int = 10) -> dict:
    # 术语扩展先行（词法增益），向量与维值通道按注册源解析；
    # 两个语义通道任何失败只记 warning，词法主链路照常（降级契约）
    glossary_words, glossary_terms, warnings = _glossary_expand(query_text)
    vector_tables, vec_warnings = _vector_recall_tables(query_text, top_k)
    value_tables, values, ambiguous_values, value_warnings = _value_matches(query_text)
    warnings = warnings + vec_warnings + value_warnings
    value_fields = {"value_matches": values, "ambiguous_values": ambiguous_values}
    try:
        discovery = metric_recall(query_text, limit=5)
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        discovery = {"candidates": [], "channel_status": "unavailable", "warnings": ["metric_recall_unavailable"]}
    value_fields["metric_discovery"] = discovery
    warnings += list(discovery.get("warnings") or [])

    result = _search_duckdb(query_text, top_k, glossary_words, value_tables, vector_tables)
    result["glossary_terms"] = glossary_terms
    result["warnings"] = warnings + result.get("warnings", [])
    result.update(value_fields)
    result["execution_environment"] = execution_context()
    try:
        result["field_context"] = field_context([
            {"table": item["name"], "columns": [field["name"] for field in item["fields"]]}
            for item in result["items"]])
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        result["field_context"] = {"packs": {"schema_tables": []}, "warnings": ["field_profiles_unavailable"]}
    result["warnings"] += result["field_context"].get("warnings", [])
    return result


def main():
    parser = argparse.ArgumentParser(description="Table recall via information_schema")
    parser.add_argument("--query", required=True)
    parser.add_argument("--top_k", type=int, default=10)
    args = parser.parse_args()
    emit(recall_tables(args.query, args.top_k))


if __name__ == "__main__":
    main()
