# 运维手册

给"要在这台机器上把它跑起来、跑坏时修好、以及把旧东西彻底关掉"的人看。功能怎么用不在这里，见 [使用与架构手册](USER_GUIDE.md)。

---

## 1. 起停

### 安装版

安装包解压/安装后直接运行 `Kestrel.exe`。它自己会拉 Brain 子进程，不需要你另开终端。关窗口是最小化到托盘，完全退出从托盘菜单选「退出」。

**数据不在安装目录里也能活**：Brain 是冻结程序，`VIGIL_HOME` 未设置时，它的家（`config/` `data/` `logs/`）就在 **Brain exe 所在目录**。升级安装包不会覆盖 `data/`；如果换安装路径，旧数据不会自动跟过去。

### 源码版

```powershell
# 终端 1：Brain
.\.venv\Scripts\python.exe brain\server.py

# 终端 2：界面
cd client; npm start
```

两个进程都要在，界面才连得上。Brain 启动时会打印实际端口。

### 多实例 / 测试

设 `VIGIL_HOME` 指向另一个目录，整个家（配置、数据、日志）就搬过去，互不干扰：

```powershell
$env:VIGIL_HOME = 'D:\vigil-test'
.\.venv\Scripts\python.exe brain\server.py
```

## 2. 出问题先看哪里

| 现象 | 先看 | 说明 |
|---|---|---|
| 界面连不上 Brain | Brain 终端输出 | 启动失败通常会在前 30 行打印原因（端口、缺依赖、配置解析） |
| 聊天报错 / 无响应 | `brain/logs/error_server.jsonl` | 逐行 JSON，含时间、路径、异常摘要 |
| 进程反复重启 | `brain/logs/watchdog.log` | 看门狗记录 |
| 模型调用失败 | 设置页的模型状态 + 上面两个日志 | 先确认 key 和接口地址，再看是不是服务商限流 |
| 工具/插件不生效 | 插件页状态、`/api/plugins` | 安装 ≠ 生效：可能需要重启 Brain |

界面里失败的一轮对话会留下错误块，点开能看到这一步的失败原因；这比翻日志快，先看它。

## 3. 备份

要备份的只有这三个目录（都在 Brain 的家里）：

```
config/    配置与人设（含密钥，注意保管）
data/      记忆、会话、任务、已安装插件、知识库
```

`logs/` 可丢。备份前**先退出客户端**，避免数据库正在写。

```powershell
# 例：整份拷到 D 盘，带日期
$src = "$env:LOCALAPPDATA\Programs\Kestrel\resources\vigil-brain"   # 换成实际位置
$dst = "D:\backup\vigil-$(Get-Date -Format yyyyMMdd-HHmm)"
robocopy $src $dst config data /E /COPYALL
```

恢复：退出客户端 → 把 `config/` `data/` 覆盖回去 → 启动。

**备份里带着密钥**。别扔网盘公开链接，别提交进任何仓库。

## 4. 升级与回滚

- 更新只从 GitHub Releases 拉（`client/electron/public-updates.js`）。自动检查静默进行，有新版会先问；你拒绝过的版本不会反复骚扰。
- 更新是事务式的：下载 → 校验 → 替换 → 校验，失败会回滚（`client/electron/rollback.ps1`、`update-transaction.js`）。
- 手动回滚：装回旧版本安装包即可，`data/` 不会被降级破坏（但**不要**在旧版本里继续用新版本产生的新表结构，必要时用备份回退）。

## 5. 密钥与凭据

### 现在该在哪里

| 凭据 | 位置 | 备注 |
|---|---|---|
| 模型 API Key | `brain/config/config.json`（`api_key` / `vision.api_key` / 各服务商条目） | 文件已被 `.gitignore` 排除 |
| Brain 本机口令 | 同上 `brain_token` | 不配时只允许回环访问，配了才能被局域网/远程设备连 |
| 语音服务 Key | 同上 `voice.aliyun.api_key` | |
| 设备令牌 | `brain/data/devices.json` | 只存哈希 |
| 管理上报令牌 | 同上 `management.token` | 默认关闭（见 §5.3） |

### 5.1 轮换原则

**删除文件不等于作废凭据。** 如果某个密钥曾经进过 git 历史、被同步到别处、或写在会被打包的脚本里，唯一正确的处置是去服务商后台**吊销并重新签发**，然后把旧值从所有副本里删掉。只删文件只是让下一个人少看到一次。

### 5.2 本机配置里的历史遗留

`brain/config/config.json` 里可能还留着 `sync` 段（`enabled` / `cloud_url` / `cloud_token`）。**它已经不被任何代码读取**，但里面的 `cloud_token` 是真实值，且曾经指向一台公网机器——按"已泄露"处理：

1. 先去那台机器（或它背后的服务）把该 token 作废；
2. 再删掉 `sync` 段；
3. 确认 `management` 段没有把同一枚 token 复用。

管理上报令牌的轮换步骤见 [TOKEN_ROTATION_CHECKLIST.md](../TOKEN_ROTATION_CHECKLIST.md)（注意：上报现在默认关闭，重新启用前不需要先轮换，但只要曾经发出去过就仍应轮换）。

### 5.3 管理上报为什么默认关闭

`management.enabled` 必须是**布尔 `true`** 才会发心跳；缺键、`null`、字符串 `"true"` 一律按关闭处理。地址只认 `management.cloud_url`，不会回退到 `sync.cloud_url`。如果你在终端看到「心跳未启用（management.enabled 非 true）」，那是正常的关闭提示，不是故障。

## 6. 旧云下线清单

历史架构里有一台公网机器承担"云端大脑"。桌面端 P8.1 起不再依赖它，但**关机不等于下线**：域名、token、私钥、脚本都还在，任何一样留着都是一条后路。

按顺序做完，才算真正关掉：

- [ ] **停服务**：把该机器上的 Brain 进程停掉并设为不自动启动；确认 8788 / 8790 不再监听。
- [ ] **吊销令牌**：`sync.cloud_token`、`management.token`、以及任何曾在那台机器上配置过的 Brain 口令，全部作废重签。
- [ ] **收回网络入口**：关闭公网端口、撤销 Tailscale 里不再需要的设备、检查有没有残留的反向代理。
- [ ] **删私钥**：`deploy/sshkeys/` 里的私钥（该目录已被 `.gitignore` 排除，不随仓库分发，但仍在本机磁盘上）。确认不再需要登录该机器后删除；删之前先把公钥从目标机的 `authorized_keys` 里移除。
- [ ] **清配置**：删掉本机 `config.json` 的 `sync` 段；确认 `management.enabled` 为 `false` 或整段删除。
- [ ] **清脚本**：`deploy/` 下的云检查脚本已于 P23 从仓库移除。本地若还有旧副本（其他 worktree、备份目录），一并删除——它们硬编码了目标地址和登录方式。
- [ ] **留证据**：把停服时间、吊销的令牌前缀（不要记全值）、执行的机器记进运维日志。

## 7. 常见故障

**Brain 起不来，提示端口被占**
删掉 `brain/data/run/` 下的残留标记再启动。端口是启动时挑的空闲端口，通常不会真冲突；冲突多半是上一个进程没退干净。

**界面一直转圈，日志里没有新东西**
看 Brain 是否真的在跑（终端窗口还在不在），以及客户端是不是连到了旧端口的实例（同时开过多个 Brain 时容易发生）。

**插件装了但不生效**
先看插件页状态。安装成功只代表文件到位并校验通过，运行时加载失败会单独标出来；很多插件需要重启 Brain。

**按链接安装被拒绝**
链接必须指向**带清单**的来源（GitHub 仓库、`tree/<ref>/<插件>`、raw 的 `market.json`）。裸 zip 和单文件直链是刻意拒绝的——没有清单就无法核对版本、权限和哈希。非可信来源会先出审批卡，批准后才装。

**GitHub 最新 release 下不到 exe**
历史出现过只上传 `release-manifest.json` 和 blockmap 的发布。判断方法：打开对应 release 的 assets 列表，必须同时有 `.exe`、`release-manifest.json`、`.contents.json`；三者缺一就是没传完。
