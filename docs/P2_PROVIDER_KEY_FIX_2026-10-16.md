# P2 最后一个模型密钥归属失败修复

基线 `18634a610b9d722ca883d816b5d1badbdd05b69b`；只修改模型中枢、云端模型请求层及相关测试。

## 原因

旧 `apply_cfg()` 先替换 provider / Base URL，再让 `provider_key()` 从当前 `api_key` 回退查询。活动身份已经改变，旧密钥会被新身份认领。同一 provider 改 Base URL 也存在相同风险。

## 修复

- 从切换前的配置解析旧密钥和目标密钥。身份按 provider + API Base URL 区分；URL scheme/host 大小写归一，路径大小写保留。
- 切换前保存原密钥的地址绑定；目标没有已绑定密钥时清空活动 `api_key`。公开状态显示未配置，切回原 provider / URL 自动恢复原密钥。
- 旧单密钥配置仍在其原活动身份下有效；旧字符串密钥记录保留。切换时为这些记录固定原地址/原服务商默认地址，不能跟着新的活动 URL 自动迁移。
- 已保存模型列表不再充当密钥归属证据。非活动自定义地址的老密钥若缺乏可确认的地址绑定，保留原记录，需在该地址明确填写密钥后才能使用。
- 云端请求层使用经归属校验的密钥；无密钥时普通聊天不发送请求，流式及探活也在发出网络请求前停止。历史错误配置中已知属于其他身份的密钥不能被当前 provider 认领。
- 现有快速切换 HTTP 测试 fixture 显式声明两个模拟服务的地址绑定；不再依赖“存在模型引用即允许把钥匙发到任意地址”的旧行为。

## 验证

- 新增 11 项 `tests/test_provider_key_boundaries.py`：两个真实 loopback HTTP 模型服务记录 Authorization 和正文，验证未配置目标不会收到原密钥；普通/流式切换准确、恢复原身份、不同 provider 同 URL、同 provider 不同 URL、多自定义地址、路径大小写、旧配置序列化、显式授权同字符串密钥及历史错误归属。
- 相关模型测试合计 71 项通过。
- 本机完整 `python -m pytest tests -q --junitxml=...`：937 项，926 passed、11 skipped、0 failed、0 errors；实际执行通过率 100%。
- CI 的 Node 共享订阅/UI 回归 7 项通过，`python -m compileall -q brain pet` 通过。

原 P2 验收报告保留为历史记录；其中所列唯一遗留密钥归属失败在本次修复。本次不修改 Creator、插件市场，不合并 master，不部署或构建安装包。线上 CI 结果以本次提交触发的 Actions 为准。
