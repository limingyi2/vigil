# Wallpaper 首帧候选原因：上游引擎升级对比

日期：2026-10-17

## 对照范围

- Vigil PR #8 被测产品提交：`193f297234a36a91ef61a4dedd63d4564df41c87`。
- Vigil vendored DSH host：1.3.1；WebWallGL 2.1.0，快照 commit `4ba71c406fbc78f24c99b1bb1edd9d9cef9793c8`（见 [`UPSTREAM.md`](../../client/vendor/dsh-wallpaper-engine/UPSTREAM.md) 和 [WebWallGL metadata](../../client/vendor/dsh-wallpaper-engine/lib/webwallgl/.upstream.json)）。
- 上游 DSH `main`：`01fbb8b8e66fd7e405e9c878d18dbd456187f111`，插件版本 1.3.3；`package.json` 声明 `webwallgl: ^3.0.0`，lockfile 固定 3.0.0。
- WebWallGL 3.0.0 tag commit：`26acdd7f8630b47376b26cc4f36a9d7de378193d`。

## 对比结果

1. **渲染入口与宿主边界：** 上游当前 renderer wrapper 调用 `createScene()`，暴露 `__wp` 控制面及 `__wpStats.frame = () => inst.stats`。Vigil 自己的 Electron adapter/host 与权限隔离仍是本地实现；上游的 host 代码不是可整体替换的修复。Vigil 内嵌 renderer 页面加载其 `assets/renderer-*.js`，与自定义 adapter 的协议继续由 Vigil 管理。
2. **首帧条件：** WebWallGL 2.1.0 与 3.0.0 的 `renderer/src/api/mount.ts` 都由 `armFirstFrame()` 等待 `rt.onFirstFrame`；scene mount 都在 `renderer.render(...).then(...)` 后触发该回调。3.0.0 在回调前增加编辑器帧捕获与 GL overlay 等工作，但没有发现“零图层时主动 clear/提交首帧”或空场景特殊完成路径。宿主仍会等实际 render Promise 完成。
3. **帧统计与零图层诊断：** 两版都在场景装载后记录 `renderer started: ${scene.layers.length} layers`；没有发现把 `0 layers` 改为首帧就绪、或将 `canvasCandidates=0` 视为成功的差异。3.0.0 的 `stats`/API 增量没有直接改变该首帧条件。
4. WebWallGL 的 frame-0 动画事件修复 `ec67ae9` 已包含在 2.1.0 基线中，且修的是场景动画事件装载，不是 renderer 空图层首帧。

主要源码对照：上游 [renderer wrapper](https://github.com/elysia395/dsh-wallpaper-engine/blob/01fbb8b8e66fd7e405e9c878d18dbd456187f111/lib/webwallgl/renderer.js)、[WebWallGL 2.1.0 mount API](https://github.com/oneincase/webwallgl/blob/2.1.0/renderer/src/api/mount.ts)、[WebWallGL 3.0.0 mount API](https://github.com/oneincase/webwallgl/blob/3.0.0/renderer/src/api/mount.ts)、[2.1.0 scene mount](https://github.com/oneincase/webwallgl/blob/2.1.0/renderer/src/scene-mount.ts)、[3.0.0 scene mount](https://github.com/oneincase/webwallgl/blob/3.0.0/renderer/src/scene-mount.ts)。

## 结论

**没有找到与空场景/零图层首帧超时明确相关的上游修复，因此没有建立实验分支，也没有替换任何引擎文件或运行无针对性的 A/B。** 仅将 2.1.0 升至 3.0.0 会带入大量不相关改动，并且不能据现有源码推断会改变本次失败条件。

P8.2 的同提交对照仍显示 SwiftShader 环境下完整流程会波动，而 Wallpaper-only 可通过；这次源码对比没有把根因归到引擎版本。建议将集成首帧超时保持为已知测试限制，先转向其他功能。只有问题在目标用户硬件上稳定复现，或上游出现可指向空场景首帧的明确提交时，再投入下一轮。

本轮没有运行 Electron A/B、没有改产品代码、没有重建安装包。PR 分支及原始诊断记录保留；工作区未跟踪文件没有纳入修改。
