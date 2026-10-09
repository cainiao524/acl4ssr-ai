#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
#  overlay_merge.py —— 把「本地覆盖层」合并到远程 ACL4SSR ini 之上
# =============================================================================
#
#  为什么需要它
#  ------------
#  阶段 5 的 ini 来自上游仓库（cainiao524/acl4ssr-steam），内容会随上游每天变化。
#  有两处必须由我们自己决定、绝不能交给上游：
#
#    1) 「💬 Ai平台」策略组默认链路是
#         💬 Ai平台 → 🚀 节点选择 → ♻️ 自动选择(url-test)
#       出口会在健康检查中变化。对 Claude / GPT 账号，IP 漂移是头号封号信号。
#       而且组里还挂着 []DIRECT —— 节点全挂时会静默走真实 IP。
#
#    2) ACL4SSR 的 AI.list 覆盖不到 Claude 的少数域名与 Anthropic 自有网段。
#
#  直接改上游 ini 不行（每天被覆盖），在客户端改也不行（订阅是生成物）。
#  所以引入覆盖层：上游 ini 作为「底」，本地覆盖层作为「补丁」，合并后才交给
#  acl4ssr_build.py 解析。
#
#  覆盖层语法（就是 ini 语法 + 一条约定）
#  --------------------------------------
#     custom_proxy_group=NAME`...   → 【替换】底里同名的组；底里没有则新增
#     rules=...                     → 【追加】（本管线里 rules= 天然排在所有
#                                     ruleset 之前，见 acl4ssr_build.py
#                                     collect_rules 第 1) 段）
#     ruleset_anchor=<组名>          → 设置锚点：随后的 ruleset= 行会插到底 ini 里
#                                     【最后一个 `ruleset=<组名>,` 行】之后
#     ruleset=...（无锚点）/ 其它键   → 追加到尾部覆盖块
#     ; 或 # 开头、空行              → 注释，忽略
#
#  为什么需要 ruleset_anchor（这是本文件最重要的一条设计）
#  -------------------------------------------------------
#  ruleset 在最终 rules 列表里的顺序 = 它们在 ini 里出现的顺序，而先匹配者胜。
#  底 ini 里「🚀 节点选择」引用的 ProxyGFWlist.list 有 7000+ 条，其中就包含：
#      DOMAIN-SUFFIX,anthropic.com
#      DOMAIN-SUFFIX,claude.ai
#      DOMAIN-SUFFIX,chatgpt.com
#      DOMAIN-SUFFIX,openai.com
#  如果 AI 规则集被简单追加到文件末尾，它们会排在 GFWlist 之后 ——
#  于是 claude.ai 先被 GFWlist 命中，走「🚀 节点选择」（会随 url-test 漂移），
#  而「💬 Ai平台」那套钉死规则永远轮不到。
#  这是最隐蔽的一类失效：配置能加载、AI 能用、分流看着正常，
#  但账号出口根本不是你以为的那台机器。
#  所以 AI 规则集必须插到「💬 Ai平台」原有 ruleset 那一组的位置。
#
#  为什么要「替换」而不是「追加」
#  ------------------------------
#  acl4ssr_build.py 的 parse_ini 对同名组是「先到先得」：
#      if group["name"] in seen_group_names: stats["duplicate_groups"] += 1; continue
#  所以简单追加在末尾的覆盖组会被丢弃，必须先在底里删掉同名行。
#
#  幂等性
#  ------
#  每次都以「远程原始 ini」为底重新合并，输出写到独立文件（*.patched.ini）。
#  重复运行结果一致，且原始 ini 保持原样可供比对。
#
#  退出码
#  ------
#     0  成功
#     2  底文件里没有任何 custom_proxy_group 行（几乎可以断定下载到了错误内容）
#     3  覆盖层里没有任何有效指令
# =============================================================================
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

GROUP_KEY_RE = re.compile(r"^\s*custom_proxy_group(?:s)?\s*=\s*(.*)$", re.I)
BANNER = "; " + "=" * 76

# 覆盖层的插入锚点：插到这一行之前，让覆盖内容仍落在规则/分组区内，
# 而不是飘到文件最尾部（人读起来才知道自己改的东西在哪）。
ANCHOR_RE = re.compile(r"^\s*;?\s*enable_rule_generator\s*=", re.I)


def die(message: str, code: int) -> "None":
    sys.stderr.write("[overlay_merge] ERROR: %s\n" % message)
    raise SystemExit(code)


def read_lines(path: Path) -> list:
    """按行读入并统一行尾。保留空行，行尾换行由写出阶段统一补。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.split("\n")


def is_blank_or_comment(line: str) -> bool:
    stripped = line.strip()
    return (not stripped) or stripped.startswith(";") or stripped.startswith("#")


def group_name_of(line: str):
    """若该行是 custom_proxy_group，返回组名（第一个反引号之前）；否则 None。

    只认反引号切分，不按位置猜 —— 与 acl4ssr_build.py 的 split_fields 保持一致。
    """
    match = GROUP_KEY_RE.match(line)
    if not match:
        return None
    name = match.group(1).split("`")[0].strip()
    return name or None


def merge(base_lines: list, overlay_lines: list) -> tuple:
    report = {
        "replaced_groups": [],
        "added_groups": [],
        "appended_keys": {},
        "anchors": {},
        "anchored_rulesets": 0,
        "base_group_count": 0,
    }

    overlay_groups = []   # [(name, line)]，保持覆盖层里的顺序
    overlay_extras = []   # rules= / ruleset= 等
    for line in overlay_lines:
        if is_blank_or_comment(line):
            continue
        name = group_name_of(line)
        if name is not None:
            overlay_groups.append((name, line))
        else:
            overlay_extras.append(line)

    if not overlay_groups and not overlay_extras:
        die("覆盖层里没有任何有效指令（只有注释/空行）", 3)

    overlay_by_name = {name: line for name, line in overlay_groups}

    out = []
    placed = set()
    for line in base_lines:
        name = group_name_of(line)
        if name is None:
            out.append(line)
            continue
        report["base_group_count"] += 1
        replacement = overlay_by_name.get(name)
        if replacement is None:
            out.append(line)
            continue
        # 同名组：丢弃底里的原行，在原位置放覆盖版本（只放一次）
        if name not in placed:
            out.append(replacement)
            placed.add(name)
            report["replaced_groups"].append(name)

    # 覆盖层里新增的组（底里没有的）
    new_group_lines = []
    for name, line in overlay_groups:
        if name in placed:
            continue
        new_group_lines.append(line)
        report["added_groups"].append(name)

    # ---- ruleset 分两拨：带锚点的插到锚点后，其余进尾部覆盖块 ----
    #
    # 为什么需要锚点：ruleset 在最终 rules 列表里的顺序 = 它们在 ini 里出现的顺序。
    # 底 ini 里「🚀 节点选择」引用的 ProxyGFWlist.list 有 7000+ 条，其中就包含
    #     DOMAIN-SUFFIX,anthropic.com / claude.ai / chatgpt.com / openai.com
    # 如果把这些 AI 规则集简单追加到文件末尾，它们会排在 GFWlist 之后 ——
    # 于是 claude.ai 先被 GFWlist 命中，走「🚀 节点选择」（可能漂到任意节点），
    # 而「💬 Ai平台」那套钉死规则永远轮不到。这是最隐蔽的一类失效：
    # 配置能加载、AI 能用、分流看着正常，但账号出口根本不是你以为的那台。
    #
    # 所以覆盖层用 `ruleset_anchor=<组名>` 指定：随后的 ruleset= 行要插到
    # 底 ini 里【最后一个 `ruleset=<组名>,` 行】之后。
    anchored_rulesets = {}   # anchor_group -> [line, ...]
    tail_extras = []
    current_anchor = ""
    for line in overlay_extras:
        key, _, _value = line.partition("=")
        key_l = key.strip().lower()
        if key_l == "ruleset_anchor":
            current_anchor = line.split("=", 1)[1].strip()
            report["anchors"][current_anchor] = report["anchors"].get(current_anchor, 0)
            continue
        if key_l == "ruleset" and current_anchor:
            anchored_rulesets.setdefault(current_anchor, []).append(line)
            report["anchored_rulesets"] += 1
            continue
        tail_extras.append(line)

    # 尾部覆盖块（新增组 + 非锚点指令）
    extra_block = []
    if new_group_lines or tail_extras:
        extra_block.append("")
        extra_block.append(BANNER)
        extra_block.append("; 本地覆盖层（assets/local-overlay.ini）—— 由 overlay_merge.py 合并")
        extra_block.append("; 下面的 custom_proxy_group 是覆盖层【新增】的组；")
        extra_block.append("; 同名组已在上面原位替换。rules= 行在本管线中天然优先于所有 ruleset。")
        extra_block.append(BANNER)
    for line in new_group_lines:
        extra_block.append(line)
    if new_group_lines and tail_extras:
        extra_block.append("")
    for line in tail_extras:
        extra_block.append(line)
        key = line.split("=", 1)[0].strip().lower()
        report["appended_keys"][key] = report["appended_keys"].get(key, 0) + 1

    # ---- 先放锚点 ruleset（就地插入，保持顺序）----
    # 从后往前插，避免索引位移。
    unresolved = {}
    for anchor_group, lines in anchored_rulesets.items():
        marker = "ruleset=%s," % anchor_group
        last = None
        for index, line in enumerate(out):
            if line.strip().startswith(marker):
                last = index
        if last is None:
            # 锚点组不在底 ini 里（上游改名？）—— 退回【最前面】而不是末尾。
            # 插到最前 = 优先级最高，最坏情况是抢了广告拦截的位置（AI 域名不在
            # 广告列表里，无实际影响）；插到末尾则等于这批规则全部废掉。
            report["anchors"][anchor_group] = -1
            unresolved[anchor_group] = lines
            continue
        report["anchors"][anchor_group] = last + 1
        out[last + 1:last + 1] = lines

    if unresolved:
        # 锚点失效时插到第一条 ruleset 之前
        first = None
        for index, line in enumerate(out):
            if line.strip().startswith("ruleset="):
                first = index
                break
        block = []
        for _group, lines in unresolved.items():
            block.extend(lines)
        if first is None:
            out.extend(block)
        else:
            out[first:first] = block

    # ---- 再放尾部覆盖块 ----
    if extra_block:
        anchor = None
        for index, line in enumerate(out):
            if ANCHOR_RE.match(line):
                anchor = index
                break
        if anchor is None:
            out.extend(extra_block)
        else:
            out[anchor:anchor] = extra_block

    return out, report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="把本地覆盖层合并到远程 ACL4SSR ini（替换同名组 + 追加 rules=）"
    )
    parser.add_argument("--base", required=True, help="远程下载的原始 ini")
    parser.add_argument("--overlay", required=True, help="本地覆盖层 ini")
    parser.add_argument("--output", required=True, help="合并结果写出路径")
    parser.add_argument("--report", default="", help="可选：把合并报告写成 JSON")
    args = parser.parse_args(argv)

    base_path = Path(args.base)
    overlay_path = Path(args.overlay)
    if not base_path.is_file():
        die("底文件不存在: %s" % base_path, 1)
    if not overlay_path.is_file():
        die("覆盖层不存在: %s" % overlay_path, 1)

    merged, report = merge(read_lines(base_path), read_lines(overlay_path))

    if report["base_group_count"] == 0:
        die("底文件里没有任何 custom_proxy_group 行 —— 大概率下载到了错误内容", 2)

    # 写出：去掉尾部多余空行，统一以单个换行结尾
    text = "\n".join(merged).rstrip("\n") + "\n"
    Path(args.output).write_text(text, encoding="utf-8")

    report["output"] = str(args.output)
    report["output_bytes"] = len(text.encode("utf-8"))
    report["output_lines"] = text.count("\n")

    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(
        "OVERLAY_OK replaced=%d added=%d appended=%d base_groups=%d out=%s"
        % (
            len(report["replaced_groups"]),
            len(report["added_groups"]),
            sum(report["appended_keys"].values()),
            report["base_group_count"],
            args.output,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
