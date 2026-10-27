# Vigil 桌宠（移植自 codex-bailong-desktop-pet）

本目录是 [Wunsch2048/codex-bailong-desktop-pet](https://github.com/Wunsch2048/codex-bailong-desktop-pet)（v1.1.1 基线）`app/` 的移植副本：一只 Windows 桌宠卡片，直接读取本机 Codex 的会话日志与额度，显示任务状态、完成提醒、额度环和 Token 日报。Vigil 客户端通过 `client/electron/pet.js` 把它作为独立进程拉起，替代原 Agent Center 里的 Codex 卡片。

## 运行

```bat
:: 依赖（一次性）
venv\Scripts\python -m pip install -r pet\requirements.txt

:: 由 Vigil 客户端拉起（Agent Center → 桌宠卡），或手动：
venv\Scripts\python pet\app\enhanced.py
```

- 数据与设置在 `%LOCALAPPDATA%\BailongEnhanced`，与本地上游官方安装共用；同一时刻只能运行一个实例（进程内有单实例锁）。
- 测试时设置 `BAILONG_ISOLATED=1` 并隔离 `CODEX_HOME`，避免与正式实例混用。
- **仅卡片模式**：仓库不含人物动画素材（上游 Git 同样不保存）。没有素材时桌宠显示为状态卡片，动画可在桌宠设置里从上游发布渠道单独下载。

## 授权边界

来源与授权见 [NOTICE.md](NOTICE.md)：上游发布者仅确认了其自身仓库的再分发授权，**不自动授予访问者商业使用或再分发许可**；原基线也未提供统一开源许可证。本副本仅用于 Vigil 本地运行与个人使用，若要随 Vigil 安装包公开发布，需先取得上游授权。

## 与 Vigil 的关系

桌宠是独立进程，直读本机 Codex 数据（`CODEX_HOME`），不经过 Vigil Brain。Agent Center 页面的桌宠卡只负责启动/停止进程；Codex CLI 的安装与登录引导保留在页面的连接区。
