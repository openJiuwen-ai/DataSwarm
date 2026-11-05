# 知识库概念与单/多 KB 模式

## 名词

- **知识库（KB）**：一个 ChromaDB collection，由 `kb_id` 标识。向量数据存在 `<workspace>/.kb_chroma/<kb_id>/`。元信息（名称/描述/标签）存在 `<workspace>/.kb_chroma/registry.json`。
- **文档（document）**：入库的一份文件（PDF/Markdown/Word/…），由 `doc_id` 标识。入库时被切分成多个 chunk。
- **chunk**：文档的切分单元，向量检索的最小粒度。默认 chunk_size=512、overlap=50（代码内固定，不可配）。

## 默认知识库

系统内置一个默认知识库 `biz_kb`。当用户**没指定**要操作哪个 KB 时：
- 所有 `kb_*` 工具的 `kbId` 参数省略即操作 `biz_kb`。
- `biz_kb` 在首次入库（或首次 `kb_update`）时自动创建，无需显式建库。

默认库能满足时就省略 `kbId` 调用，不必先列库或向用户确认用哪个库；只有用户主动提到另一个库，才需要先 `kb_list` 选目标。

## 何时进入多 KB 模式

默认 `biz_kb` 已能满足大部分"把业务文档灌进一个库"的场景。只有以下情况才需要多 KB：

- 业务上需要**隔离**不同来源的知识（例如"产品手册库"与"合规制度库"分开，避免检索时互相污染）。
- 用户**显式**要求"新建一个知识库 / 换到另一个 KB"。

多 KB 时：
- 先 `kb_list` 看有哪些 KB。
- 需要一个新库时用 `kb_create(kbId=..., name=..., description=...)` 显式创建（kbId 仅限字母/数字/下划线/中划线，1-64 位，不可重名）。
- 后续操作带 `kbId=<目标 KB ID>` 即可指向具体 KB。
- `kbId` 参数在所有 `kb_get`/`kb_update`/`kb_list_entries`/`kb_upload_file`/`kb_attach_dir`/`kb_search` 上都可用。

> 会话的 KB 按数据源工作空间天然隔离（每个工作空间含独立的默认 `biz_kb`），无需在 `kbId` 里编码工作空间信息。

## 文档与 chunk 的关系

一份文档 → N 个 chunk。`kb_list_entries` 返回的是**文档维度**的清单（每条含 `doc_id`、`file_name`、`chunks` 数量），不是 chunk 维度。`kb_search` 返回的是 chunk 维度的结果。
