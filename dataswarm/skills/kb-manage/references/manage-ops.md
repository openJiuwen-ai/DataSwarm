# 管理类操作（create / list / get / update / list-entries）

## kb_create — 新建知识库

```
kb_create(kbId, name?, description?, tags?)
```

- `kbId` 必传：仅限字母/数字/下划线/中划线（1-64 位），会成为 KB 的标识，不可与已有 KB 重名（含默认 `biz_kb`）。
- `name` 省略时取 `kbId`；`tags` 是字符串列表，可选。
- 成功返回注册 entry（`kb_id`/`name`/`description`/`tags`/`created`）；重名或 `kbId` 非法返回 `success: false` + `error`。
- 新建的是**空库**，向量目录在首次入库时才构建；随后入库要带 `kbId`，否则会灌进默认 `biz_kb`。

何时用：用户明确要"新建一个知识库"且业务上需要与默认库隔离。单库场景不要建——直接用默认 `biz_kb`。

## kb_list — 列所有知识库

```
kb_list()
```
返回 `results` 列表，每项含 `kb_id`/`name`/`description`/`tags`/`created`，以及 `count`。不含 chunk 数（要 chunk 数用 `kb_get`）。

何时用：用户问"有哪些知识库"，或要切换到别的 KB 时先看一眼。

## kb_get — 知识库详情

```
kb_get(kbId?)            # 省略取默认 biz_kb
```
返回 KB 元信息 + `chunk_count`（已入库 chunk 总数，KB 未实际构建时为 0）。`found: false` 表示该 kb_id 不存在。

何时用：用户问"这个知识库里有多少内容 / KB 的基本信息"。

## kb_update — 改名/描述/标签

```
kb_update(kbId?, name?, description?, tags?)
```
- 至少传 `name`/`description`/`tags` 中的**一个**，否则报错。
- `tags` 是字符串列表，例如 `["订单", "2026Q2"]`。
- 返回更新后的 `name`/`description`/`tags`。
- `kbId` 省略取默认 `biz_kb`；若默认 KB 尚未创建，会自动建一个空 KB。

何时用：用户说"改一下知识库的描述 / 名字 / 标签"。

## kb_list_entries — 列库内文档清单

```
kb_list_entries(kbId?, keyword?, top?)
```
- `keyword`：按文档名做关键词过滤（大小写不敏感）。可选。
- `top`：最多返回多少条文档。可选。
- 返回 `entries` 列表，每项含 `doc_id`/`file_name`/`chunks`（该文档的 chunk 数）。

何时用：
- 用户问"知识库里有哪些文档 / 都入了什么"→ 不带 `keyword`。
- 用户问"有没有关于 X 的文档"→ 带文件名关键词 `keyword="X"`。

注意：`list_entries` 按文档名过滤，**不是**按内容语义检索。要按内容查，用 `kb-search` 技能的 `kb_search`。

## kb_delete_documents — 删除库内文档

```
kb_delete_documents(docIds, kbId?)       # 省略 kbId 取默认 biz_kb
```

- `docIds`：要删除的文档 ID 列表，来自 `kb_list_entries` 返回的 `doc_id` 或入库时的 `doc_ids`。必传非空列表。
- 返回 `deleted`（删成功的 doc_id）和 `missing`（向量库里没找到对应 chunk 的 doc_id， Chroma 无匹配时视为未删）；`success` 仅在 `missing` 为空时为 True。
- 删除是按文档粒度，一份文档的所有 chunk 一并删除。

何时用：用户说"把某份文档从知识库里删掉 / 移除 X 文档"。执行前先 `kb_list_entries` 拿 `doc_id` 并向用户确认。

## kb_delete — 删除整个知识库

```
kb_delete(kbId)                          # kbId 必传
```

- 删除注册信息与该库的向量数据目录，不可恢复。
- **默认知识库 `biz_kb` 拒绝删除**，会返回 `success: false`；用户想清空默认库时改用 `kb_delete_documents`。
- `kbId` 不存在同样返回 `success: false`。

何时用：用户明确要"删掉整个知识库"。执行前必须与用户确认库名，因为不可恢复。
