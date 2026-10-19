# P8.5 Windows 交付记录

2026-10-19，桌面 **1.1.5 / code 26**，正式构建 `candidate=false`。

| 项目 | 已核验结果 |
|---|---|
| 安装包 | `F:\vigil-p85\client\dist-release\Vigil.Setup.1.1.5.exe` |
| 大小 | **163,955,629 字节** |
| SHA-256 | `fb910bdd51d6757abceccafae6053222ccb0da33e0f6684f3412527b2ae450c1` |
| 构建源提交 | `0c1c75a9cb60885c228f60b3b0a55da684570ad8` |
| 基线 | `7d961619608c54053e198ef283e00b0a93b56ca6` |
| 实现提交 | `479c31c`，加微信拆条存档兼容修复 `0c1c75a` |
| 构建源摘要 | `a9256ccff2bb98d024fcd8d703ac085c29b099e9d28c8adedc90f43145e57896` |
| 构建运行时 | Python 3.12.14、PyInstaller 6.22.3、Electron 28.3.3 |
| 便携包 | `F:\vigil-p85\client\dist-release\Vigil 1.1.5.exe` |
| 原始发布 manifest | `F:\vigil-p85\client\dist-release\release-manifest.json` |
| 安装内容清单 | `F:\vigil-p85\client\dist-release\Vigil.Setup.1.1.5.contents.json` |

已从干净、提交后的隔离 worktree 执行正式构建。构建后 `scripts/build_release.py --verify` 通过；独立校验脚本和 PowerShell `Get-FileHash` 分别重算 SHA-256/大小，与发布 manifest 一致。独立脚本还核对 Git 源提交的版本、Electron build-info、独立 Brain manifest、Electron 所携带的 Brain manifest，并确认最终 exe 的 PYZ 中实际包含 `conversation_context` 与 `history_retrieval`。

已执行 `git push origin HEAD:dev`，远端 `refs/heads/dev` 已核验包含构建源 `0c1c75a9cb60885c228f60b3b0a55da684570ad8`。本交付记录与证据是后续文档提交；不改变安装包的构建源身份。没有修改 master，没有删除分支或旧安装包。

## 最终验收

- Python 全量：**1328 收集，1311 通过，17 条既有条件跳过，0 失败**。
- 新 P8.5：**39/39**；其中包含 P8.4 **18/18** 子进程验收。P8.5 + Agent/HTTP 定向 **72/72**；打包 Python 3.12 同组测试通过，JUnit 将 8 个 subTest 一并计入、报告 80。
- 前端 CI 对应 Node：**24/24**。
- 全仓 Python 编译、版本一致性（5 通过、1 条旧母本缺失跳过）、`git diff --check` 均通过。
- 真实 Ollama：在隔离合成会话中结构化摘要保存成功，端口 `18787` 可回查。不是 FakeProvider 成绩，详细 usage 与预算见 `p85-evidence/ollama.json`。
- 已打包 Brain 的独立临时副本：health、插件播种、Wallpaper JS/CSS 与版本稳定性 smoke 通过。
- 已打包 Brain 的历史检索端到端 smoke：**通过**。临时 VIGIL_HOME + 合成 40 条 PC 原始消息 + loopback Fake Ollama，通过真实 exe 的 `/api/chat/stream` 回查旧事实 `18883`；实际发送的请求含独立历史原文证据块，索引成功落盘，原有存档前缀未改写；模型 options 为 `num_ctx=12288`、`num_predict=2048`。这证明打包后的检索链路可用，不冒充真实模型语义准确率。

## 使用边界与保留材料

本机没有配置/安装历史检索用的本地 Embedding 模型，**实际为 `vector_available=false`，降级 BM25**。没有自动下载模型，也没有调用云端 Embedding 兜底。真实云端调用、通用历史问答 95% 准确率未作实机验证；合成精确事实样本及向量 FakeBackend 的验收范围见 `p85_report.md`。

安装包位于仍保留的 `F:\vigil-p85` worktree，可直接取用。没有替用户运行安装程序或覆盖既有安装/配置。原工作区未提交文件和旧安装包保持原样。中途为补齐微信拆条存档兼容而停止的首次构建材料也保留在 `build/brain-release-first-attempt`、`dist/brain-release-first-attempt`，不作为交付产物。

机器可读校验文件：`p85-evidence/release-manifest.json`、`build-info.json`、`installer-verified.json`、`powershell-hash.json`、`installer-contents.json`、`packaged-context.json`。完整改动文件、根因、预算矩阵、测试隔离和已知限制见 [实现报告](p85_report.md)。
