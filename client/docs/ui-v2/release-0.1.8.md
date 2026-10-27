# Vigil UI V2 0.1.8 打包与安装记录

> 历史资料：本文保留对应阶段的设计、用法或验证记录，部分模块已移除。当前桌面版请查阅 [文档索引](https://github.com/limingyi2/vigil/blob/dev/docs/README.md)，不要直接照本文部署或推断已交付功能。

日期：2026-10-11。开发分支：`ui/v2-chat-redesign`，最终发布至 `master`。应用源码基线：`f8f3a821618953cdec5bf1bf8533f316c0a446de`。

## 发布范围

- 合入 `origin/master` 的 `8ae1d8f`，保留已提交的混合模式、本地委派设置及其数据绑定；模型设置函数与该主分支版本一致。
- 保留两轮聊天 UI 改造，版本提升至 0.1.8；安装包排除 `_harness.html` 和 `_devstub.js`。
- 使用合并后的源码重新构建随包 Brain，生成 Windows x64 安装版与便携版。
- 完成独立分支回归后，按确认将源码快进推送至 `master`，不使用强制推送；UI 分支保留用于版本对比。

## 验证

| 项目 | 结果及边界 |
| --- | --- |
| 合并后 UI 回归 | 36 组隔离 Electron 检查通过，使用正式 preload 与夹具 API；没有真实模型请求 |
| 随包 Brain | 在临时副本启动，健康接口、插件种子、壁纸 JS/CSS 和插件版本稳定性检查通过 |
| 安装包 | ASAR 内关键 Electron/UI 文件与源码一致，版本为 0.1.8，预览文件被排除，Brain 资源存在 |
| 覆盖安装 | NSIS 静默安装退出码 0，安装路径 `F:\vigil`，注册版本为 0.1.8 |
| 安装一致性 | 已安装 ASAR 和 Brain EXE 的 SHA256 与构建产物一致 |
| 配置保留 | 安装完成、首次启动前，保存配置文件 SHA256 与安装前备份一致 |
| 正式启动 | 已安装 `Vigil.exe` 启动后进程存活，存在标题为 Vigil 的主窗口；未执行真实对话，也未对该窗口完成全部视觉回归 |

原有配置中的外部 Brain 路径保留，因此随包 Brain 的隔离检查不能代表现有外部 Brain 已被更新。没有覆盖生产历史、记忆或凭据。

## 产物

构建环境：Electron 28.3.3、electron-builder 24.13.3、Python 3.13.15、PyInstaller 6.22.3。

| 产物 | 字节数 | SHA256 |
| --- | ---: | --- |
| `Vigil Setup 0.1.8.exe` | 244065838 | `25E4FF814CC3E7809A99AF079B49C27C55AA7299ECE9BE340D48E92E2CCDF29E` |
| `Vigil 0.1.8.exe` | 243844784 | `CEE2CB08B089AFB7A666ED7456F7922DD42CFB886451AFA4135E1BC77C40F1ED` |

产物位于构建目录 `client/dist/`，二进制文件未提交到 Git。安装前的 ASAR 与配置备份位于本地忽略目录 `client/_shots/ui-v2/install-backup-0.1.7/`，没有上传。

## 已知限制

第二轮测试中出现过一次 Wallpaper 首帧超时，复跑成功；原因仍未定位。本次没有修改其核心渲染机制。完整验证边界、截图与失败记录见 [第二轮报告](round2.md)。
