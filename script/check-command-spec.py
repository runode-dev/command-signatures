#!/usr/bin/env python3
"""核对一份命令规格和装着的那个命令的 --help 对不对得上。

用法：check-command-spec.py SPEC.json COMMAND

从顶层起，对规格和帮助里出现的每个子命令跑 `COMMAND 子命令... --help`，比较两边的选项和
子命令，把差异按 Markdown 打到标准输出。有差异时退出码是 1，没有时是 0。

帮助的读法按 pi 的写法：选项行以 `-` 开头，选项名和说明之间隔着两个以上空格；子命令从
`COMMAND 子命令... 名字` 这样的用法行和示例行里认，参数的固定候选也算认得。换别的命令时
先看它的帮助是不是这么写的。
"""

import json
import re
import subprocess
import sys

# 帮助里不一定列、规格里也不一定写的选项，不比。
IGNORED = {"-h", "--help"}


def names(value):
    return [value] if isinstance(value, str) else list(value)


def spec_node(node):
    options = {n for option in node.get("options", []) for n in names(option["name"])} - IGNORED
    subcommands = {n: sub for sub in node.get("subcommands", []) for n in names(sub["name"])}
    args = node.get("args", [])
    suggestions = {
        n
        for arg in (args if isinstance(args, list) else [args])
        for suggestion in arg.get("suggestions", [])
        for n in names(suggestion if isinstance(suggestion, str) else suggestion["name"])
    }
    return options, subcommands, suggestions


def all_options(node):
    options = spec_node(node)[0]
    for sub in node.get("subcommands", []):
        options |= all_options(sub)
    return options


def flags(text):
    return set(re.findall(r"(?<![\w-])--?[A-Za-z][\w-]*", text)) - IGNORED


def help_text(command, path):
    run = subprocess.run([command, *path, "--help"], capture_output=True, text=True, timeout=60)
    return run.stdout + run.stderr


def compare(command, node, path, report):
    text = help_text(command, path)
    spec_options, spec_subs, suggestions = spec_node(node)
    # 选项行以 `-` 开头，选项名和说明之间隔着两个以上空格。
    help_options = set()
    for line in text.splitlines():
        if line.strip().startswith("-"):
            help_options |= flags(re.split(r"\s{2,}", line.strip())[0])
    prefix = re.escape(" ".join([command, *path]))
    help_subs = set(re.findall(rf"^\s+{prefix} ([a-z][\w-]*)(?=\s|$)", text, re.M))

    # 子命令的帮助和这一级一字不差时，帮助分不出选项是哪个子命令的：把它整个并进这一级比，
    # 它用法行上的选项也算这一级帮助里的。
    separate = []
    for name in sorted(spec_subs.keys() & help_subs):
        if help_text(command, [*path, name]) == text:
            spec_options |= all_options(spec_subs[name])
            for usage in re.findall(rf"^\s+{prefix} {re.escape(name)}\b.*$", text, re.M):
                help_options |= flags(usage)
        else:
            separate.append(name)

    where = " ".join([command, *path])
    for label, items in [
        ("帮助里有、规格里没有的选项", help_options - spec_options),
        ("规格里有、帮助里没有的选项", spec_options - help_options),
        ("帮助里有、规格里没有的子命令", help_subs - spec_subs.keys() - suggestions),
        ("规格里有、帮助里没有的子命令", spec_subs.keys() - help_subs),
    ]:
        if items:
            report.append(f"- `{where}` {label}：" + "、".join(f"`{i}`" for i in sorted(items)))
    for name in separate:
        compare(command, spec_subs[name], [*path, name], report)


def main():
    spec_path, command = sys.argv[1:]
    with open(spec_path) as file:
        spec = json.load(file)
    report = []
    compare(command, spec, [], report)
    print("\n".join(report))
    sys.exit(1 if report else 0)


if __name__ == "__main__":
    main()
