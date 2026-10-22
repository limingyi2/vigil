# Vigil Wallpaper v4

独立的 Vigil Wallpaper Engine 插件。渲染器随插件发布，不依赖 DSH 内核或 Vigil 客户端里的旧 Wallpaper 实现。

## 支持能力

- Wallpaper Engine `Scene`：实时 WebGL 场景渲染，保留粒子、Shader、骨骼/动画、透明背景和高清 DPR 适配（受 Chromium/WebGL 能力限制）。
- `Video`、`Web`、图片和 GIF 壁纸；网页在沙箱 iframe 中运行，并提供 Wallpaper Engine 兼容 shim。
- Vigil 聊天界面的侧栏、消息区、输入框和设置页透明化适配，支持遮罩、毛玻璃、适配方式、静音和帧率控制。
- 资源通过 Vigil 通用短时票据桥读取，卸载时撤销；插件不会删除或改写 Steam Workshop、旧版配置或用户个人壁纸文件。
- 本地图片/视频可在设置页临时打开，使用浏览器对象 URL，不复制或删除源文件。

## 来源与许可

渲染器源自 `elysia395/dsh-wallpaper-engine`，上游提交和 MIT 署名见 [UPSTREAM.md](UPSTREAM.md) 与 [LICENSE](LICENSE)。Vigil 适配代码由 Vigil 项目维护。

## 安装与卸载

从 Vigil 插件市场安装 `wallpaper` 4.0.0。安装后启用“壁纸”页面；卸载仅移除插件包和 v4 配置命名空间，不触碰用户壁纸素材。
