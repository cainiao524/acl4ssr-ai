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

# 节点名正则：只认带 [SELF] 标识的【自建】节点。
#
# ⚠️ 这里曾经是 "(AI|Claude|GPT|OpenAI|专用|专线|Dedicated|落地|解锁)" ——
#    靠"节点名里有没有 AI 字样"来挑 AI 出口。那个口径在加入机场节点后会崩：
#    机场节点名叫「🇯🇵 日本 IEPL 专线」这种太常见了，于是它们会被吸进 AI 组，
#    而 select 组首位即默认出口 —— AI 出口 IP 悄悄变成机场节点。
#    对 Claude / ChatGPT 账号，IP 漂移是头号封号信号。
#    所以改成按【归属标识】过滤：自建节点名里带 [SELF]，机场节点没有。
#    这样无论机场怎么命名，都进不来。
AI_NODE_FILTER = "[[]SELF[]]"

# 替换「💬 Ai平台」组定义（上游那一行会被整行替换掉）
AI_GROUP_REPLACEMENT = [
    "custom_proxy_group=💬 Ai平台`select`[]🔒 AI 专用`[]🚀 手动切换",
    "custom_proxy_group=🔒 AI 专用`select`%s" % AI_NODE_FILTER,
]

# 自建节点在【转换站】手里会被改名 —— 所以下面所有"钉死自建节点"的组
# 一律用【过滤器】表达，不能用 `[]节点名` 引用。
#
# ⚠️ 真实事故（FlClash 报 proxy group[3]: 日本节点: '🇯🇵 日本 [SELF]
#    xtls-reality' not found）：转换站的 append_type=true 会给节点名插入
#    协议前缀，变成
#        🇯🇵 [VLESS] 日本 [SELF] xtls-reality
#    而改名【只作用于节点名，不作用于组里写死的 `[]名字` 引用】，
#    引用就悬空了；`select` 组解析不到成员 = 整份配置加载失败。
#    `🔒 AI 专用` 用的是过滤器 [[]SELF[]]，所以它一直没事 —— 这就是对照。
#
# 正则的两条硬约束（两条线共用同一份字符串，别改坏）：
#   * 【不能有字面空格】。assets/acl4ssr_build.py 的 normalize_filter_pattern
#     会 re.sub(r"\s+", "", body) 把空格全删掉，写 "日本 [SELF] xtls-reality"
#     会被压成 "日本[SELF]xtls-reality" —— 一个节点都匹配不到、组直接空掉。
#   * 【[SELF] 必须转义】成 \[SELF\]，否则 `[SELF]` 是字符类（匹配 S/E/L/F 之一）。
#   * 用 .* 跨过地区与协议之间可能出现的任何 [TYPE] 前缀 —— 这就是免疫点。
SELF_REALITY_FILTER = r"(\[SELF\].*xtls-reality)"
SELF_HYSTERIA2_FILTER = r"(\[SELF\].*hysteria2)"

# 自建 / 机场两组地区组 —— `✈️ 机场节点` 靠引用它们来表达"机场那一侧"。
#
# ⚠️ 为什么不写 `^(?!.*\[SELF\]).*`（"名字里不含 [SELF]"）：
#    那是【否定前瞻】，而这个转换站（subconverter-ng）不支持。
#    实测过两种写法，结果都是**匹配到 0 个节点**：
#        (^(?!.*\[SELF\]).*)   -> 0
#        (^(?!.*SELF).*)       -> 0   （去掉方括号也一样，排除"转义被吃掉"这种解释）
#    注意 subconverter 官方文档里【写着】前瞻可用（README-cn 里就有
#    `custom_proxy_group=节点选择\`select\`(^(?!.*(美国|日本)).*)`），
#    所以这是这个服务/这个分支的实现差异 —— 不能照文档推断。
#    危险之处在于它【不报错】：静默匹配 0 个，`select` 组退化成 `[DIRECT]`，
#    配置能加载、看着正常，实际上整组废掉。所以 CI 里加了"禁止前瞻"的断言。
#
#    也不要用 `!!GROUPID=1`（"只取第 2 个订阅"）—— 同样实测匹配 0 个，
#    而且它把语义绑在"订阅顺序"上，用户换个顺序就静默指向自建节点。
#
# 退而求其次的可行表达：机场节点都落在这些【地区组】里，而本机的自建节点
# 只叫「日本」，不匹配这些地区的正则 —— 所以这几个组天然只含机场节点。
# 代价：它列的是 5 个子组而不是 46 个平铺节点，且日本不在其中
#（机场的日本节点在「🇯🇵 日本节点」里，和自建节点同组）。
AIRPORT_REGION_GROUPS = (
    "🇭🇰 香港节点", "🇨🇳 台湾节点", "🇸🇬 狮城节点", "🇺🇲 美国节点", "🇰🇷 韩国节点",
)

# 机场的日本节点（正向匹配，不用前瞻）。
#
# `JP` 这种短词做子串匹配容易误伤，所以对过数据：在真实 48 个节点名上，
# (日本|🇯🇵)、(日本|🇯🇵|Japan|JP)、(日本|JP|jp|Japan) 的结果完全一致，
# 都是恰好 20 个（自建 2 + 机场 18），没有多命中一个。
#
# 它和前面两个成员是重叠的（自建两个也含「日本」），但实测转换站会去重：
# 最终 20 个成员、0 重复。所以不需要为了去重去用前瞻——那反而会全废。
JAPAN_REGION_FILTER = r"(日本|🇯🇵|Japan|JP)"

# 「🇯🇵 日本节点」——自建节点的固定出口。
#
# ⚠️ 这里曾经是 `fallback` 主备切换（Hysteria2 优先、失效切 Reality）。
#    那个设计有个副作用：同一个 IP 的两个协议延迟差常年在几十毫秒内浮动，
#    于是健康检查会反复换协议，正在跑的长连接随切换被重建 —— 表现就是
#    "连接方式一直变、不稳定"，而且这正是用户实际反馈的问题。
#    现在改成 select + 两个协议都列出：出口【恒定】为 Reality，
#    需要换协议时在客户端一键切，不用改配置。
#
# ⚠️ 节点名的格式是 <地区> [SELF] <协议>，协议名必须在最后 ——
#    机器侧 sing-box 内核靠"剥掉末尾协议名"反推节点名，顺序错了会造出
#    "… hysteria2 [SELF] hysteria2" 这种脏名字。
#
# ⚠️ 机场的日本节点【也在这个组里】—— 但排在自建两个【之后】。
#    这是有意的：`🇯🇵 日本节点` 不只是 AI 链路上的一环，它是 ACL4SSR 的
#    【地区组】，另外 13 个组（油管/奈飞/国外媒体/漏网之鱼/Steam 商店…）都
#    `[]🇯🇵 日本节点` 引用它。曾经把它写成"只有自建 2 个"，后果是那 13 个组
#    一起失去了"选一个机场日本节点"的能力 —— 组名叫「日本节点」却一个机场
#    日本节点都没有，是名不副实的陷阱。
#
#    为什么加回来不会让出口漂移（这是当初钉死它的理由，必须保住）：
#      * 成员顺序 = 自建 Reality → 自建 Hysteria2 → 机场日本，
#        而 select 组【首位即默认出口】=> 默认永远是自建 Reality；
#      * 它是 `select`，不是 `url-test`/`fallback`/`load-balance`
#        —— 没有任何"自己会换"的机制。
#    即使 subconverter 换成按节点顺序展开，结果也一样：自建节点在订阅里就排在
#    机场前面（输入 A 是 VPS，输入 B 是机场）。两种解释下首位都是自建 Reality。
JAPAN_FALLBACK_GROUP = (
    "custom_proxy_group=🇯🇵 日本节点`select`"
    "%s`%s`%s" % (SELF_REALITY_FILTER, SELF_HYSTERIA2_FILTER, JAPAN_REGION_FILTER)
)

# 自建 / 机场 两个显式入口（手动查看与切换用，【不被任何规则引用】）。
#
# 这条线比自建那条线更需要它们：产物里 2 个自建 + 46 个机场混在同一份订阅里，
# 没有显式入口时只能从 48 项里靠名字认哪台是自己的机器 ——
# 而"选错节点"的代价是 AI 出口 IP 漂到机场，那是账号风险，不是体验问题。
#
# 都是 `select`：不用 url-test/fallback/load-balance，避免"自己会换"。
# 不被规则引用 => 即使其中一个为空，也不影响任何流量的走向。
ISOLATION_GROUPS = (
    "custom_proxy_group=🚀 自建节点`select`%s" % AI_NODE_FILTER,
    "custom_proxy_group=✈️ 机场节点`select`"
    + "`".join("[]" + g for g in AIRPORT_REGION_GROUPS),
)

# 「会自动挑节点」的三个组 —— 必须钉死在自建节点上。
#
# 上游定义是：
#     ♻️ 自动选择   url-test      `.*` + 测速 URL
#     🔯 故障转移   fallback      `.*` + 测速 URL
#     🔮 负载均衡   load-balance  `.*` + 测速 URL
# `.*` = 吃下【全部】节点。加了机场订阅后，这三个组的成员会从 2 个变成几十个，
# 而默认链路 🚀 节点选择 → ♻️ 自动选择，于是默认出口变成"最快的那个节点"——
# 机场通常比自建 VPS 快，所以默认出口会落到机场上。
#
# 对 Claude / ChatGPT 账号，出口 IP 漂到机场是头号封号信号。所以钉死。
#
# ⚠️ 钉的写法是【过滤器】而不是 `[]🇯🇵 日本 [SELF] xtls-reality` 名字引用：
#    转换站的 append_type 会把节点改名成 `🇯🇵 [VLESS] 日本 [SELF] xtls-reality`，
#    名字引用当场悬空、客户端拒绝加载配置（见上面 SELF_REALITY_FILTER 的事故记录）。
#    filter 只匹配【恰好一个】节点（协议名是区分点），所以 select 首位仍然恒定。
PINNED_GROUP_TEMPLATE = (
    "custom_proxy_group={group}`select`%s" % SELF_REALITY_FILTER
)
AUTO_SELECT_GROUPS = ("♻️ 自动选择", "🔯 故障转移", "🔮 负载均衡")

# ── Steam：下载直连、商店/社区走节点 ────────────────────────────────────────
#
# 上游三个 Steam 组里，「🎮 游戏下载」和「🎮 游戏平台」本就是 DIRECT 优先，
# 这里把它们钉死不再变；「🎮 Steam 商店/社区」的「🚀 节点选择」**刻意保留** ——
# store.steampowered.com / steamcommunity.com 在国内基本打不开，不给节点就没法用。
#
# 所以别把商店组改成 DIRECT：那不是"Steam 不碰 VPS"，而是"商店打不开"。
# 真正减少 Steam 占用 VPS 的是【下载】（本来就是 DIRECT）。
#
# ℹ️ 曾经拆出过「🔑 Steam 登录」单独一组（为了单独控制登录出口），后来去掉了：
#    登录域名回落到商店组的 DOMAIN-SUFFIX,steampowered.com，出口与商店一致，
#    行为没有变化，少一个组少一处维护。
STEAM_GROUPS = {
    "🎮 游戏下载": "[]DIRECT`[]🚀 节点选择`[]♻️ 自动选择`[]🔯 故障转移`[]🔮 负载均衡",
    "🎮 游戏平台": "[]DIRECT`[]🚀 节点选择`[]♻️ 自动选择`[]🔯 故障转移`[]🔮 负载均衡",
}

# ── AI 优先关键字：只把那几个被微软规则抢走的域拉回 AI 组 ────────────────────
#
# 问题（实测，不是推测）：OpenAI 有一批资产和实时通道跑在微软/Azure 的主机名上
#     openaiapi-site.azureedge.net
#     openaiassets.blob.core.windows.net / openaicomproductionae4b.blob.core.windows.net
#     production-openaicom-storage.azureedge.net
#     openaipublic.blob.core.windows.net
#     chatgpt-async-webps-prod-*.webpubsub.azure.com   ← ChatGPT 实时/语音信令
# 而微软的规则集（`ruleset=Ⓜ️ 微软服务,…Microsoft.list`，ini 第 32 行）声明在
# AI 规则集（第 35-38 行）【之前】。规则先匹配者胜 => 微软的宽泛后缀
# （azure.com / windows.net / azureedge.net）先把这些域吃掉，落进「Ⓜ️ 微软服务」，
# 而那一组首位是 DIRECT —— 于是 ChatGPT 的文件存储和实时/语音信令走真实 IP，
# 且【没有任何报错】。
#
# 修法：【只】在最前面插一个只含两条关键字的规则集，把那几个域拉回 AI 组。
#     DOMAIN-KEYWORD,openai     ← 覆盖前 5 个（openaicom… 也含 openai）
#     DOMAIN-KEYWORD,chatgpt    ← 覆盖 webpubsub 那个实时通道
# 为什么不用"把「Ⓜ️ 微软服务」首位改成节点"：那也行，但会把
# Windows Update / Office / Teams 这类【大流量】一起拽上 VPS。
# 这里只动那 6 个域，微软其余流量保持直连。
#
# 为什么不干脆把整个 AI 规则集挪到微软之前：那会连带把
# copilot.microsoft.com / sydney.bing.com / ai.azure.com 也拉进 AI 组 ——
# 超出"只修这几个域"的范围，属于另一项决定。
AI_PRIORITY_LIST = (
    "https://raw.githubusercontent.com/cainiao524/acl4ssr-ai/main/base/ai-priority.list"
)

# 插到【第一条微软规则集】之前。用前缀匹配而不是写死整行：
# 上游换 ruleset URL 时不该让这个 patch 失效。
MICROSOFT_RULESET_PREFIX = "ruleset=Ⓜ️ 微软"

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

    # ---- patch 6: 把「会自动挑节点」的三个组钉死在自建节点上 --------------
    #
    # 为什么必须做：默认链路是
    #     🚀 节点选择 → ♻️ 自动选择
    # 而 ♻️/🔯/🔮 三个组在上游是 url-test / fallback / load-balance + filter `.*`，
    # 也就是【从所有节点里挑】。一旦把机场订阅也加进来，它们的成员会变成
    # 几十上百个机场节点 —— 机场通常比自建 VPS 快，于是**默认出口会变成机场节点**。
    #
    # 这和本仓库的核心主张（AI / 账号流量的出口必须恒定且属于自己）直接冲突：
    # 出口 IP 漂到机场，是 Claude / ChatGPT 的头号封号信号。
    #
    # VPS 那条线（assets/local-overlay.ini）早就把这三个组钉死了，
    # 这里补齐，让两条线的默认行为一致 —— 否则会出现
    # "隔离看起来做对了、默认出口其实还是机场" 这种最难发现的缺口。
    #
    # 做法：改成单成员 select。保留组名（别处有引用，删了会悬空），
    # 单成员的效果就是"永远用那一个"，同时不产生漂移。
    # 想用机场时走 🚀 手动切换 —— 那是显式手动选择，不是默认。
    pinned = 0
    for group_name in AUTO_SELECT_GROUPS:
        pat = re.compile(r"^\s*custom_proxy_group\s*=\s*%s`" % re.escape(group_name))
        idxs = [i for i, line in enumerate(kept) if pat.match(line)]
        if not pinned and not idxs:
            raise RuntimeError(
                "找不到「%s」策略组定义，上游 ini 结构可能已变。\n"
                "  这三个组必须在，否则默认出口会落到机场节点上。" % group_name
            )
        for i in idxs:
            kept[i] = PINNED_GROUP_TEMPLATE.format(group=group_name)
            pinned += 1
    report["auto_select_pinned"] = pinned

    # ---- patch 7: Steam 下载钉死 DIRECT ------------------------------------
    #
    #  下载/平台：DIRECT 优先（不占 VPS 流量，也不绕远路）
    #  商店/社区：**不动** —— 上游的「🚀 节点选择」是对的，国内不给节点就打不开。
    #            登录域名（login./api./checkout./help.）落在同一个
    #            DOMAIN-SUFFIX,steampowered.com 上，出口与商店一致，无需单独分组。
    #
    # 缺「游戏下载」时 fail-closed：宁可报错，也不要静默留一份下载走代理的配置。
    steam_replaced = 0
    for group_name, members in STEAM_GROUPS.items():
        pat = re.compile(r"^\s*custom_proxy_group\s*=\s*%s`" % re.escape(group_name))
        idxs = [i for i, line in enumerate(kept) if pat.match(line)]
        if not idxs:
            raise RuntimeError(
                "找不到「%s」策略组定义，上游 ini 结构可能已变。\n"
                "  Steam 下载组必须在，否则下载会默认走代理吃流量。" % group_name
            )
        for i in idxs:
            kept[i] = "custom_proxy_group=%s`select`%s" % (group_name, members)
            steam_replaced += 1

    report["steam_download_direct"] = steam_replaced

    # ---- patch 8: 自建 / 机场 两个显式入口 --------------------------------
    #
    # 插在「🔒 AI 专用」之后：那一块本来就是"哪些节点是自己的"这个语义，
    # 放一起最容易看懂，也离客户端 UI 顶部够近。
    #
    # 两个组都不被任何规则引用 —— 它们是手动入口，缺了不影响分流，
    # 所以这里的失败语义是"报错并要求人工确认"，而不是静默生成一份
    # 用户再也分不清自建/机场的配置。
    existing_group_names = {
        line.split("=", 1)[1].split("`", 1)[0]
        for line in kept if line.startswith("custom_proxy_group=")
    }
    wanted_names = [g.split("=", 1)[1].split("`", 1)[0] for g in ISOLATION_GROUPS]
    isolation_inserted = 0
    if not existing_group_names.intersection(wanted_names):
        anchor_re = re.compile(r"^\s*custom_proxy_group\s*=\s*🔒 AI 专用`")
        anchor_idx = [i for i, line in enumerate(kept) if anchor_re.match(line)]
        if not anchor_idx:
            raise RuntimeError(
                "找不到「🔒 AI 专用」组，无法在其后插入「🚀 自建节点 / ✈️ 机场节点」。\n"
                "  这两个组是手动入口，缺了不影响分流，"
                "但用户就只能从几十项里靠名字认哪台是自己的机器。"
            )
        for offset, group_line in enumerate(ISOLATION_GROUPS):
            kept.insert(anchor_idx[0] + 1 + offset, group_line)
            isolation_inserted += 1
    report["isolation_groups_inserted"] = isolation_inserted

    # ---- patch 9: AI 优先关键字插到微软规则集【之前】 ----------------------
    #
    # 只把落在微软/Azure 主机名上的那几个 OpenAI 域拉回 AI 组，别的都不动
    # （微软其余流量、Copilot/Bing 的行为都保持原样）。
    #
    # 找不到微软规则集时 fail-closed：静默跳过等于留一份
    # "ChatGPT 实时/语音通道直连真实 IP" 的配置，而且不会有任何报错 ——
    # 这正是这个 patch 存在的理由，所以它自己也不能静默失败。
    ms_ruleset_idx = [i for i, line in enumerate(kept)
                      if line.strip().startswith(MICROSOFT_RULESET_PREFIX)]
    if not ms_ruleset_idx:
        raise RuntimeError(
            "找不到以 %r 开头的规则集行，无法把 AI 优先关键字插到它前面。\n"
            "  不插的话，openai/chatgpt 会被微软的 azure.com / windows.net / "
            "azureedge.net 抢先，那批 OpenAI-on-Azure 域会直连真实 IP。"
            % MICROSOFT_RULESET_PREFIX
        )
    priority_line = "ruleset=💬 Ai平台,%s" % AI_PRIORITY_LIST
    if any(line.strip() == priority_line for line in kept):
        report["ai_priority_ruleset"] = "already-present"
    else:
        at = ms_ruleset_idx[0]
        kept.insert(at, priority_line)
        report["ai_priority_ruleset"] = "inserted-before:%s" % kept[at + 1].strip()[:60]

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
