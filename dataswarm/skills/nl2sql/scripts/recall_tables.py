# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import argparse
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_client import (query, emit, emit_error, classify_error, database_name,
                        source_type, duckdb_tables, glossary_resolve, semantic_vector_recall,
                        schema)

# ---- 语义增强通道参数（一期接线：术语扩展 + 向量混合召回，schema 向量设计 §6 P1）----
GLOSSARY_MIN_SCORE = 0.6      # 弱命中术语不扩展（与指标侧 GLOSSARY_EXPAND_MIN_SCORE 同刻度）
GLOSSARY_WORD_CAP = 5         # 扩展词上限：过多会稀释词法 OR 条件
LEXICAL_NAME_SCORE = 0.9     # 词法命中表名（与向量校准分同 0~1 刻度，max 合并）
LEXICAL_COMMENT_SCORE = 0.7  # 词法仅命中注释/列名
LEXICAL_BASE_SCORE = 0.5     # 无词可配时基线（查询词为空的全表罗列场景）


def _esc_like(s: str) -> str:
    """转义 LIKE 模式中的单引号（Doris information_schema 不支持占位符绑参）。

    Doris information_schema 虚拟表对 ? 占位符触发 Nereids 解析器 bug
    (arity() is null)，必须用字面值拼。查询词来自用户输入不可信，
    故单引号翻倍防 SQL 注入。%/_ 为 LIKE 通配符，查表名场景用户输入概率
    极低，保留原样（即便有也仅扩大召回范围，无安全风险）。
    """
    return s.replace("'", "''")


def _glossary_expand(query_text: str) -> tuple[list[str], list[dict], list[str]]:
    """术语库同义扩展：resolve 命中术语的规范名/别名作为额外词法召回词。

    「各品类的成交规模」经术语「商品交易总额(GMV)」扩展出 GMV/商品交易总额
    后，LIKE 通道才能命中 gmv 相关表。通道失败（服务未起/术语未建）静默
    降级返回空——术语扩展是召回增益不是依赖，**不是错误**。
    """
    try:
        resolved = glossary_resolve(query_text, limit=3)
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
        terms.append({"name": name, "score": round(score, 4),
                      "match_type": str(match.get("match_type") or "")})
        for word in (name, *(str(s) for s in term.get("synonyms_list") or [])):
            word = str(word).strip()
            if word and word.lower() not in lowered and word not in words:
                words.append(word)
    return words[:GLOSSARY_WORD_CAP], terms, []


def _vector_recall_tables(query_text: str, top_k: int) -> tuple[dict[str, float], list[str]]:
    """schema 向量通道（服务端 FAISS）：返回 {表名: 校准分}。

    duckdb 文件源无语义采集（向量索引只覆盖已采集注册源），直接跳过记
    warning。服务端降级契约：通道关闭/未绑定返回空 tables + warning；
    本地调用失败（服务未起）同样降级——词法通道照跑。
    """
    if source_type() == "duckdb":
        return {}, ["vector_recall_skipped:duckdb_source"]
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
                   extra_words: list[str] | None = None) -> dict:
    """duckdb 文件源召回：csv 目录多视图，表名/列名命中查询词即返回。

    文件视图和原生库不复用 Doris 的 information_schema 查询；对象列表经
    duckdb_tables() 获取，fields 从
    统一 schema 接口取，保留原生库字段注释。向量通道对 duckdb 源跳过
    （见 _vector_recall_tables），术语扩展词仍进词法匹配。
    """
    words = [w.lower() for w in re.split(r"[\s_,]+", query_text) if w]
    for w in extra_words or []:
        if w.lower() not in words:
            words.append(w.lower())
    items = []
    for table in duckdb_tables():
        schema_result = None
        try:
            schema_result = schema(table)
        except Exception as exc:
            et, msg, _ = classify_error(exc)
            emit_error(et, msg)
        fields = [
            {"name": c["name"], "type": c["type"], "comment": c.get("comment") or "",
             "nullable": c.get("nullable", c.get("null", True))}
            for c in (schema_result or {}).get("columns", [])
        ]
        if words:
            name_l = table.lower()
            field_l = " ".join(f["name"].lower() for f in fields)
            if not any(w in name_l or w in field_l for w in words):
                continue
        if words and any(w in table.lower() for w in words):
            score, via = LEXICAL_NAME_SCORE, "name"
        elif words:
            score, via = LEXICAL_COMMENT_SCORE, "column"
        else:
            score, via = LEXICAL_BASE_SCORE, ""
        items.append({
            "name": table,
            "comment": "",
            "row_count_estimated": True,
            "row_count": 0,
            "score": score,
            "matched_on": (["lexical:" + via] if via else []),
            "fields": fields,
        })
        if len(items) >= top_k:
            break
    return {"database": database_name(), "source_type": "duckdb", "query": query_text,
            "items": items, "count": len(items)}


def recall_tables(query_text: str, top_k: int = 10) -> dict:
    # 术语扩展先行（词法增益），向量通道并行取（duckdb 源内部跳过）；
    # 两个语义通道任何失败只记 warning，词法主链路照常（降级契约）
    glossary_words, glossary_terms, warnings = _glossary_expand(query_text)
    vector_tables, vec_warnings = _vector_recall_tables(query_text, top_k)
    warnings = warnings + vec_warnings

    if source_type() == "duckdb":
        result = _search_duckdb(query_text, top_k, glossary_words)
        result["glossary_terms"] = glossary_terms
        result["warnings"] = warnings
        return result

    db = database_name()
    if not db:
        emit_error("MISSING_CONFIG",
                   "db-ref 未含 database schema，information_schema 查询需要库名")

    # 分词召回：query 按空白/下划线/逗号拆词，每词一个 LIKE（OR）。
    # 表名如 ods_olist_customers_dataset（下划线分隔），词间 AND 会命中
    # "olist customers" 而把整串当单个 %...% 必然空。术语扩展词追加进
    # 词表（不重复）——「成交规模」经术语桥接出 GMV 后才能 LIKE 到 gmv 表。
    #
    # Doris information_schema 不支持 ? 占位符绑参（Nereids arity bug），
    # 库名（来自 config，可信）与查询词 LIKE 模式（用户输入，转义单引号后）均字面拼。
    words = [w for w in re.split(r"[\s_,]+", query_text) if w]
    for w in glossary_words:
        if w not in words:
            words.append(w)
    if not words:
        words = [""]
    conds = " OR ".join(
        [f"table_name LIKE '%{_esc_like(w)}%' OR table_comment LIKE '%{_esc_like(w)}%'"
         for w in words]
    )
    db_esc = db.replace("'", "''")

    list_sql = (
        "SELECT table_name, table_comment, table_rows "
        "FROM information_schema.tables "
        f"WHERE table_schema = '{db_esc}' AND ({conds}) "
        "ORDER BY table_rows DESC "
        f"LIMIT {int(top_k)}"
    )
    try:
        list_result = query(list_sql)
    except Exception as exc:
        et, msg, _ = classify_error(exc)
        emit_error(et, msg)

    candidate_rows = list_result["rows"]
    candidate_names = [r[0] for r in candidate_rows]
    # 词法候选 ∪ 向量命中表（向量独有表不在 LIKE 结果里，字段/注释须补查）
    all_names = list(dict.fromkeys([*candidate_names, *vector_tables.keys()]))
    src_type = source_type() or "doris"
    if not all_names:
        return {"database": db, "source_type": src_type, "query": query_text,
                "items": [], "count": 0, "glossary_terms": glossary_terms,
                "warnings": warnings}

    # 表元数据：词法候选自带（候选行）；向量独有表单独补查 information_schema
    row_info: dict[str, tuple[str, int]] = {}
    for table_name, table_comment, table_rows in candidate_rows:
        row_info[table_name] = (table_comment or "", table_rows or 0)
    vector_only = [n for n in all_names if n not in row_info]
    if vector_only:
        vnames_esc = ",".join(f"'{n.replace(chr(39), chr(39)*2)}'" for n in vector_only)
        vlist_sql = (
            "SELECT table_name, table_comment, table_rows "
            "FROM information_schema.tables "
            f"WHERE table_schema = '{db_esc}' AND table_name IN ({vnames_esc})"
        )
        try:
            for r in query(vlist_sql)["rows"]:
                row_info[r[0]] = (r[1] or "", r[2] or 0)
        except Exception:  # noqa: BLE001 —— 元数据补查失败不阻断，占位空注释
            warnings.append("vector_meta_lookup_failed")
        for name in vector_only:
            row_info.setdefault(name, ("", 0))

    # 表名来自上一步库内查询结果 ∪ 服务端向量召回（可信），字面拼 IN 列表
    names_esc = ",".join(f"'{n.replace(chr(39), chr(39)*2)}'" for n in all_names)
    col_sql = (
        f"SELECT table_name, column_name, column_type, column_comment, is_nullable "
        f"FROM information_schema.columns "
        f"WHERE table_schema = '{db_esc}' AND table_name IN ({names_esc}) "
        f"ORDER BY table_name, ordinal_position"
    )
    try:
        col_result = query(col_sql)
    except Exception as exc:
        et, msg, _ = classify_error(exc)
        emit_error(et, msg)

    fields_by_table: dict[str, list] = {}
    for table_name, col_name, col_type, col_comment, is_nullable in col_result["rows"]:
        fields_by_table.setdefault(table_name, []).append({
            "name": col_name,
            "type": col_type,
            "comment": col_comment or "",
            "nullable": is_nullable == "YES",
        })

    # 合并：score = max(词法分, 向量分)；matched_on 标记命中通道，供上层选表
    # 与观测（lexical:name / emb / glossary：术语桥接召回的证据）
    glossary_lowered = {w.lower() for w in glossary_words}
    items = []
    for name in all_names:
        comment, rows = row_info.get(name, ("", 0))
        lex_score, via = _lex_score_via(name, comment, words)
        vec_score = vector_tables.get(name, 0.0)
        if lex_score <= 0.0 and vec_score <= 0.0:
            continue
        matched_on = []
        if lex_score > 0.0:
            matched_on.append(f"lexical:{via}" if via else "lexical")
        if vec_score > 0.0:
            matched_on.append("emb")
        text_l = f"{name} {comment}".lower()
        if glossary_lowered and any(w in text_l for w in glossary_lowered):
            matched_on.append("glossary")
        items.append({
            "name": name,
            "comment": comment,
            "row_count_estimated": True,
            "row_count": rows,
            "score": round(max(lex_score, vec_score), 4),
            "matched_on": matched_on,
            "fields": fields_by_table.get(name, []),
        })
    # 分数优先（混合召回的排序语义），同分按行数（原版排序口径）
    items.sort(key=lambda it: (-it["score"], -(it["row_count"] or 0), it["name"]))
    items = items[:top_k]

    return {"database": db, "source_type": src_type, "query": query_text,
            "items": items, "count": len(items), "glossary_terms": glossary_terms,
            "warnings": warnings}


def main():
    parser = argparse.ArgumentParser(description="Table recall via information_schema")
    parser.add_argument("--query", required=True)
    parser.add_argument("--top_k", type=int, default=10)
    args = parser.parse_args()
    emit(recall_tables(args.query, args.top_k))


if __name__ == "__main__":
    main()
