# 报告与交付（分析链路收口；进入报告阶段前读）

> **数据就绪 ≠ 完成**：各成分取数/结果检查完成后**必须走完本步**（报告生成 +
> `send_file_to_user`）才算交付——把「核心数字已就绪」当最终答复直接收尾是不合格交付。

## 1. 报告组装

报告一律经 dw-report（**禁止自建报告脚本**：禁止手写 build_data.py /
render_report.py / 手拼 HTML——自建产物不入 artifact 约定目录，账本断链）。

**HTML 落盘后必须过交付门禁**，非 0 退出禁止交付：

```text
python "${JIUWENSWARM_DATA_DIR:-$HOME/.dataswarm}/agent/workspace/skills/dw-report/scripts/dw_report.py" \
  verify --html <report.html> --min-charts 1
```

`--html` 用渲染脚本返回的 `html_path` **原样**传入，禁止自己拼日期戳猜文件名
（猜错即 `unreadable: No such file` 空转）。

**禁止复制历史报告冒充本轮交付**：找不到本轮产物时（渲染失败/路径未知），如实
上报缺口并修复渲染，**不得** `cp` artifact 目录里上一轮的 `report_*.html` 当交付。

门禁拦截三类空图事故：外链 CDN（jsdelivr 国内不通 → 全空）、`series: []`
（骨架有数据零 → 全白）、无任何 `echarts.init`（报告变纯文字）。
报告内容 = **分析基线 +【归因增强】**（见 SKILL.md Step 3）：

- 分析基线：趋势/对比/分布/结构——数据已在 c1..cn 成分里，交给 dw-report 组装
- 归因增强（触发时）：data-insight 的五级路径结论，作为"驱动与归因"段的素材

**spec 深度要素与字段契约以 dw-report 为准**（`reference/quality.md` 写作
约束 + `reference/schema.md` spec 契约）——hypothesis / alternative_checked /
expected_impact / limitations / claim_type 的写法不在本 skill 重复。

mode=open 时额外：先调 `report-gate --analysis-id <id> --spec-file <报告规格文件>`，
spec 带 `mode=open`、`analysis_id`、`manifest_path`，引用 evidence_id；
mode=closed 不传 open 字段、不调 report-gate。

## 2. 交付

```text
send_file_to_user（md_path / html_path）
```

`abs_file_path_list` 传**路径数组**（`["D:/…/a.md", "D:/…/a.html"]`），不要传
JSON 字符串（单反斜杠会被错误解析，导致误报「所有文件均不存在」）。
发送失败时先核对路径是否与 write_file 落盘路径**逐字一致**，不要盲目重试。

最终答复必须包含：

1. **2-3 条结论摘要**，**关键数值直接写进答复正文**——对比类给全部对比项数值、
   趋势/排名类给首末与极值点位值；数值只留在报告文件里而答复只给定性描述
   （"详见报告"）是不合格交付
2. **可选内联关键图**（≤2 张）：答复正文用 ```echarts 代码块内联报告中的关键图表
   ——option JSON 从 dw-report 的 data.json **原样复制**（与报告同源同口径，
   禁止手改数值/改图型）；答复图表仅作速览，完整交互版仍以 html 报告为准
3. **口径声明**：「本期结果均未经确定性语义验证」+ 每个数字两维标注：
   - **锚定深度**（`grounding_level`）：G1「参考已发布口径」/ G2、G3「探索口径」
   - **选中证据**（选定指标的 `evidence_tier`）：`confirmed`「口径经人工确认的术语
     关系锚定」/ `scored`「指标由词面或语义相似度匹配，未经人工背书」
   - 两维正交：G1 的数也可能是 `scored`（指标本身已发布，但「该用这个指标回答这个
     问题」只是匹配出来的判断），**不能混为一谈**
4. 未完成成分清单（如有）
5. **建模建议**：`gap_terms` 非空时按话术点破缺口：「术语库已认识『X』（口径：
   <definition>），但尚未建模成指标——到指标建模页固化后，下次可直接锚定已发布
   口径」。**仍要尽力给探索口径结果**，不是只报缺口；无 gap_terms 但同一口径被
   追问两次以上时，也建议建模
6. **规范化声明**（仅当 `intent_normalized.completeness.defaults_applied` 非空）：
   「已将你的问题理解为：<question_std>（时间范围取 <默认值>）」——agent 替用户
   做的每个假设都要说出口

## 3. 覆盖与排他

| 关系 | 对端 | 说明 |
|---|---|---|
| 上承交付 | 主 agent → `send_file_to_user` | 本 skill 串联完把产物路径交付用户 |
| 下接语义 | `analysis_flow.py`（本 skill scripts/） | 四层链路全部经此脚本（封 dwcli） |
| 下接洞察 | `data-insight` | 只做 CSV 计算，已剥离取数流程 |
| 下接报告 | `dw-report` | 生成 + 落盘，不交付 |
| 排他 | `dw-warehouse-design` + `dw-warehouse-build` | 建仓场景不套本流程，转交对应 skill |
| 排他 | `nl2sql`（独立取数） | 本 skill 已含四层取数；纯取数不分析才直接用 nl2sql |
