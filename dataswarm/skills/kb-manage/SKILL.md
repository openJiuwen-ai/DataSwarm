---
name: kb-manage
description: 管理业务知识库——新建、列表、详情、改描述/标签、列库内文档、上传本地文档入库、挂载本地目录全量入库、删除文档、删除知识库。当用户要"建知识库/新建知识库/导入文档/喂知识/加入知识库/挂载目录/看 KB 里有什么/改知识库描述/删文档/删知识库"时使用。
allowed_tools: [kb_list, kb_create, kb_get, kb_update, kb_list_entries, kb_upload_file, kb_attach_dir, kb_delete_documents, kb_delete]
---

# 业务知识库管理

本技能通过本地知识库工具（`kb_*`）管理 ChromaDB 本地向量知识库。知识库用于给 `kb-search` 技能提供检索来源：你在这里灌入业务文档（产品手册、规章、FAQ、PDF/Word/Markdown 等），检索技能才能回答业务问题。

KB 数据按数据源工作空间隔离：每个工作空间（默认/证券/temp 等）有自己独立的知识库集合，会话里看到的 KB 只属于当前选中的工作空间。无需向用户解释目录细节。

## 核心概念（一句话版）

- **知识库（KB）**：一个向量库 collection。默认有一个 `biz_kb`，调用时 `kbId` 省略即指它。
- **文档**：知识库的最小单元，一份 PDF/Markdown/Word 等。一份文档会被切成多个 chunk 入库。
- **chunk**：文档被切分后的检索粒度。`kb_list_entries` 按**文档**聚合 chunk 数返回。

详情见 [references/kb-concepts.md](references/kb-concepts.md)。

## 使用方式

默认知识库 `biz_kb` 在首次入库时自动创建，单库场景不需要先建库，`kbId` 省略即操作当前工作空间的默认库。

按用户诉求选动作：

- **写入**：用户给文件路径 → `kb_upload_file`（`paths` 传文件路径列表，支持一次多份）；给目录 → `kb_attach_dir`（递归扫描目录下所有支持文档）。两者省略 `kbId` 即入默认库。确实需要隔离来源时才新建：`kb_create(kbId=..., name=..., description=...)`，之后写入带上 `kbId`。
- **查看**：问有哪些库 → `kb_list`；问某个库的基本信息 → `kb_get`；问库内有哪些文档 → `kb_list_entries`（想看某类文档可带 `keyword` 过滤文件名）。
- **改元信息**：`kb_update` 改 `name`/`description`/`tags`，至少传一项。
- **删除**：删文档 → `kb_delete_documents`（`docIds` 来自 `kb_list_entries` 的 `doc_id`）；删整库 → `kb_delete`（`kbId` 必传，默认库不可删）。

最小闭环：

```
kb_update(description="订单中心知识库")             # 单库场景直接改默认库描述
kb_upload_file(paths=["/abs/path/to/manual.pdf"])   # 入库
kb_list_entries()                                   # 看库内文档
```

需要新建隔离库时：

```
kb_create(kbId="compliance_kb", name="合规制度库", description="规章与流程")
kb_upload_file(paths=["/abs/path/to/rule.pdf"], kbId="compliance_kb")
```

入库入口的选择、多选规则、支持的文件格式见 [references/upload-guide.md](references/upload-guide.md)。

## 何时阅读参考文件

- 单/多 KB 概念、默认 KB 行为、何时该问用户选 KB：读 [references/kb-concepts.md](references/kb-concepts.md)
- `create`/`list`/`get`/`update`/`list-entries` 的参数与返回细节：读 [references/manage-ops.md](references/manage-ops.md)
- 上传文件 vs 挂载目录的入口选择、多选规则、支持的文件格式：读 [references/upload-guide.md](references/upload-guide.md)
- 入库/检索报错、embedding 缺失、disabled 怎么办：读 [references/troubleshooting.md](references/troubleshooting.md)

## 关键约束

- 工具返回 `"disabled": true` 时，说明知识库未启用（`config.yaml` 的 `knowledge_base.enabled` 为 false，或 embedding 配置缺失）。告知用户让管理员开启，不要反复重试。
- `kb_upload_file` 的 `paths` 必须是**存在的**文件路径；路径校验会拒绝不存在的路径。
- 挂载目录用 `kb_attach_dir`，不要用 `kb_upload_file` 传目录路径。
- `kb_create` 的 `kbId` 仅限字母/数字/下划线/中划线（1-64 位），不能与已有 KB 重名；单库场景直接用默认 `biz_kb`，不要为建而建。
- 删除操作先向用户确认范围再执行：删部分文档用 `kb_delete_documents`（`docIds` 来自 `kb_list_entries` 的 `doc_id`），删整库用 `kb_delete`。**默认知识库 `biz_kb` 不可整库删除**，用户要清空默认库时改用 `kb_delete_documents` 逐个删文档。
