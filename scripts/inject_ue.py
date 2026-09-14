#!/usr/bin/env python3
"""把 engine/ue_model.js 注入到各页面的标记区。

页面是自包含单文件，但测算引擎只有一份源。谁要用引擎，就在页面里留一段
    <script id="ue-model">/*UE_MODEL_START*/ … /*UE_MODEL_END*/</script>
本脚本把两个标记之间的内容整段换成引擎当前内容。

改公式只改 engine/ue_model.js，改完跑一次这个脚本。
绝不在页面里手改引擎 —— 那会让三页各自长出一个版本，而差异要到某个数对不上时才暴露。

用法
    python3 scripts/inject_ue.py            # 注入全部页面
    python3 scripts/inject_ue.py --check    # 只检查是否一致，不一致非零退出
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "engine", "ue_model.js")
TARGETS = ["site/sandbox/index.html", "site/unit-econ/index.html", "site/op-planner/index.html"]
START, END = "/*UE_MODEL_START*/", "/*UE_MODEL_END*/"


def main():
    check = "--check" in sys.argv
    with open(ENGINE, encoding="utf-8") as f:
        src = f.read().strip()
    if START in src or END in src:
        print("! 引擎源文件里出现了标记串本身，注入会自我截断")
        return 2

    bad = 0
    for rel in TARGETS:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            print("· 跳过（文件不存在）%s" % rel)
            continue
        with open(path, encoding="utf-8") as f:
            html = f.read()
        pat = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
        if not pat.search(html):
            print("! %s 里找不到标记区，跳过" % rel)
            bad += 1
            continue
        new = pat.sub(lambda _m: START + "\n" + src + "\n" + END, html, count=1)
        if new == html:
            print("= %s 已是最新" % rel)
            continue
        if check:
            print("! %s 与引擎源不一致" % rel)
            bad += 1
            continue
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        print("✓ 已注入 %s" % rel)
    if check and not bad:
        print("三页内联引擎与源一致")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
