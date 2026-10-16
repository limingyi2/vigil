# 一体化客户端与 Brain

开发基线：P2 `a3d0ba0ddf90118647c03f7784a6c6ae67735faf`；独立分支 `codex/unified-client-brain`。

## 设计与迁移风险（实施前方案）

复用 electron-builder / PyInstaller / Sidecar / 现有认证和 NSIS 更新。一个源快照生成两份相同构建身份，Brain 文件清单逐项校验。启动检查 commit、版本、源码摘要、协议与 Creator Jobs/Agent Runs 等能力，并用已认证的真实路由再次确认。校验失败停止服务，不扫描端口或回退其他 Brain。

本地是默认模式。随包 EXE 是唯一启动目标，旧 sidecar_exe 只作为数据来源。显式选择远程模式后，才使用 remote_base 与原远程凭据。sync.cloud_url/cloud_token 独立保留；微信桥、跨设备转发继续复用现有机制，不从旧 base 隐式推断云端。

用户目录默认 `%APPDATA%/vigil-client/brain`，包含 config/data/plugins/skill_host/skills/logs/archive；只读二进制位于安装资源目录。`VIGIL_USER_DATA` 允许隔离测试或明确指定整个用户目录。冻结 Brain 同样遵守 VIGIL_HOME。Skill Host 使用随包 Electron 的 Node 运行时和随机回环端口、认证密钥，不要求用户安装 Node/Python。

迁移风险：旧目录可能有冲突文件、活动 WAL 数据库、旧绝对路径、第三方插件自定义存储。迁移只复制，不删除来源；SQLite 使用在线备份而非复制 WAL 文件；目标已有文件不覆盖，源冲突副本保存在 migration/conflicts。仅对已迁入的 persona_file/memory_dir 内部路径重定向，不改用户授权根或外部文件路径。迁移失败保留来源，拒绝悄悄创建一份空配置继续使用。多个源按显式旧 sidecar、旧安装备份、随包历史目录顺序处理。

第三方插件若把数据硬编码在迁移范围之外，不自动修改插件源码。原目录与冲突副本均保留，可由用户明确处理。

## 构建门禁

Windows 任意工作目录：

```powershell
python scripts/build_release.py
# 或 scripts\build_client.bat；自动先构建 Brain，再打包 Electron。
```

脚本使用自身位置定位仓库。需要 Python 打包依赖及 client npm 依赖；优先使用本地 venv，也可显式使用 .venv/Python 执行脚本。正式构建要求干净已提交源码；任何缺失 Brain、源码变化、版本变化、文件摘要变化均拒绝打包。Brain 中间构建目录与 `client/dist-release` 每次先清理，避免复用旧配对产物。build-info 与文件清单是生成产物，不入 Git，不含本地凭据。NSIS 文件名固定为 `Vigil.Setup.<version>.exe`，发布清单直接采用产物文件名，并核验提交、版本、大小和 SHA-256。

未发布的验收构建使用 `--candidate`，标记 candidate=true，更新器会拒绝。Windows Actions 在一次性 hosted runner 上下载并校验正式 Vigil 1.0.2 安装包，再以正式 appId 在临时安装目录覆盖升级到 1.1.0；不要在装有日常 Vigil 的电脑上运行这个生产身份安装器。Actions 产物仅用于验收，不创建 Release，也不推送到用户更新通道。

## 更新和恢复

未配置个人云服务器时，从公共 GitHub Release 的 release-manifest.json 获取更新；候选包、错误摘要或非 GitHub 下载来源被拒绝。已有明确云端更新配置可继续使用。

沿用一个 NSIS 安装包更新 Electron 和 Brain。下载必须有正确大小及 SHA-256，下载不完整不安装。NSIS 在卸载旧程序前，用旧 Electron 的 Node 模式备份并校验整套应用，检查点保存在独立用户目录 updates；备份失败取消更新。新实例只有完整兼容检测通过后才标为 healthy。

新 Brain 无法就绪时提示恢复，恢复助手在应用退出后校验并暂存完整旧版，替换整个应用目录；失败则保留原目录。用户数据不随二进制回退，故本阶段不做不可逆数据库格式升级。旧安装里 EXE 旁的数据从检查点备份迁入，防止 NSIS 删除旧程序目录时丢失。保留失败版本和备份便于排查，需要额外磁盘空间。恢复程序恢复二进制配对，Windows 安装器显示版本/注册信息可能仍是失败升级的版本，后续重装会刷新。

## 验收入口

```powershell
python -m pytest tests/test_unified_runtime.py -q
node --test client/tools/test-unified-runtime.js client/tools/test-p2-stream-lifecycle.js client/tools/test-public-updates.js
python scripts/build_release.py --candidate
```

Windows Actions 使用官方 `Vigil.Setup.1.0.2.exe` 作为升级基线，校验 SHA-256 与大小后执行 NSIS 安装、真实覆盖升级、旧目录迁移、Brain 失败回滚及检查点失败保护。测试验证模型配置、聊天记录、Creator 草稿、插件、Skill 和其他数据；打包后的 Electron/Brain 版本身份必须配对。Creator 使用隔离的本地模型替身，验收流式输出、失败记录、诊断和草稿；不使用真实 API Key。

Creator Renderer 错误诊断与 GitHub Release 清单/下载/重定向/大小/SHA-256 检查另有自动化回归。当前公开 latest 仍是 1.0.2，尚无 1.1.0 release-manifest；正式 Release 创建前只验证更新器的隔离测试，不把未发布包报告为线上可更新。

内置插件更新使用上次随包代码摘要识别未修改文件；用户改动保留。旧版迁移时使用旧包 `_internal/plugins` 辨认原版代码。Skill Host 使用独立 ESM 技能目录，Brain 退出后宿主自动结束。
