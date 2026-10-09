# wallpaper 壁纸插件

给 Vigil 客户端加自定义背景层，支持渐变预设、本地上传、图片URL，可调透明度/遮罩/毛玻璃。

综合 DeepSeek Harness 三款壁纸插件（deepseek-harness-background、dsh-plugin-wallpaper、dsh-wallpaper）的优点重写。

## 功能

- **6 个内置渐变预设**：极光、海洋、日落、森林、夜空、樱花
- **本地上传**：支持 PNG / JPG / GIF / WebP（≤10MB）
- **图片 URL**：粘贴任意 http(s) 图片链接
- **壁纸不透明度**：0-100%，控制壁纸显示强度
- **遮罩不透明度**：0-95%，亮色主题用白色遮罩、暗色主题用黑色遮罩，保证文字可读性
- **面板不透明度**：0-100%，控制卡片/面板的透明程度
- **毛玻璃模糊**：0-40px，给透明面板加 backdrop-filter 模糊效果
- **填充方式**：cover（填充裁剪）/ contain（适应完整）
- **配置持久化**：存在 `~/.vigil/wallpaper/config.json`，重启不丢
- **主题感知**：自动根据亮色/暗色主题切换遮罩颜色

## 安装

通过 Vigil 插件市场安装，或手动把 `wallpaper/` 目录放到 brain 的 `plugins/` 目录下。

安装后**重启客户端**（前端注入代码需要客户端重新加载）。

## 使用

1. 打开客户端，进入「能力与扩展」→「壁纸」
2. 勾选「启用壁纸」
3. 选择壁纸类型：
   - **渐变预设**：点击下方预设色块即可切换
   - **本地上传**：选择本地图片文件，自动上传并应用
   - **图片URL**：粘贴图片链接，自动应用
4. 拖动滑块调整效果（实时预览，松开保存）：
   - 壁纸不透明度：壁纸显示强度
   - 遮罩不透明度：文字可读性遮罩
   - 面板不透明度：卡片透明程度
   - 毛玻璃模糊：透明面板的模糊半径
5. 选择填充方式：填充（裁剪）或适应（完整）

## 文件结构

```
wallpaper/
├── __init__.py    # 后端：配置读写、图片上传、图片服务
├── frontend.js    # 前端注入：背景层渲染、设置页、配置同步
├── frontend.css   # 前端样式：背景层、毛玻璃、设置页
└── README.md      # 说明文档
```

## 后端 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/plugin/wallpaper/config` | 获取配置 + 预设列表 |
| POST | `/api/plugin/wallpaper/config` | 更新配置 |
| POST | `/api/plugin/wallpaper/upload` | 上传图片（base64） |
| GET | `/api/plugin/wallpaper/image?file=xxx` | 获取图片（base64） |

## 配置存储

- 配置文件：`~/.vigil/wallpaper/config.json`
- 图片目录：`~/.vigil/wallpaper/images/`
- 上传新图片时自动删除旧图片，不会堆积

## 版本

- **v2.0.0** - 完全重写：6个渐变预设、本地上传、URL、4个滑块、毛玻璃、主题感知、配置持久化
- v1.0.0 - 初始版本：简单渐变背景
