# Vigil 最小插件模板

这个目录是一个可以被当前 `plugin_host` 加载的单文件插件模板。

先运行结构检查：

```bash
python tools/check_plugin_structure.py templates/plugin-basic
```

分发时保持扁平结构，只保留一个 `.py`，并可附带 `zxplugin.json`、`README.md`
或 `.txt`。检查器只读文件和 Manifest，不安装、不导入、不执行插件。
