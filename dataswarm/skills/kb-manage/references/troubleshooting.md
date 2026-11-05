# 常见错误与自愈

## 工具返回 `disabled: true`

所有 `kb_*` 工具在知识库未就绪时返回 `{"disabled": true, "error": "..."}`。两种原因：

| `error` 含义 | 原因 | 正确做法 |
|---|---|---|
| `Knowledge base feature is disabled` | `config.yaml` 的 `knowledge_base.enabled` 为 false | 告知用户让管理员开启；**不要重试**。 |
| `Knowledge base not available` | manager 初始化失败（如 chromadb 拉起失败） | 告知用户检查环境；**不要重试**。 |
| （含 `embed config is incomplete`） | `config.yaml` 的 `embed` 段（`embed_api_key`/`embed_base_url`/`embed_model`）缺失 | 告知用户补全 embedding 配置。 |

> 先读返回的 `error` 字段，按上表判断原因后把结论告诉用户；同一调用不要反复提交。

## kb_upload_file 返回 `success: false`

| 场景 | 原因 | 做法 |
|---|---|---|
| `no documents parsed (unsupported format...)` | 文件格式不支持或文件为空 | 确认是支持的格式（见 upload-guide.md），且文件非空。 |
| `no file paths provided` | `paths` 为空数组 | 传入非空路径列表。 |
| `path does not exist: ...` | 文件路径不存在 | 让用户确认正确路径。 |

## kb_attach_dir 返回空 `files`

- 目录下没有支持格式的文件 → 提示用户把文档放进目录，或改用 `kb_upload_file` 逐个传。

## kb_list_entries / kb_get 的 chunk 数为 0

- `kb_get` 的 `chunk_count=0` 且 KB 刚建：说明还没入库过文档，正常。先 `kb_upload_file` 入库再查。
- 已入库但仍为 0：可能 KB 尚未实际构建（懒构建，首次入库才拉起 ChromaDB collection）。再入一次或检索一次即可触发构建。

## 检索返回空（kb_search）

这部分属于 `kb-search` 技能，见 `kb-search/references/search-params.md` 的"结果为空时"。
