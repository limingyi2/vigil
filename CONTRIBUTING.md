# 参与开发

开发分支为 `dev`。提交前先确认当前分支、工作区和改动范围，避免把本机配置、运行数据或其他任务的文件一起加入提交。

## 环境

使用 Python 3.12 和 Node.js 22。Brain 依赖在 `brain/requirements.txt`，测试依赖在 `requirements-dev.txt`，客户端使用 `npm ci --prefix client` 安装锁定版本。

源码启动见 [客户端开发](client/README.md)，完整安装包构建见 [发布流程](docs/03-客户端/客户端打包与推送更新流程.md)。真实配置从示例复制，不应提交 API Key、访问令牌、用户会话或日志。

## 检查

在独立数据目录中运行受影响的回归。基础检查为：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m compileall -q brain
node --test client/tools/test-p2-stream-lifecycle.js client/tools/test-unified-runtime.js client/tools/test-public-updates.js
git diff --check
```

涉及窗口、preload、SSE、插件资源或安装布局时，补充对应的 Electron 或 Windows 验收。报告使用的源码提交、命令和结果；跳过、受阻和未验证项分别说明。

## 提交

提交说明应写清触发问题、改动后的行为和验证方式。无关的格式、功能与历史清理分开提交，便于回退和评审。改接口时同时更新调用方、状态语义和契约。

注释用于解释约束、兼容原因和不明显的算法，不复述代码。删除失效的操作步骤、个人调试叙述和缺乏上下文的警告；保留仍可验证的原因、来源和第三方署名。

## 文档

- 使用说明描述当前可用功能；设计稿写明状态和适用阶段。
- 命令应能在标注的目录和环境中执行，不假定依赖或本机快捷方式已经存在。
- 路径、字段、权限和版本以实现为依据。描述网络行为时说明实际连接，不使用笼统的“完全离线”。
- 性能结果注明数据集、模型、环境和测量方式，不将单次结果写成通用能力。
- 历史测试输出、评测样本和第三方许可保留原始内容。修改正文时不要改写证据。
- 新增文档登记到 [文档索引](docs/README.md)，临时排查过程优先写在 PR 中。

## 许可与用户数据

项目许可见 [LICENSE](LICENSE)。第三方代码、字体和资源按各自许可处理，仓库公开不代表可以任意分发。用户数据与凭据维护见 [PRIVACY_MAINTENANCE.md](PRIVACY_MAINTENANCE.md)。
