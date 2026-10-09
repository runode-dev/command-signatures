#!/usr/bin/env python3
"""核对一份命令规格和装着的那个命令的 --help 对不对得上。

用法：check-command-spec.py SPEC.json COMMAND

从顶层起，对规格和帮助里出现的每个子命令跑 `COMMAND 子命令... --help`，比较两边的选项和
子命令，把差异按 Markdown 打到标准输出。有差异时退出码是 1，没有时是 0。

帮助的读法：选项行以 `-` 开头、缩进不超过八格，选项名和说明之间隔着两个以上空格；子命令
从 `Commands:` 段里每项的第一个词认（`a|b` 和 `[aliases: x]` 是别名），也从
`COMMAND 子命令... 名字` 这样的用法行和示例行里认，参数的固定候选也算认得。规格里标了
hidden 的选项和子命令，帮助里没有不算差异。
"""

import json
import re
import subprocess
import sys

# 帮助里不一定列、规格里也不一定写的选项和子命令，不比。
IGNORED = {"-h", "--help"}
IGNORED_SUBCOMMANDS = {"help"}


def names(value):
    return [value] if isinstance(value, str) else list(value)


def spec_node(node):
    """选项和子命令各分成全部的和不隐藏的，再加上参数的固定候选。"""
    options = {n for option in node.get("options", []) for n in names(option["name"])} - IGNORED
    shown_options = {
        n for option in node.get("options", []) if not option.get("hidden") for n in names(option["name"])
    } - IGNORED
    subcommands = {n: sub for sub in node.get("subcommands", []) for n in names(sub["name"])}
    shown_subcommands = {n for n, sub in subcommands.items() if not sub.get("hidden")} - IGNORED_SUBCOMMANDS
    args = node.get("args", [])
    suggestions = {
        n
        for arg in (args if isinstance(args, list) else [args])
        for suggestion in arg.get("suggestions", [])
        for n in names(suggestion if isinstance(suggestion, str) else suggestion["name"])
    }
    return options, shown_options, subcommands, shown_subcommands, suggestions


def all_options(node):
    options = spec_node(node)[0]
    for sub in node.get("subcommands", []):
        options |= all_options(sub)
    return options


def flags(text):
    return set(re.findall(r"(?<![\w-])--?[A-Za-z][\w-]*", text)) - IGNORED


def help_text(command, path):
    run = subprocess.run([command, *path, "--help"], capture_output=True, text=True, timeout=60)
    # kilo 每次启动先打一行带时间和运行 id 的 INFO 日志，会让两次读到的同一份帮助对不上。
    return "\n".join(line for line in (run.stdout + run.stderr).splitlines() if not line.startswith("INFO ")) + "\n"


def help_options(text):
    options = set()
    for line in text.splitlines():
        if re.match(r"^ {0,8}--?[A-Za-z]", line):
            options |= flags(re.split(r"\s{2,}", line.strip())[0])
    return options


def without_examples(text):
    """去掉 `Examples:` 段：示例里命令后面跟的是参数，不是子命令。顶格的示例段到下一个
    `标题:` 为止（示例折行可能折回行首）；缩进的示例段嵌在命令列表里，到同一缩进的下一项为止。"""
    kept = []
    heading = None
    for line in text.splitlines():
        lead = len(line) - len(line.lstrip())
        if (
            heading is not None
            and line.strip()
            and (lead < heading or (lead == heading and (heading > 0 or line.rstrip().endswith(":"))))
        ):
            heading = None
        if heading is None and re.match(r"^\s*examples?:\s*$", line, re.I):
            heading = lead
        if heading is None:
            kept.append(line)
    return "\n".join(kept)


def help_subcommands(text, command, path):
    prefix = re.escape(" ".join([command, *path]))
    subcommands = set(re.findall(rf"^\s+{prefix} ([a-z][\w-]*)(?=\s|$)", without_examples(text), re.M))
    # `Commands:` 段：按第一项的缩进认，缩进更深的是续行或示例，更浅的是段结束了。
    indent = None
    in_section = False
    for line in text.splitlines():
        if re.match(r"^\s*(sub)?commands:\s*$", line, re.I):
            in_section, indent = True, None
            continue
        if not in_section or not line.strip():
            continue
        lead = len(line) - len(line.lstrip())
        indent = lead if indent is None else indent
        if lead < indent:
            in_section = False
            continue
        for aliases in re.findall(r"\[aliases?: ([^\]]+)\]", line):
            subcommands.update(alias.strip() for alias in aliases.split(","))
        first = line.split()[0]
        # pi 在这一段里写的是 `pi install ...` 这样的整行用法，上面已经认过了。
        if lead == indent and first != command:
            subcommands.update(n for n in first.split("|") if re.fullmatch(r"[a-z][\w-]*", n))
    return subcommands - IGNORED_SUBCOMMANDS


def compare(command, node, path, report):
    text = help_text(command, path)
    spec_options, shown_options, spec_subs, shown_subs, suggestions = spec_node(node)
    found_options = help_options(text)
    found_subs = help_subcommands(text, command, path)
    prefix = re.escape(" ".join([command, *path]))

    # 子命令的帮助和这一级一字不差时，帮助分不出选项是哪个子命令的：把它整个并进这一级比，
    # 它用法行上的选项也算这一级帮助里的。别名指着同一个规格，只比一次，用帮助里认得的头一个名字。
    separate = []
    for sub in node.get("subcommands", []):
        name = next((n for n in names(sub["name"]) if n in found_subs), None)
        if name is None:
            continue
        if help_text(command, [*path, name]) == text:
            spec_options |= all_options(sub)
            shown_options |= all_options(sub)
            for usage in re.findall(rf"^\s+{prefix} {re.escape(name)}\b.*$", text, re.M):
                found_options |= flags(usage)
        else:
            separate.append(name)

    where = " ".join([command, *path])
    for label, items in [
        ("帮助里有、规格里没有的选项", found_options - spec_options),
        ("规格里有、帮助里没有的选项", shown_options - found_options),
        ("帮助里有、规格里没有的子命令", found_subs - spec_subs.keys() - suggestions),
        ("规格里有、帮助里没有的子命令", shown_subs - found_subs),
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
