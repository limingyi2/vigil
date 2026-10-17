# P2 验收修复（2026-10-16）

基线：`p2-agent-execution` / `f397b85ff4e6ccbfaddda8df710dd2133360afb1`。
原 CI：[38890231635](https://github.com/limingyi2/vigil/actions/runs/38890231635)，875 项、15 failures、22 errors、10 skipped。

## 原有 37 个失败项分类

| 分类 | 数量 | 原因和处置 |
|---|---:|---|
| 旧测试契约失效 | 28 | 14 个错误仍调用已移除的文本工具链，1 个引用退役 APK 构建脚本；13 个断言依赖旧密钥存储形状、URL 密钥/跨地址回退、旧掩码、已移除工具和路由首次字符串位置等。更新为当前安全契约，未恢复旧生产接口 |
| CI 环境、编码与平台差异 | 8 | pytest 缺失 3；硬编码 Windows Node 路径 1；AST 读取 UTF-8 BOM 3；跨平台绝对路径分类 1。显式安装 pytest、用 pytest 收集函数测试、用 PATH Node 和临时文件语法检查、按 Python 合法 BOM 处理、验证 runner 原生绝对路径越界 |
| 已存在的真实模型设置缺陷 | 1 | `TestProviderKeyStore.test_切到没存过密钥的厂商不换key`：`apply_cfg` 切换 provider 后仍保留旧 api_key，provider_key 的当前配置回退将其识别为新家密钥。保留失败断言，不用 skip/xfail 或放宽预期隐藏问题。本次不修改模型设置实现 |

旧文本工具测试替换为原生 schema 调用、未知工具、失败回退及“文本标签不可执行”的测试。数量变化来自实际移除过时测试并补充当前安全路径；pytest 也首次执行原来 unittest 未收集的函数用例。

## P2 本次修复

- Creator 使用兼容原有 `(draft, error-string)` 返回形式的 `DraftFailure` 携带安全结构化诊断。JSONDecodeError 的具体原因、行、列、字符位置不再丢失；不包含 JSON 文档片段。
- 作业快照和事件保留真实 stage、错误码、HTTP 状态、建议和日志 ID。认证与磁盘保存错误分别归类，静态校验失败保持 draft 关联并提供相同诊断字段。
- 前端实时错误与历史恢复都显示这些字段，错误以 textContent / 已转义文本显示，错误状态提供 alert 语义。
- 诊断日志只写日志 ID、stage、code、HTTP 状态和数字位置；不写异常字符串、Provider body、模型原文、Key、Token 或系统提示词。删除聊天分片和完整流返回对象的 console 日志。
- 共享流式监听在最后一个订阅退出时移除 IPC listener；重复订阅相同函数彼此独立，异常和重入注销隔离。
- 普通聊天在 done/error、异常、降级和缺少终止事件的超时路径注销；Creator 在 finally 注销；气泡被切页/刷新移除时 MutationObserver 立即注销并断开观察。没有取消后台作业或自动重发请求。
- 本次未修改插件市场、任务执行权限、审批接口或模型设置实现；未合并 master、部署或生成安装包。

## 验证

- `python -m pytest tests -q --junitxml=...`：本机 926 项，914 passed / 1 failed / 11 skipped / 0 errors，实际执行通过率 `914/915 = 99.89%`；失败项为上表模型设置问题。
- `python -m pytest brain/tests/test_creator_mode.py brain/tests/test_creator_install.py -q`：30 passed。
- 新增 Creator 诊断测试 7 项：安全 JSON 原因/位置、缺字段/未闭合、401 脱敏、任意日志原文拒绝、静态校验关联、保存权限错误、未知失败降级；已计入 926 项。
- `node --test client/tools/test-p2-stream-lifecycle.js`：7 passed，含 100 轮共享注册/注销、重复回调、异常隔离、重入注销、页面移除、终态关闭、Creator 安全错误渲染及页面发送路径契约。
- Windows Electron `client/tools/test-p2-creator-electron.js`：通过；真实 renderer/preload，隔离 IPC，20 轮聊天、全部 7 种 Creator 阶段、XSS 字符文本显示与切页恢复。无网络/模型调用、无生产配置。
- CI 改用 pytest，显式准备 Node 22；新增 Node 回归即使 Python 剩余失败也会执行，上传 JUnit 结果，避免忽略 pytest 函数用例。

## 剩余事项

1. 模型设置跨服务商密钥归属缺陷需独立授权修复；本 P2 分支的完整 CI 预期仍会因此为红，不能宣布全绿。
2. 待本次提交触发的 GitHub Actions 完成，另行报告线上实际通过率；本机结果不代替 CI。
