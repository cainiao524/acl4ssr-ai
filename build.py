#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
#  build.py —— 从上游 ini 生成 AI 优化版订阅配置
# =============================================================================
#
#  产物：
#      ACL4SSR_Online_Full_ClaudeAI_MultiMode.ini
#
#  它可以直接填进任何 subconverter 订阅转换站的「远程配置 / 配置文件」输入框，
#  也可以作为 raw URL 被别的配置引用。
#
#  ── 为什么不直接手工改一份 ini 放在仓库里 ────────────────────────────────────
#
#  上游（cainiao524/acl4ssr-steam）是每天自动同步的。手工改的副本有两个问题：
#    1) 上游加新策略组时你收不到；
#    2) 看不出我们到底改了什么 —— diff 里全是上游的变动，我们的 3 行淹没其中。
#
#  所以这里把「我们的改动」写成显式的 patch，由脚本每天重新应用。
#  这样 git diff 永远只有两类：上游的变动，和我们 patch 的那几行。
#
#  ── 我们改了什么 ─────────────────────────────────────────────────────────────
#
#  1) 「💬 Ai平台」策略组：从「会漂移 + 可直连」改成「钉死 / 不泄漏」
#
#     上游原版：
#         custom_proxy_group=💬 Ai平台`select`[]🚀 节点选择`[]♻️ 自动选择`[]...`[]DIRECT
#
#     两个问题：
#         (a) select 组的第一项就是默认选中项，而它是「🚀 节点选择」，
#             该组的第一项又是「♻️ 自动选择」(url-test) —— 于是默认链路是
#                 💬 Ai平台 → 🚀 节点选择 → ♻️ 自动选择(url-test)
#             出口会随健康检查漂移。对 Claude / GPT 账号，IP 漂移是头号封号信号。
#         (b) 组里挂着 []DIRECT —— 节点全部不可用时，AI 流量会静默走真实 IP，
#             而客户端界面看不出任何异常。
#
#     改成：
#         custom_proxy_group=🔒 AI 专用`select`(AI|Claude|GPT|...)
#         custom_proxy_group=💬 Ai平台`select`[]🔒 AI 专用`[]🚀 手动切换
#
#         * 没有「自动选择 / 故障转移 / 负载均衡」→ 不会漂
#         * 没有 DIRECT → 节点全挂时直接失败（fail closed），你会立刻发现
#         * 有专用节点时默认走它；没有时退到「🚀 手动切换」让你手选一个，
#           选完即固定 —— 两条路都不会偷偷换 IP
#
#  2) 补两个「Claude / ChatGPT 深度规则集」
#
#     上游只引用了 ACL4SSR 的 AI.list(47条) 与 OpenAi.list(17条)，
#     Claude 侧靠 DOMAIN-KEYWORD,claude 兜底 —— 兜不住不含 "claude" 字样的
#     clau.de，也兜不住 Anthropic 自有网段。
#
#     补的是 VPSDance/ai-proxy-rules（MIT，每日同步的聚合器：
#     合并 v2fly / blackmatrix7 / xiaolai / net-coffee 多源后按 provider 精修）：
#         anthropic  50 条（含 clau.de / claude.app / MCP / 160.79.104.0/21）
#         openai     52 条
#
#     ⚠️ 必须用 【Surge 格式的 .list】而不是 clash 的 .yaml：
#        clash/*.yaml 是 mihomo rule-provider 风格（`payload:` + `+.x.y`），
#        部分 subconverter 版本对它的支持并不一致；而 surge/*.list 是纯粹的
#        `TYPE,VALUE[,modifier]`，是所有 subconverter 都吃的最大公约数。
#
#  3) 位置：新增的规则集必须紧跟上游那两条 AI 规则集之后
#
#     ini 里规则是【先匹配者胜】。上游「🚀 节点选择」引用的 ProxyGFWlist.list
#     有 7000+ 条，其中就包含：
#         DOMAIN-SUFFIX,anthropic.com
#         DOMAIN-SUFFIX,claude.ai
#         DOMAIN-SUFFIX,chatgpt.com
#         DOMAIN-SUFFIX,openai.com
#     如果把新规则集追加到文件末尾，它会排在 GFWlist 之后 ——
#     于是 claude.ai 先被 GFWlist 命中，走「🚀 节点选择」，而我们钉死的那套
#     永远轮不到。这是最隐蔽的一类失效：配置能加载、AI 能正常用、
#     分流看着也正常，但账号出口根本不是你以为的那台。
#     所以下面用【锚点插入】把它放到 AI.list / OpenAi.list 那一组的紧后面。
#
#  4) 日本节点：Hysteria2 优先，失效时切到 Reality（120 秒健康检查）
#
#  5) Steam 三分流来自底板，保持不变
#
#     🎮 游戏下载(默认DIRECT) / 🎮 Steam 商店/社区(默认节点选择) /
#     🎮 游戏平台(默认DIRECT)
#
#  ── 退出码 ───────────────────────────────────────────────────────────────────
#      0 成功   2 上游结构变了（找不到锚点）   3 拉取失败
# =============================================================================
from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

__version__ = "1.1.0"

UPSTREAM_INI = (
    "https://raw.githubusercontent.com/cainiao524/acl4ssr-steam/main/"
    "ACL4SSR_Online_Full_GameControl_MultiMode.ini"
)
OUTPUT_INI = "ACL4SSR_Online_Full_ClaudeAI_MultiMode.ini"

# 锚点：上游「💬 Ai平台」那一组的最后一条 ruleset（OpenAi.list）
ANCHOR = "ruleset=💬 Ai平台,https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/Ruleset/OpenAi.list"

# 本仓库自带的 subconverter 模板（base/clash-base.yaml）。
# 上游 ini 里那一行 clash_rule_base 默认是【注释掉】的，于是 subconverter 用它
# 自己的内建模板 —— 而内建模板里没有 dns 段。没有 dns 段的后果不只是不安全，
# 还会让分流本身失效：GEOIP,CN 这条兜底要靠真实解析结果判断归属，DNS 配不好
# 就会把国内小站判成境外、落到 FINAL 走代理。详见 base/clash-base.yaml 的注释。
CLASH_RULE_BASE = (
    "https://raw.githubusercontent.com/cainiao524/acl4ssr-ai/main/base/clash-base.yaml"
)

# 新增的 AI 规则集（Surge 格式 .list；jsDelivr 在国内比 raw 更稳，
# 若你的转换器在境外且 jsDelivr 抖动，把 cdn.jsdelivr.net/gh 换成 raw.githubusercontent.com）
AI_RULESETS = [
    "https://cdn.jsdelivr.net/gh/VPSDance/ai-proxy-rules@main/rules/surge/anthropic.list",
    "https://cdn.jsdelivr.net/gh/VPSDance/ai-proxy-rules@main/rules/surge/openai.list",
]

# 节点名正则：命中「专用 / 落地 / 解锁」类节点。改成你自己节点的标识即可。
# 注意这里刻意【不加】`.*` —— 那会匹配全部节点，等于没有钉死。
AI_NODE_FILTER = "(AI|Claude|GPT|OpenAI|专用|专线|Dedicated|落地|解锁)"

# 替换「💬 Ai平台」组定义（上游那一行会被整行替换掉）
AI_GROUP_REPLACEMENT = [
    "custom_proxy_group=💬 Ai平台`select`[]🔒 AI 专用`[]🚀 手动切换",
    "custom_proxy_group=🔒 AI 专用`select`%s" % AI_NODE_FILTER,
]

# 日本同一服务器的 Hysteria2 / XTLS-Reality 主备故障转移。
# subconverter 会按独立正则的出现顺序添加节点：先 Hysteria2，再 Reality。
# 如果节点名称改变，请同步调整以下两个过滤正则。
JAPAN_FALLBACK_GROUP = (
    "custom_proxy_group=🇯🇵 日本节点`fallback`"
    "(?:日本|川日|东京|大阪|泉日|埼玉|沪日|深日|JP|Japan).*(?:[Hh]ysteria2|[Hh][Yy]2)`"
    "(?:日本|川日|东京|大阪|泉日|埼玉|沪日|深日|JP|Japan).*(?:[Rr]eality|REALITY)`"
    "http://www.gstatic.com/generate_204`120,5"
)

BANNER = [
    "; " + "=" * 76,
    "; 本文件由 build.py 自动生成，请勿手改 —— 改动请改 build.py 里的 patch。",
    "; 底板: %s" % UPSTREAM_INI,
    "; patch: 1) 「💬 Ai平台」改为钉死单节点、去掉自动选择与 DIRECT",
    ";        2) 新增 Claude / ChatGPT 深度规则集（锚点插入，防止被 ProxyGFWlist 抢先）",
    ";        3) 日本 Hysteria2 优先，失效时切到 XTLS-Reality",
    "; 生成器版本: v%s" % __version__,
    "; " + "=" * 76,
]


def fetch(url: str, timeout: float = 30.0) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "acl4ssr-claude-build/%s" % __version__,
                 "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8", errors="replace")
    if not payload.strip():
        raise RuntimeError("空响应: %s" % url)
    return payload


def apply_patch(text: str):
    """返回 (新文本, 报告 dict)。结构不符时抛 RuntimeError。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    report = {
        "anchor_found": False,
        "rulesets_inserted": 0,
        "group_replaced": False,
        "group_added": False,
        "input_lines": len(lines),
    }

    # ---- patch 1: 锚点插入新增规则集 ----
    anchor_index = None
    for index, line in enumerate(lines):
        if line.strip() == ANCHOR:
            anchor_index = index          # 取【最后】一个匹配，容忍上游出现重复行
    if anchor_index is None:
        raise RuntimeError(
            "找不到锚点行，上游 ini 结构可能已变。\n  期望: %s\n"
            "  请更新 build.py 里的 ANCHOR 常量。" % ANCHOR
        )
    report["anchor_found"] = True

    inserted = ["ruleset=💬 Ai平台,%s" % url for url in AI_RULESETS]
    lines[anchor_index + 1:anchor_index + 1] = inserted
    report["rulesets_inserted"] = len(inserted)

    # ---- patch 2: 替换「💬 Ai平台」策略组定义 ----
    group_re = re.compile(r"^\s*custom_proxy_group\s*=\s*💬 Ai平台`")
    replaced_at = None
    kept = []
    for index, line in enumerate(lines):
        if group_re.match(line):
            if replaced_at is None:
                replaced_at = len(kept)
                kept.append(None)          # 占位，稍后填新定义
            continue                       # 丢弃上游原行（含重复行）
        kept.append(line)
    if replaced_at is None:
        raise RuntimeError(
            "找不到「💬 Ai平台」策略组定义，上游 ini 结构可能已变。\n"
            "  请检查上游 ini 里是否仍有 custom_proxy_group=💬 Ai平台`... 这一行。"
        )
    kept[replaced_at] = AI_GROUP_REPLACEMENT[0]
    report["group_replaced"] = True

    # ---- patch 3: 追加「🔒 AI 专用」组（放在「💬 Ai平台」之后，便于阅读）----
    # mihomo / subconverter 都允许组在文件任意位置声明，但放在一起最容易看懂。
    kept.insert(replaced_at + 1, AI_GROUP_REPLACEMENT[1])
    report["group_added"] = True

    # ---- patch 4: 激活 clash_rule_base（DNS / sniffer / ipv6）----
    #
    # 上游 ini 里这一行是【注释掉】的：
    #     ;clash_rule_base=https://.../Clash/GeneralClashConfig.yml
    # 而那个被注释的目标本身也是 Clash for Windows 时代的老文件：没有 dns 段、
    # 没有 tun 段、ipv6 还是 true。所以这里整行换成我们自己的 base。
    base_re = re.compile(r"^\s*;?\s*clash_rule_base\s*=")
    for index, line in enumerate(kept):
        if base_re.match(line):
            kept[index] = "clash_rule_base=%s" % CLASH_RULE_BASE
            report["clash_base_activated"] = True
            break
    else:
        # 上游把那行删了 —— 插到 enable_rule_generator 之前（同一区块）
        inserted_at = None
        for index, line in enumerate(kept):
            if re.match(r"^\s*enable_rule_generator\s*=", line):
                inserted_at = index
                break
        if inserted_at is None:
            inserted_at = len(kept)
        kept.insert(inserted_at, "clash_rule_base=%s" % CLASH_RULE_BASE)
        report["clash_base_activated"] = "appended"

    # ---- patch 5: 日本节点从测速自动切换改为健康检查主备切换 ----
    japan_group_re = re.compile(r"^\s*custom_proxy_group\s*=\s*🇯🇵 日本节点`")
    japan_indices = [i for i, line in enumerate(kept) if japan_group_re.match(line)]
    if len(japan_indices) != 1:
        raise RuntimeError(
            "找不到唯一的「🇯🇵 日本节点」策略组（找到 %d 个）；请检查上游更新。"
            % len(japan_indices)
        )
    kept[japan_indices[0]] = JAPAN_FALLBACK_GROUP
    report["japan_fallback_replaced"] = True

    # ---- 加文件头横幅 ----
    out = "\n".join(BANNER + [""] + kept)
    out = out.rstrip("\n") + "\n"
    report["output_lines"] = len(kept) + len(BANNER) + 1
    return out, report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="从上游 ACL4SSR ini 生成 AI 优化版订阅配置（AI 钉死 + Claude 规则集 + Steam 分流）"
    )
    parser.add_argument("--upstream", default=UPSTREAM_INI, help="上游 ini 地址或本地路径")
    parser.add_argument("--output", default=OUTPUT_INI, help="产物路径")
    parser.add_argument("--report", action="store_true", help="额外打印 patch 报告")
    args = parser.parse_args(argv)

    source = Path(args.upstream)
    try:
        if source.is_file():
            text = source.read_text(encoding="utf-8", errors="replace")
            origin = "local:%s" % source
        else:
            text = fetch(args.upstream)
            origin = args.upstream
    except Exception as exc:                      # noqa: BLE001
        sys.stderr.write("[build] ERROR: 拉取上游失败: %s\n" % exc)
        return 3

    try:
        out, report = apply_patch(text)
    except RuntimeError as exc:
        sys.stderr.write("[build] ERROR: %s\n" % exc)
        return 2

    Path(args.output).write_text(out, encoding="utf-8")
    print("BUILD_OK source=%s output=%s lines=%d bytes=%d"
          % (origin, args.output, report["output_lines"], len(out.encode("utf-8"))))
    if args.report:
        for key in sorted(report):
            print("  %-20s %s" % (key, report[key]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
