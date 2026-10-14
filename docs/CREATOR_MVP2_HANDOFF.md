# Creator Mode MVP-2 交接

日期：2026-10-13
分支：`creator-mode-mvp-2`

## 已实现

- `brain/creator_install.py` 为每次批准创建一次性审批记录。
- 审批记录绑定草稿 ID、版本、类型、安装目标、每个文件的 SHA-256/字节数和整体摘要哈希。
- 批准前重新运行现有 Creator/P4 静态检查器；修改文件、版本或 Manifest 后旧审批失效。
- 默认有效期 10 分钟；过期、已消费、失败消费和并发重复消费都会拒绝。
- 安装前重新读取并核对完全相同的文件字节，再生成内存 ZIP。
- Plugin 委托现有 `plugin_ops.install` 事务；Skill 委托现有 `plugins.skill._install_zip` 事务。没有新增文件复制器，也没有自动安装 pip/npm 依赖。
- 保留既有路径、Manifest、静态检查、Token 鉴权、回滚和 `restart_needed` 语义。
- `POST /api/creator/approve` 与 `POST /api/creator/install` 只接受服务端已认证管理员；没有模型工具入口。`GET /api/creator/approvals` 只读。
- Creator 草稿卡增加“审核并安装”面板、安装目标、代码执行风险、明确批准、取消和一次性安装状态。安装成功后通知能力页刷新 Plugin/Skill 列表。

## 身份边界

当前服务端能确认的是 Brain Token 或管理员设备 Token 及其角色。审批记录保存 `device_id` 和 token 类型，服务端不会把前端按钮、草稿 ID 或审批 ID 当成身份凭证。当前版本没有操作系统级强真人身份验证；交互仍需用户在 Electron 界面明确点击，但服务端可确认的身份边界是管理员 Token。

## 针对性验证

在临时目录中运行 `brain/tests/test_creator_install.py`：7 个测试函数通过，覆盖：

1. 未授权审批和非法身份安装拒绝；
2. 静态验证失败不能审批/安装；
3. 草稿修改后旧审批失效；
4. 审批过期和重复使用拒绝；
5. 并发安装最多消费一次授权；
6. 既有 Plugin 事务入口收到相同字节快照且生成代码未被 import/执行；
7. 安装失败记录事务返回的回滚状态且不允许重放；
8. Skill 通过既有 `_install_zip` 边界接收带包根目录的归档。

另外：

- `python -m py_compile brain/creator_install.py brain/server.py` 通过。
- `node --check client/src/js/pages.js` 通过。
- 未重复运行完整 P3/P4/Creator 测试矩阵。
- 当前 Codex 云端环境没有 Windows Electron 和运行中的 Skill Host，因此 Electron 真人点击、真实 Skill Host 热重载和 Windows 安装包验收仍待 Windows 本机完成；不能把模拟 UI 或 stub Host 结果记作真人 E2E 通过。

## 风险与后续 Windows 验收

1. 在隔离 `VIGIL_HOME`、测试管理员 Token 和无害 Plugin/Skill 下启动 Brain/Electron。
2. 核对草稿卡显示的完整文件、目标和风险说明，真人分别执行“明确批准”和“取消/拒绝”。
3. 证明拒绝不调用安装事务，批准只产生一次安装，重复点击被服务端拒绝。
4. Skill 验收必须使用已授权的 Skill Host；若 Host 不可达，报告事务回滚/未验证，不降低策略。

## 未做的事情

本阶段没有修改 P3 真人审批策略、模型快速切换、启动性能、生产 Token、生产服务器或 master，也没有自动触发安装。
