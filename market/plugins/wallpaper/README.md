# Vigil Wallpaper v4

独立的 Vigil Wallpaper Engine 插件。渲染器随插件发布，不依赖 DSH 内核或 Vigil 客户端里的旧 Wallpaper 实现。

## 支持能力

- Wallpaper Engine `Scene`：实时 WebGL 场景渲染，保留粒子、Shader、骨骼/动画、透明背景和高清 DPR 适配（受 Chromium/WebGL 能力限制）。
- `Video`、`Web`、图片和 GIF 壁纸；网页在沙箱 iframe 中运行，并提供 Wallpaper Engine 兼容 shim。
- Vigil 聊天界面的侧栏、消息区、输入框和设置页透明化适配，支持遮罩、毛玻璃、适配方式、静音和帧率控制。
- 资源通过 Vigil 通用短时票据桥读取，卸载时撤销；插件不会删除或改写 Steam Workshop、旧版配置或用户个人壁纸文件。
- 本地图片/视频可在设置页临时打开，使用浏览器对象 URL，不复制或删除源文件。

## 来源与许可

渲染器源自 `elysia395/dsh-wallpaper-engine`，上游提交和 MIT 署名见 [UPSTREAM.md](UPSTREAM.md) 与 [LICENSE](LICENSE.txt)。Vigil 适配代码由 Vigil 项目维护。

## 安装与卸载

从 Vigil 插件市场安装 `wallpaper` 4.2.0。安装后启用“壁纸”页面；卸载仅移除插件包和 v4 配置命名空间，不触碰用户壁纸素材。

## 内置壁纸（4.1.9）

插件自带三张自绘壁纸：**晨雾**（极光帘幕）、**星海**（星空与银河尘带）、**涟漪**（同心光环）。

- 不需要 Wallpaper Engine，不需要 Steam，不需要订阅 Workshop，也不需要联网；
- 三张都是随插件分发的单文件 Canvas 动画（内联 HTML 文档 + data URI 缩略图），
  不落地任何图片资源、不新增权限，30fps 封顶、窗口隐藏自动暂停；
- 装了 Wallpaper Engine 之后，Workshop 内容照旧出现在同一个列表里（内置排在最前）。

## 在线壁纸（4.2）

插件市场安装包硬上限 4 MB，一条视频壁纸就顶穿了，所以动态画面**不随插件打包**。
插件只带一份清单 `cloud-walls.json` 和播放器，画面放在 GitHub Release 上，
用户点卡片时才下载。

- **下载前就能看见样子**：清单里内嵌一张 240 宽 JPEG 缩略图（data URI），
  不需要为"挑哪张"先下几百 MB；
- **下载即校验**：边下边算 SHA-256，并核对字节数与清单声明一致，任何一项不符
  都会整包丢弃、回到"未下载"态，不会留下半截文件；
- **域名白名单**：只认 `github.com` / `release-assets.githubusercontent.com` /
  `objects.githubusercontent.com` / `raw.githubusercontent.com` / `cdn.jsdelivr.net`，
  且重定向后的目标主机也要过白名单（GitHub Release 是两跳）；
- **下载后离线可用**：落在 `%USERPROFILE%\.vigil\wallpaper-v4\cloud\`，
  删除也在这个目录里，不动 Workshop 和用户自己的文件；
- 单张上限 256 MB，落盘前要求磁盘至少留 64 MB 余量。

### 维护者：加一张在线壁纸

1. 把转好的视频传到 GitHub Release（建议独立仓库 `vigil-walls`，tag 如 `walls-v1`）；
2. 生成清单：

```bash
python market/tools/make-cloud-manifest.py \
  --base https://github.com/<user>/vigil-walls/releases/download/walls-v1 \
  --credit "作者署名" --verify \
  _tmp/walls/*.mp4
```

`--verify` 会联网 HEAD 一遍，确认文件真的在线上、大小对得上。脚本自动抽首帧做
缩略图、算 SHA-256，并用插件自己的 `_clean_cloud_entry` 复核清单能不能过关。

⚠️ 换过视频文件就必须重跑脚本：哈希对不上时，用户那边下载会被拒。

