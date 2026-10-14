#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静态检查 Vigil 插件开发目录，不安装也不执行插件。"""
import argparse
import json
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))

from plugin_structure import check_plugin_directory  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="插件开发目录")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="输出 JSON 报告")
    args = parser.parse_args(argv)
    report = check_plugin_directory(args.path)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        state = "通过" if report["ok"] else "失败"
        print("[%s] %s" % (state, report["path"]))
        print("布局：%s；入口：%s" % (report["layout"] or "未知",
                                    report["entry"] or "无"))
        for item in report["errors"]:
            print("ERROR %s: %s" % (item["code"], item["message"]))
        for item in report["warnings"]:
            print("WARN  %s: %s" % (item["code"], item["message"]))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
