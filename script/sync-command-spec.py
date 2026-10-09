#!/usr/bin/env python3
"""按装着的那个命令的 --help 改规格，让 check-command-spec.py 对得上。

用法：sync-command-spec.py SPEC.json COMMAND

帮助里有、规格里没有的选项和子命令补进规格（描述、参数和固定候选从帮助里解析，新子命令连
它自己的选项和子命令一起补）；规格里有、帮助里没有的删掉，标了 hidden 的留着。规格文件不
存在时从只有名字的空规格起。改了什么按行打到标准输出，写回的格式和 prettier 一致。
帮助的写法有 commander、clap、yargs 三种，读不出来的（比如叶子命令把上一级的帮助原样
打出来）当没有帮助。
"""
import importlib.util
import json
import os
import re
import subprocess
import sys

_spec = importlib.util.spec_from_file_location("check", os.path.join(os.path.dirname(__file__), "check-command-spec.py"))
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

ROOT = {}
IGNORED = {"-h", "--help"}


def help_text(cmd, path):
    r = subprocess.run([cmd, *path, "--help"], capture_output=True, text=True, timeout=60, cwd="/tmp")
    t = "\n".join(l for l in (r.stdout + r.stderr).splitlines() if not l.startswith("INFO ")) + "\n"
    m = re.search(r"^Usage: (.*)$", t, re.M)
    # 有的叶子命令的 --help 打出的是上一级的帮助；用法行对不上这一级就当没有帮助。
    if path and m and " ".join(path) not in m.group(1):
        return ""
    if path and (len(path) > 6 or t == ROOT.get(cmd)):
        return ""
    return t


def indent(l):
    return len(l) - len(l.lstrip())


def parse_options(text):
    """返回 [(names, argspec, description, possible_values)]"""
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        l = lines[i]
        if re.match(r"^ {0,8}--?[A-Za-z]", l):
            start = indent(l)
            parts = re.split(r"\s{2,}", l.strip(), maxsplit=1)
            col = parts[0]
            desc = [parts[1]] if len(parts) > 1 else []
            j = i + 1
            paras = []
            cur = desc
            while j < len(lines):
                n = lines[j]
                if not n.strip():
                    paras.append(cur)
                    cur = []
                    j += 1
                    continue
                if indent(n) > start and not re.match(r"^ {0,8}--?[A-Za-z]", n):
                    cur.append(n.strip())
                    j += 1
                else:
                    break
            paras.append(cur)
            paras = [p for p in paras if p]
            # clap 风格（说明在下一行、段落用空行隔开）只取第一段；commander 风格全取。
            text_d = " ".join(paras[0]) if paras else ""
            possible = None
            for p in paras:
                m = re.search(r"\[possible values: ([^\]]+)\]", " ".join(p))
                if m:
                    possible = [v.strip() for v in m.group(1).split(",")]
            text_d = re.sub(r"\s*\[possible values: [^\]]+\]", "", text_d).strip()
            cm = re.search(r'\(choices: ((?:"[^"]+"(?:, )?)+)(?:, default: [^)]*)?\)', text_d)
            if cm:
                possible = re.findall(r'"([^"]+)"', cm.group(1))
                text_d = text_d.replace(cm.group(0), "").strip()
            yarg = re.findall(r"\[(boolean|string|number|array|count)\]", text_d)
            ym = re.search(r'\[choices: ((?:"[^"]+"(?:, )?)+)\]', text_d)
            if ym:
                possible = re.findall(r'"([^"]+)"', ym.group(1))
            text_d = re.sub(r"\s*\[(boolean|string|number|array|count|required|deprecated)\]|\s*\[(default|choices|aliases?): [^\]]*\]", "", text_d).strip()
            names = re.findall(r"(?<![\w<\[|-])(--?[A-Za-z][\w-]*)", re.sub(r"[<\[].*", "", col))
            am = re.search(r"([<\[])([^>\]]+)[>\]](\.\.\.)?", col)
            arg = None
            if am:
                arg = {"name": am.group(2).removesuffix("..."), "optional": am.group(1) == "[", "variadic": bool(am.group(3)) or am.group(2).endswith("...")}
            if arg is None and any(t in ("string", "number", "array") for t in yarg):
                arg = {"name": "value", "optional": False, "variadic": False}
            out.append((names, arg, text_d, possible))
            i = j
        else:
            i += 1
    return out


def parse_commands(text, prefix=()):
    """返回 {主名: (别名列表, 描述)}；嵌套的下一层（缩进比名字深、又没到描述那一列）不算。"""
    lines = text.splitlines()
    res = {}
    in_sec = False
    ind = None
    desc_col = None
    last = None
    for l in lines:
        if re.match(r"^\s*(sub)?commands:\s*$", l, re.I):
            in_sec, ind, desc_col, last = True, None, None, None
            continue
        if not in_sec or not l.strip():
            continue
        if indent(l) == 0:
            in_sec = False
            continue
        ind = indent(l) if ind is None else ind
        if indent(l) < ind:
            in_sec = False
            continue
        if indent(l) == ind:
            parts = re.split(r"\s{2,}", l.strip(), maxsplit=1)
            toks = parts[0].split()
            while toks and toks[0] in prefix:
                toks = toks[1:]
            desc = parts[1] if len(parts) > 1 else ""
            if not toks or not re.fullmatch(r"[a-z][\w|-]*", toks[0]):
                last = None
                continue
            if desc_col is None and len(parts) > 1:
                desc_col = l.index(parts[1], indent(l))
            names = toks[0].split("|")
            al = re.findall(r"\[aliases?: ([^\]]+)\]", l)
            extra = [a.strip() for x in al for a in x.split(",")]
            last = names[0]
            res[last] = [names[1:] + extra, desc]
        elif last and desc_col is not None and indent(l) >= desc_col:
            res[last][1] += " " + l.strip()
    for k, v in res.items():
        v[1] = re.sub(r"\s*\[aliases?: [^\]]+\]", "", v[1]).strip()
    return res


def parse_usage_args(text, cmd, path):
    m = re.search(r"^Usage: (.*)$", text, re.M)
    if not m:
        return []
    toks = m.group(1).split()
    toks = toks[len([cmd, *path]):]
    args = []
    for t in toks:
        if re.fullmatch(r"\[?(options|OPTIONS|COMMAND|command)\]?(\.\.\.)?", t) or t in ("[COMMAND]...",):
            continue
        am = re.fullmatch(r"([<\[])([^>\]]+)[>\]](\.\.\.)?", t)
        if am:
            args.append({"name": am.group(2), "optional": am.group(1) == "[", "variadic": bool(am.group(3))})
    return args


def arg_json(a, description=None):
    d = {"name": a["name"]}
    if description:
        d["description"] = description
    if a.get("optional"):
        d["isOptional"] = True
    if a.get("variadic"):
        d["isVariadic"] = True
    n = a["name"].lower()
    if n in ("dir", "directory", "folder") or n.endswith("_dir") or n.endswith("-dir"):
        d["template"] = "folders"
    elif n in ("file", "path", "paths", "files") or n.endswith("_file") or n.endswith("-file") or n.endswith("-path") or n.endswith("_path"):
        d["template"] = "filepaths"
    return d


def first_sentence(s, limit=160):
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > limit:
        cuts = [m.start() for m in re.finditer(r"\. | — | \(|; ", s) if m.start() >= 30]
        if cuts:
            s = s[: cuts[0]]
    return s.rstrip(".;,: ")


def option_json(names, arg, desc, possible):
    d = {"name": names[0] if len(names) == 1 else names}
    if desc:
        d["description"] = first_sentence(desc)
    if arg:
        a = arg_json(arg)
        if possible:
            a["suggestions"] = possible
        d["args"] = a
    return d


def names(v):
    return [v] if isinstance(v, str) else list(v)


def sync(cmd, node, path, log):
    """补这一级缺的选项和子命令。缺什么以 check-command-spec.py 认得的为准，描述、参数从帮助里解析。"""
    text = help_text(cmd, path)
    where = " ".join([cmd, *path])
    have = {n for o in node.get("options", []) for n in names(o["name"])}
    found = check.help_options(text)
    for o in parse_options(text):
        ns = [n for n in o[0] if n in found]
        if not ns or all(n in have or n in IGNORED for n in ns):
            continue
        entry = option_json(*o)
        node.setdefault("options", []).append(entry)
        have |= set(ns)
        log.append(f"+opt {where} {ns}")
    cmds = parse_commands(text, (cmd, *path))
    owner = {a: n for n, (al, _) in cmds.items() for a in al}
    subs = {n for s in node.get("subcommands", []) for n in names(s["name"])}
    args = node.get("args", [])
    suggested = {
        n
        for arg in (args if isinstance(args, list) else [args])
        for sug in arg.get("suggestions", [])
        for n in names(sug if isinstance(sug, str) else sug["name"])
    }
    for n in sorted(check.help_subcommands(text, cmd, path)):
        main = owner.get(n, n)
        if n in subs or main in subs or n in suggested:
            continue
        al, d = cmds.get(main, ([], ""))
        sub = {"name": [main, *al] if al else main, "description": first_sentence(d) or main}
        sub_args = parse_usage_args(help_text(cmd, [*path, main]), cmd, [*path, main])
        if sub_args:
            sub["args"] = [arg_json(a) for a in sub_args] if len(sub_args) > 1 else arg_json(sub_args[0])
        node.setdefault("subcommands", []).append(sub)
        subs |= set(names(sub["name"]))
        log.append(f"+sub {where} {main}")
    for s in node.get("subcommands", []):
        sync(cmd, s, [*path, names(s["name"])[0]], log)


def prune(cmd, node, path, log):
    """规格里有、帮助里没有的选项名和子命令名删掉。帮助读不出东西时不动，免得一次读失败清空整个节点。"""
    text = help_text(cmd, path)
    found_options = check.help_options(text)
    found_subs = check.help_subcommands(text, cmd, path)
    where = " ".join([cmd, *path])
    if found_options or found_subs:
        keep = []
        for o in node.get("options", []):
            ns = names(o["name"])
            if not o.get("hidden"):
                gone = [n for n in ns if n not in found_options and n not in IGNORED]
                if gone:
                    log.append(f"-opt {where} {gone}")
                    ns = [n for n in ns if n not in gone]
            if ns:
                o["name"] = ns[0] if len(ns) == 1 else ns
                keep.append(o)
        if "options" in node:
            node["options"] = keep
        keep = []
        for s in node.get("subcommands", []):
            ns = names(s["name"])
            if not s.get("hidden"):
                gone = [n for n in ns if n not in found_subs]
                if gone:
                    log.append(f"-sub {where} {gone}")
                    ns = [n for n in ns if n not in gone]
            if ns:
                s["name"] = ns[0] if len(ns) == 1 else ns
                keep.append(s)
        if "subcommands" in node:
            node["subcommands"] = keep
    for s in node.get("subcommands", []):
        prune(cmd, s, [*path, names(s["name"])[0]], log)


if __name__ == "__main__":
    spec, cmd = sys.argv[1], sys.argv[2]
    if os.path.exists(spec):
        d = json.load(open(spec))
    else:
        first = (help_text(cmd, []).strip().splitlines() or [cmd])[0]
        d = {"name": cmd, "description": first}
    ROOT[cmd] = help_text(cmd, [])
    log = []
    sync(cmd, d, [], log)
    prune(cmd, d, [], log)
    print("\n".join(log))
    with open(spec, "w") as f:
        f.write(json.dumps(d, indent=4, ensure_ascii=False) + "\n")
