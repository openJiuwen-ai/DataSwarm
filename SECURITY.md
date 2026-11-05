# 安全策略

## 报告漏洞

如果你发现 DataSwarm 的安全漏洞,请通过
[GitCode 仓库 issue](https://gitcode.com/openJiuwen/DataSwarm/issues) 报告。

为避免漏洞在修复前被公开利用:

- 标题请写「Security: <一句话概括>」,正文只描述影响面与复现前提,
  **不要贴出可被直接利用的完整 PoC**
- 涉及凭据泄露(如连接串、API Key)的报告,请先脱敏
- 收到报告后维护者会尽快确认并跟进修复进度

## 报告范围

- DataSwarm 自身的代码与脚本(`scripts/dataswarm/`、`deploy/` 及各产品模块)
- 运行时配置与数据链路中的凭据处理(如连接串去凭据约束被绕过)

不在范围内:

- 依赖库自身的漏洞(请直接向对应上游报告;可同时在本仓开 issue 通知升级)
- 未配置任何认证就暴露到公网的自部署实例

## 安全约束(贡献者须知)

- 禁止硬编码密码 / 密钥 / token;禁止对不可信数据使用 `eval` / `exec` / `pickle.load`
- 数据源连接串必须去凭据后再进入 artifact / plan / 历史表;
  凭据只从运行时 config 解析
- 外部命令一律 `shutil.which()` 取绝对路径执行,禁 `shell=True`
