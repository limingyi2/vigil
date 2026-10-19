# 1.1.6 云端上下文三档：Windows 候选包交付

2026-10-19。仅推送 GitHub `dev`，不修改 master，不创建 GitHub Release、不上传/发布正式版本。候选包 `candidate=true` 可用于本地安装验收。

| 项目 | 独立核验结果 |
|---|---|
| 版本 | **1.1.6 / code 27** |
| Windows 安装包 | `F:\vigil-context-tiers\client\dist-release\Vigil.Setup.1.1.6.exe` |
| 大小 | **163,961,744 字节** |
| SHA-256 | `c021a4a57a70c5e7c032c89807fb3a61edadbec36bf02c4c320fa18ef039e662` |
| 构建源提交 | `949ff0c4aa7441977ba4e086f0cfdddb18b5f536` |
| 基线 | `b1a8f1f2d653a80f2d660333a40544cc18106962` |
| 源文件摘要 | `22233611a1044a8d88b2973f6975a332907b3207cea8bef392dc704ae567b203` |
| 构建类型 | 本地候选，`candidate=true` |
| 便携包 | `F:\vigil-context-tiers\client\dist-release\Vigil 1.1.6.exe` |
| 发布清单 | `F:\vigil-context-tiers\client\dist-release\release-manifest.json` |
| 安装内容清单 | `F:\vigil-context-tiers\client\dist-release\Vigil.Setup.1.1.6.contents.json` |

干净提交后使用现有 `scripts/build_release.py --candidate` 构建。构建后 `--verify --candidate` 通过，独立 Python 与 PowerShell 分别计算安装包 SHA-256 和大小并与 manifest 一致。独立脚本比对源提交内版本文件、Electron build-info、独立 Brain manifest、客户端内嵌 Brain manifest；最终 exe 的 PYZ 内实际包含上下文压缩与历史检索模块。

构建源 `949ff0c` 已推送并核验远端 `refs/heads/dev`。本记录及校验副本作为后续文档提交推送，不改变安装包的源身份。未运行安装程序覆盖用户当前安装；原工作区未提交文件、实际配置、聊天数据和上一版 1.1.5 安装包保持原样。

## 已打包 exe 的实际验证

使用临时副本、临时 VIGIL_HOME、合成 40 条 PC 历史、loopback Fake Ollama；不访问真实聊天或付费 API。

1. 依次保存 96K、128K、300K，通过已打包服务真实 HTTP API 的 stats 验证偏好已经传递到运行时。
2. 重启真实 exe，重新读取模型菜单 API，300K 偏好恢复。该大容量是测试夹具显式声明的合成能力，不能当成实际云端模型支持 300K 的证据。
3. 历史检索状态为“BM25 关键词检索”；真实聊天请求注入历史原文证据，找回早期端口 `18883`。原始 40 条存档前缀未被改写。
4. 同一请求仍通过本地 provider 使用 `num_ctx=12288`、`num_predict=2048`，云端偏好未覆盖本地预算。
5. 原有打包后端 health、插件播种、Wallpaper JS/CSS 与版本稳定性 smoke 通过。

真实 Electron renderer/preload 回归还覆盖三档单选、容量不足/未知提示、完整客户端子进程重启、模式切换保留消息节点与草稿、键盘导航和原模型管理功能。

## 最终测试结果

- 全量 Python：**1359 收集，1342 通过、17 个既有条件跳过，0 失败/错误**。
- P8.4：**18/18**；P8.5：**39/39**；本轮新增：**31/31**。
- 预算/模型/HTTP/运行时定向：**117/117**；另一次涵盖完整运行模式的定向 **128/128**。
- 前端 CI 对应 Node：**24/24**；真实 Electron 扩展验收通过。
- 打包用 Python 3.12.14：本轮 + P8.5 + 版本检查 **75 通过、1 条旧母本缺失跳过**。
- Python 编译、JS 语法、`git diff --check` 通过。

本轮没有配置 Embedding API、新建向量数据库、下载模型、修改检索算法或委派压缩逻辑。未知模型容量使用保守窗口，选择 300K 本身不会授权 300K 请求；已有可用向量后端沿用原路径，未验证或失败状态不会冒称语义检索。

完整文件改动、预算规则与限制见 [实施报告](cloud-context-tiers.md)。机器可读证据在 `cloud-context-evidence/`，包括测试 XML、Electron 输出、打包实际请求结果、build-info、release-manifest、安装内容与两套独立校验结果。
