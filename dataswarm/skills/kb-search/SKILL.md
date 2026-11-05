---
name: kb-search
description: 从业务知识库做语义检索回答用户问题。当用户的问题涉及已导入的业务文档内容（产品手册、规章、FAQ、业务术语等）时使用——配合 kb-manage 技能先入库文档。
allowed_tools: [kb_search]
---

# 业务知识库检索

本技能通过 `kb_search` 工具从 ChromaDB 本地知识库做语义检索，拿回相关 chunk 后组织回答。知识库内容由 `kb-manage` 技能灌入——检索前确保已有文档入库。

## 何时检索

当用户的问题**需要已导入的业务文档支撑**时调用。典型场景：
- "产品 X 的配置项有哪些？"（产品手册已入库）
- "报销流程第三步是什么？"（报销制度已入库）
- "术语 Y 是什么意思？"（术语表已入库）

**不该检索**的场景：常识问题、用户实时给的上下文、代码本身能回答的问题。不要为了"用工具"而检索。

## 基本用法

```
kb_search(query="用户的原始问题", kbId?)
```
- `query`：用用户的**原始自然语言**提问，不要改写成关键词堆砌。语义检索对自然句更友好。
- `kbId`：省略取默认 `biz_kb`。多 KB 场景见 `kb-manage` 技能。
- 返回 `results`（每项含 `text`/`score`/`doc_id`/`chunk_id`/`metadata`）+ `count`。

## 如何用检索结果回答

1. 拿 `results` 里的 `text` 字段作为事实依据组织回答。
2. **引用来源**：回答中提及信息出自哪份文档（用 `metadata` 里的 `file_name` 或 `doc_id`）。
3. 不要把 chunk 原文整段复述——提炼成对用户有用的回答。
4. 多条结果时，`score` 高的优先采信；低分且与问题无关的可忽略。

## disabled 或空结果怎么办

- 返回 `disabled: true` → 知识库未启用。告知用户让管理员开启（具体见 `kb-manage/references/troubleshooting.md`）。不要重试。
- `count: 0` → 库里没有相关内容。按 [references/search-params.md](references/search-params.md) 的"结果为空时"处理：先确认库非空，再考虑放宽检索。
- 库为空（`kb_list_entries` 返回空）→ 提示用户先用 `kb-manage` 技能入库文档。

## 何时阅读参考文件

- `top_k`/`agentic`/`scoreThreshold` 参数怎么调、空结果怎么降级、来源引用格式：读 [references/search-params.md](references/search-params.md)
