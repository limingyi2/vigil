# 运维手册

本文说明桌面版的数据目录、备份、更新和故障排查。日常功能使用见 [使用说明](USER_GUIDE.md)。

## 启动与退出

安装版通过客户端启动随包 Brain。关闭窗口通常会隐藏到托盘；备份、恢复或更换运行环境前，应从托盘菜单完整退出，并确认相关进程已经结束。

源码版可分别启动 `brain/server.py` 与 Electron。独立 Brain 的监听地址以启动日志为准。多个实例应使用不同的 `VIGIL_HOME`，避免共用数据库和运行标记。

## 找到数据目录

安装版由 `client/electron/sidecar.js` 将 `app.getPath('userData')/brain` 作为 `VIGIL_HOME` 传给 Brain。实际用户目录可能被环境变量或测试配置改变，请在客户端本地内核状态中查看 `home`。

源码版未设置 `VIGIL_HOME` 时，默认数据根目录为仓库的 `brain/`。单独运行冻结 Brain 且未设置该变量时，才会采用可执行文件所在目录。不要据此推断安装版的数据在 `Program Files` 或应用资源目录。

| 相对数据根目录 | 内容 |
| --- | --- |
| `config/` | 模型配置、凭据、人设、专家与应用接入设置 |
| `data/` | 会话、记忆、任务、知识库和已安装扩展 |
| `logs/` | 服务与诊断日志 |
| `data/run/` | 进程运行标记 |

## 备份与恢复

完整退出客户端后，备份实际数据根目录中的 `config/` 与 `data/`。若配置了外部 `memory_dir`，也要备份该目录。日志可按需要保留。

以下示例的路径必须替换为本机实际值：

```powershell
$dataRoot = 'D:\VigilData\brain'
$backupRoot = "D:\VigilBackup\$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory -Path $backupRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $dataRoot 'config') -Destination $backupRoot -Recurse
Copy-Item -LiteralPath (Join-Path $dataRoot 'data') -Destination $backupRoot -Recurse
```

恢复前确认客户端和 Brain 均已退出，并保留恢复前的数据副本。恢复 `config/` 与 `data/` 后再启动。不要在旧版本中直接使用不兼容的新数据库结构；需要降级时，同时使用与旧版本匹配的备份。

备份包含凭据和私人资料，应放在受控位置。

## 更新与回退

客户端从 GitHub Releases 读取 `release-manifest.json`，按整数 `code` 比较版本。下载后的安装程序校验大小与 SHA-256，再由用户发起安装。

更新入口位于左下角和关于页。完整应用更新由安装程序与更新事务处理；本地 JavaScript/CSS/HTML 补丁有独立的暂存和启动健康确认流程，详见 [本地补丁](local-integrations-and-patches.md)。

发布文件应同时包含安装程序、`.exe.blockmap`、`.contents.json` 与 `release-manifest.json`。候选包不作为正式更新发布。安装验收报告会分别记录新装、重启、卸载保留、历史升级和用户真机测试的状态；某个工作流通过不等于所有环境都已验证。

## 日志与排查

| 现象 | 检查位置 |
| --- | --- |
| Brain 未就绪 | 客户端本地内核状态、启动阶段记录和进程日志 |
| 模型请求失败 | 服务商配置、模型状态、`logs/error_server.jsonl` 和本轮错误信息 |
| 工具未执行 | 当前权限模式、请求审批状态和工具执行回执 |
| 插件安装或加载失败 | 插件页、安装回执、`/api/plugins` 和 Brain 日志 |
| 视频无法播放 | 媒体转换状态、ffmpeg 配置、EgoAgent 健康状态 |
| 更新失败 | 更新状态、下载哈希与安装/回退日志 |

不要在 Brain 仍运行时删除运行标记或数据库文件。端口冲突时先核实占用进程；托管 Brain 使用动态端口，单看 `8788` 不能判断它是否存活。

## 凭据

模型 Key、Brain 令牌和应用接入配置不应进入 Git 或公开日志。如果凭据曾泄露，需要在对应服务撤销并重新签发，再更新本地配置；删除仓库文件不能撤销已泄露凭据。

管理上报只有在 `management.enabled` 为布尔 `true` 时才启用。地址使用 `management.cloud_url`，不回退到旧 `sync` 配置。旧云环境的下线和令牌处理见 [轮换清单](../TOKEN_ROTATION_CHECKLIST.md)。历史配置中的令牌仍需在原服务撤销，不能仅删除本地字段。

## 历史环境

旧版手机、微信与云端部署方案已退出当前桌面主流程。维护历史机器时，应单独核实监听端口、服务自启动、远程入口与凭据，不能直接照旧文档重启或覆盖部署。
