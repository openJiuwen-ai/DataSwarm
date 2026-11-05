# 入库指南（upload_file vs attach_dir）

## 选哪个入库入口

| 用户给的是 | 用哪个工具 | 参数 |
|---|---|---|
| 一个或多个**文件路径** | `kb_upload_file` | `paths=["a.pdf", "b.md"]` |
| 一个**目录路径** | `kb_attach_dir` | `dirPath="/abs/path/to/docs/"` |

判断规则：
- 用户说"导入这个文件 / 这几个文件"，路径指向文件 → `kb_upload_file`
- 用户说"把这个目录/文件夹的文档都导入"，路径指向目录 → `kb_attach_dir`

> 选错入口会被参数校验拦下：目录路径交给 `kb_upload_file` 会在路径校验阶段失败，文件路径交给 `kb_attach_dir` 会因"不是目录"失败。

## kb_upload_file — 本地文档入库

```
kb_upload_file(paths=["/abs/a.pdf", "/abs/b.md"], kbId?)
```
- `paths`：文件路径列表，**支持一次多份**。每份独立解析，单份失败不影响其他份（失败会被跳过并在结果里体现）。
- `kbId`：省略取默认 `biz_kb`。
- 返回 `success`/`doc_ids`/`files_requested`/`docs_indexed`。

### 多选规则
- `paths` 数组里每个元素是一个文件路径，可多份：`paths=["a.pdf","b.pdf","c.md"]`。
- 一次调用对应一次批量入库，不要为多份文件分多次调用（除非用户明确要分批）。

## kb_attach_dir — 挂载本地目录

```
kb_attach_dir(dirPath="/abs/path/to/docs/", kbId?)
```
- `dirPath`：目录路径（必须是已存在的目录）。
- 递归扫描目录下所有**支持格式**的文件，全量入库。
- 返回 `files`（扫描到的文件列表）+ 入库结果。

### 递归与过滤
- 默认**递归**扫描子目录，所有支持格式的文件都入。
- 不支持过滤后缀（当前实现）；要只入特定文件，用 `kb_upload_file` 逐个传。

## 支持的文件格式

`.txt` `.md` `.markdown` `.pdf` `.docx` `.doc` `.xlsx` `.xls` `.csv` `.tsv` `.html` `.htm` `.json` `.png` `.jpg` `.jpeg` `.webp` `.gif` `.jfif`

不在此列表的格式会被跳过（`kb_upload_file` 在解析阶段 skip，`kb_attach_dir` 在扫描阶段就不收）。

## 路径要求

- **必须存在**：文件/目录不存在会被拒绝。
- 绝对路径优先；相对路径相对 agent 工作目录解析。
- 路径穿越到敏感目录（如 `../../../etc/passwd`）会被校验拒绝。
