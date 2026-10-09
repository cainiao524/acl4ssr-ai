#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
#  sb-oneclick-new · ACL4SSR 游戏分流增强订阅生成器（阶段 5 资产）
#  ini (subconverter 专用格式)  ->  mihomo 可直接加载的「全内联」完整配置
# -----------------------------------------------------------------------------
#  为什么需要这个编译器：
#    cainiao524/acl4ssr-steam 的 ACL4SSR_Online_Full_GameControl_MultiMode.ini
#    是 subconverter 专用格式：Clash / mihomo / sing-box 都读不了。
#    本编译器把它在本地编译成一份自包含 YAML：
#      * 规则全部内联（rules 段直接写规则，不用 rule-provider 在线依赖）
#      * 节点全部内联（proxies 段来自 /etc/sing-box/subscribe/proxies）
#      => 客户端只需要这一份文件，不依赖 subconverter、不依赖任何在线规则服务。
#
#  ---------------------------------------------------------------------------
#  【三大实测天坑 —— 漏一个 mihomo 就起不来，本文件强制处理】
#  ---------------------------------------------------------------------------
#   坑 1  URL-REGEX 是旧版 Clash 专有规则类型，mihomo v1.19.x 完全不认识，
#         命中即 "unsupported rule type" 解析期报错 -> 必须按【类型白名单】
#         过滤（见 SUPPORTED_RULE_TYPES / filter_rule_type()）。被丢弃的类型
#         逐个计数，最终写进自检统计（dropped_rule_types）。
#   坑 2  global-client-fingerprint 已被 mihomo v1.19.31 移除，任何残留都会
#         在启动时打 error 级日志 -> 本编译器从头到尾不生成该键，并在自检里
#         对生成产物做一次反向断言（assert_no_banned_keys()）。
#   坑 3  IP-CIDR 的三段式写法  IP-CIDR,x,no-resolve  里 no-resolve 是【修饰
#         符】不是【策略】。mihomo 会把第三段当成策略名去找策略组，找不到就
#         报错；正确写法是四段式 IP-CIDR,x,<策略组>,no-resolve（策略插到修
#         饰符前面）。处理见 normalize_rule()。
#
#  ---------------------------------------------------------------------------
#  【DNS 顶配与「严禁项」—— 直接决定客户端卡不卡】
#  ---------------------------------------------------------------------------
#    dns.enable = true（注意：是 enable，不是 enabled；拼错整段 DNS 静默失效）
#    enhanced-mode = fake-ip、fake-ip-range = 198.18.0.1/16、respect-rules = true
#    nameserver / proxy-server-nameserver = 国内 DoH
#    fallback = 境外【加密 DoH】(https://...)，fallback-filter = geoip CN
#
#    【严禁】把 dns.fallback 写成 tls://8.8.4.4 或 tls://1.1.1.1 这类直连 DoT。
#    原因（实测，不是理论洁癖）：
#      1) respect-rules 为 false 时，这些 DoT 服务器【不走代理】，由 mihomo 从
#         物理网卡直连出去；国内网络下 853 端口的明文直连 DoT 必然被墙/被丢包，
#         于是所有命中 fallback 的域名每次【新建连接】都要干等约 5 秒超时，
#         首屏/冷启动体验直接崩掉。
#      2) 顺带把「本机正在向境外 DNS 发加密查询」这个元数据暴露给本地 ISP，
#         等于免费送了一份可用性画像（时间/频率/目标）。
#      => 本编译器只允许 https:// 形式的 DoH 进入 fallback，并用
#         assert_no_banned_keys() 断言产物中不存在 "tls://" 字样。
#
#  ---------------------------------------------------------------------------
#  【本机可单测的三种模式】
#  ---------------------------------------------------------------------------
#    parse : 只解析 ini -> parsed.json（+ stats.json），不联网、不写 etc 路径
#    fetch : 按 parsed.json 把每个 ruleset 下载到 rule<NN>.list（幂等复用）
#    build : 读取 parsed.json + 已下载的 rule<NN>.list + proxies 文件 -> YAML
#
#  例（冒烟测试，全部落在临时目录）：
#    python3 acl4ssr_build.py parse --ini /tmp/a.ini --parsed-json /tmp/p.json
#    python3 acl4ssr_build.py build --parsed-json /tmp/p.json \
#        --rules-dir /tmp/rules --proxies /tmp/proxies --output /tmp/out.yaml
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import warnings
from pathlib import Path

__version__ = "2.0.0"

# ---------------------------------------------------------------------------
# 统一接口契约的默认值（全部可被命令行参数覆盖，方便本机冒烟测试）
# ---------------------------------------------------------------------------
DEF_WORKDIR = "/root/sb-oneclick"
DEF_ASSETS_DIR = "/root/sb-oneclick/assets"
DEF_INI_PATH = "/root/acl4ssr-gamecontrol.ini"
DEF_PARSED_JSON = "/root/acl4ssr-parsed.json"
DEF_STATS_JSON = "/root/acl4ssr-stats.json"
DEF_RULE_DIR = "/etc/sing-box/subscribe/acl4ssr"
DEF_OUTPUT = "/etc/sing-box/subscribe/acl4ssr-game.yaml"
DEF_PROXIES = "/etc/sing-box/subscribe/proxies"
DEF_CONF_DIR = "/etc/sing-box/conf"
DEF_SUB_PORT = 56808
DEF_TRAFFIC_PORT = 56809
DEF_INI_URL = (
    "https://raw.githubusercontent.com/cainiao524/acl4ssr-steam/master/"
    "ACL4SSR_Online_Full_GameControl_MultiMode.ini"
)

USER_AGENT = "sb-oneclick-acl4ssr-builder/%s (+mihomo-inline-compiler)" % __version__

# ---------------------------------------------------------------------------
# 坑 1：mihomo v1.19.x 实测支持的规则类型【白名单】
# 只有出现在这里的类型才会被写进产物；其余（URL-REGEX / USER-AGENT / SCRIPT /
# AND,xxx 之类的旧式写法）一律丢弃并计数。
# ---------------------------------------------------------------------------
SUPPORTED_RULE_TYPES = frozenset(
    {
        "DOMAIN",
        "DOMAIN-SUFFIX",
        "DOMAIN-KEYWORD",
        "DOMAIN-REGEX",
        "GEOSITE",
        "GEOIP",
        "SRC-GEOIP",
        # 【刻意不含 IP-ASN】—— 技术上 mihomo 支持，但代价大于收益，见 EXCLUDED_RULE_TYPES。
        "IP-CIDR",
        "IP-CIDR6",
        "IP-SUFFIX",
        "SRC-IP-CIDR",
        "SRC-IP-CIDR6",
        "SRC-PORT",
        "DST-PORT",
        "IN-PORT",
        "IN-TYPE",
        "IN-USER",
        "IN-NAME",
        "PROCESS-PATH",
        "PROCESS-PATH-REGEX",
        "PROCESS-NAME",
        "PROCESS-NAME-REGEX",
        "UID",
        "NETWORK",
        "DSCP",
        "RULE-SET",
        "AND",
        "OR",
        "NOT",
        "SUB-RULE",
        "MATCH",
    }
)

# 已知的「旧版 Clash / sing-box 专有」反面清单：仅用于日志里点名批评，
# 真正过滤依据是上面的白名单（白名单比黑名单安全：新类型默认丢弃）。
KNOWN_LEGACY_TYPES = {
    "URL-REGEX": "旧版 Clash 专有（mihomo 不支持）",
    "USER-AGENT": "旧版 Clash 专有（mihomo 不支持）",
    "SCRIPT": "旧版 Clash 专有（mihomo 不支持）",
    "FINAL": "旧写法，应写成 MATCH",
    "HOST": "旧写法，应写成 DOMAIN",
    "HOST-SUFFIX": "旧写法，应写成 DOMAIN-SUFFIX",
    "HOST-KEYWORD": "旧写法，应写成 DOMAIN-KEYWORD",
    "HOST-WILDCARD": "旧写法，mihomo 不支持",
    "PROCESS-NAME-REGEXP": "旧写法，应写成 PROCESS-NAME-REGEX",
    "DOMAIN-SET": "mihomo 不支持",
    "CLASH-MODE": "非规则类型",
    "DEST-PORT": "旧写法，应写成 DST-PORT",
}

# 「技术上支持、但代价大于收益」而刻意排除的规则类型。
# 与 KNOWN_LEGACY_TYPES 的区别：那些是「写了也没用（mihomo 不认）」，
# 这些是「mihomo 认，但我们主动不要」—— 报错时不该把它们说成"旧写法"。
EXCLUDED_RULE_TYPES = {
    "IP-ASN": (
        "需要额外的 ASN.mmdb（约 12 MB）：实测首次加载会从 GitHub 下载它，"
        "加载耗时 34ms -> 1319ms；下载失败则【整份配置起不来】。"
        "而收益为零 —— Anthropic(AS399358) 广播的 IPv4 只有 160.79.104.0/23，"
        "已被规则集里的 IP-CIDR,160.79.104.0/21 完全覆盖；"
        "OpenAI(AS401518) 广播的 IPv4 只有 199.47.142.0/23，本身就是规则集里的那条 IP-CIDR。"
        "两个 ASN 都是纯冗余，不值得为它引入一个会拖垮加载的依赖。"
    ),
}

# 规则里可以出现的「修饰符」（必须排在策略之后）
RULE_MODIFIERS = frozenset({"no-resolve", "src"})

# 内建策略（不需要在 proxy-groups 里定义）
BUILTIN_POLICIES = frozenset({"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"})

# 策略组类型（subconverter custom_proxy_group 第二段）
VALID_GROUP_TYPES = frozenset({"select", "url-test", "fallback", "load-balance", "relay"})

# 可被本编译器原样内联进 mihomo proxies 的协议白名单
SUPPORTED_PROXY_TYPES = frozenset(
    {
        "ss",
        "ss2022",
        "ssr",
        "vmess",
        "vless",
        "trojan",
        "hysteria",
        "hysteria2",
        "tuic",
        "snell",
        "anytls",
        "http",
        "socks5",
        "wireguard",
        "mieru",
        "ssh",
    }
)

# 每个协议允许写入 YAML 的键（mihomo 严格解析：多一个不认识的键就报错）
ALLOWED_PROXY_KEYS = {
    "_common": {
        "name",
        "type",
        "server",
        "port",
        "udp",
        "tfo",
        "mptcp",
        "ip-version",
        "interface-name",
        "routing-mark",
        "dialer-proxy",
        "smux",
        "client-fingerprint",
        "skip-cert-verify",
        "servername",
        "sni",
        "tls",
        "alpn",
        "network",
        "ws-opts",
        "grpc-opts",
        "h2-opts",
        "http-opts",
        "reality-opts",
        "fingerprint",
        "username",
        "password",
        "ports",
        "up",
        "down",
        "obfs",
        "obfs-password",
        "auth-str",
        "up-mbps",
        "down-mbps",
        "congestion-controller",
        "udp-relay-mode",
        "reduce-rtt",
        "heartbeat-interval",
        "disable-sni",
        "protocol",
        "protocol-param",
        "obfs-param",
        "cipher",
        "uuid",
        "alterId",
        "flow",
        "encryption",
        "packet-encoding",
        "global-padding",
        "authenticated-length",
        "private-key",
        "public-key",
        "pre-shared-key",
        "peers",
        "reserved",
        "allowed-ips",
        "mtu",
        "dns",
        "ip",
        "ipv6",
        "remote-dns-resolve",
        "ecn",
        "token",
        "ports-range",
    },
}

# ---------------------------------------------------------------------------
# 国内 DoH（用于 nameserver / proxy-server-nameserver）
# ---------------------------------------------------------------------------
DOMESTIC_DOH = [
    "https://doh.pub/dns-query",
    "https://dns.alidns.com/dns-query",
]
# 境外【加密 DoH】（fallback 专用）。严禁 tls:// 直连 DoT —— 原因见文件头注释。
# respect-rules 为 false 时直连 DoT 从物理网卡出去，国内必被墙，
# 每次新建连接干等约 5 秒，且把「本机向境外 DNS 发加密查询」暴露给本地 ISP。
OVERSEAS_DOH = [
    "https://1.1.1.1/dns-query",
    "https://8.8.8.8/dns-query",
]

# ---------------------------------------------------------------------------
# fake-ip-filter：这些域名【不做】fake-ip，保持真实解析
# ---------------------------------------------------------------------------
# 为什么重要：fake-ip 的好处是让域名规则在"目标已经是 IP"时仍能命中；
# 但副作用是【任何不认 fake-ip 的程序都会坏】—— 典型的就是靠 UDP 打洞或
# 直接对 IP 建立连接的场景：游戏联机、P2P、NTP 对时、推送。
# 早期只有 7 条（lan/local/msftncsi/time/stun），实测不够：
#   * 少了 stun.*.*.*（三层通配）→ 部分联机打洞失败
#   * 少了主机平台域名（Nintendo / PlayStation / Xbox / Battle.net）→ 联机失败
#   * 少了 ntp.* 与 dns.msftncsi.com → NTP 与联网状态检测可能异常
# 这里全部用【通配域名】而不是 geosite:/rule-set: 前缀 ——
# 后者会额外依赖 geodata 或 rule-provider，与本管线的「全内联、零在线依赖」冲突。
FAKE_IP_FILTER = [
    "+.lan",
    "+.local",
    "localhost.ptlogin2.qq.com",
    "+.msftconnecttest.com",
    "+.msftncsi.com",
    "dns.msftncsi.com",
    "time.*.com",
    "time.*.cn",
    "ntp.*.com",
    "ntp.*.cn",
    "stun.*.*",
    "stun.*.*.*",
    "+.push.apple.com",
    "+.n.n.srv.nintendo.net",
    "+.stun.playstation.net",
    "+.xboxlive.com",
    "+.battlenet.com.cn",
    "Mijia Cloud",
]

# ---------------------------------------------------------------------------
# nameserver-policy：分域解析
# ---------------------------------------------------------------------------
# 默认 nameserver 只有国内 DoH（doh.pub / alidns），fallback 才用境外 ——
# 而 fallback 仅在「国内 DoH 返回 CN 属地 IP」时才启用（fallback-filter geoip CN）。
# 对 AI 域名来说这是错的口径：它们的正确解析结果本来就不该是 CN IP，
# 于是 fallback 永远不会触发，我们一直在用国内 DoH 的答案。
# 显式把 AI 域名钉到境外 DoH：解析更干净，也避免国内 DoH 被投毒时无兜底。
NAMESERVER_POLICY = [
    ("+.cn", DOMESTIC_DOH),
    ("+.claude.ai", OVERSEAS_DOH),
    ("+.anthropic.com", OVERSEAS_DOH),
    ("+.claude.com", OVERSEAS_DOH),
    ("+.claudeusercontent.com", OVERSEAS_DOH),
    ("+.openai.com", OVERSEAS_DOH),
    ("+.chatgpt.com", OVERSEAS_DOH),
    ("+.oaistatic.com", OVERSEAS_DOH),
    ("+.oaiusercontent.com", OVERSEAS_DOH),
]

# 全局严禁出现在产物里的字符串（含坑 2 与严禁的直连 DoT）
BANNED_KEY_PATTERNS = ("global-client-fingerprint",)
BANNED_VALUE_PATTERNS = ("tls://",)

# 模板模式（--emit-template）里用作 proxies 段内容的注入标记。
# 写成【缩进的注释】：一来它是合法 YAML，忘了替换也只是"没有节点"而不是语法错误；
# 二来与正式产物保持同一套结构（都是 `proxies:` + 若干行），
#     这样"模板与完整版只差 proxies 段"才是一句可以被程序验证的话。
# 机器侧（scripts/sync-subscription.sh）把这一整行替换成真实节点条目。
NODE_INJECT_MARKER = "  # @NODE_INJECT_POINT@ ← 此处由机器侧注入本地节点（模板产物不含凭据）"

# 规则前缀（subconverter ruleset 的 [] 引用）
PREFIX_FINAL = "[]FINAL"
PREFIX_GEOIP = "[]GEOIP,"
PREFIX_GEOSITE = "[]GEOSITE,"

# 官方规则集根目录（仅用于把 ini 里的 ACL4SSR/... 短路径还原成完整 URL）
ACL4SSR_RAW_BASES = (
    "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/",
    "https://cdn.jsdelivr.net/gh/ACL4SSR/ACL4SSR@master/",
)


def log(message: str) -> None:
    """日志一律走 stderr，避免污染 stdout 的机器可读输出。"""
    sys.stderr.write("[acl4ssr] %s\n" % message)
    sys.stderr.flush()


def configure_stdio() -> None:
    """让 CLI 在 Windows 默认代码页下也能输出节点组里的 emoji。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            # 兼容被调用方传入的简化文件对象。
            pass


def q(value) -> str:
    """YAML 双引号标量。json.dumps 的输出是合法 YAML 双引号标量，
    且 ensure_ascii=False 能原样保留 emoji（如 "🇯🇵 日本 JP xtls-reality"）。"""
    return json.dumps(str(value), ensure_ascii=False)


def read_text(path) -> str:
    """按 UTF-8 读文本，兼容 CRLF / BOM / 非法字节（规则集里偶尔有脏字节）。"""
    data = Path(path).read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace")


def write_text_atomic(path, text: str, mode: int = 0o644) -> Path:
    """UTF-8 + LF 原子落盘（先写 .tmp 再 os.replace，失败不会留半截文件）。"""
    target = Path(path)
    if target.parent and str(target.parent) not in ("", "."):
        target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, target)
    return target


# ===========================================================================
#  第 1 部分：.ini 解析（subconverter 格式）
# ===========================================================================
def logical_lines(text: str):
    """把 ini 切成逻辑行：去注释、去空白、合并续行。

    subconverter 的 ini 允许行尾 `\\` 续行（ACL4SSR 的长 custom_proxy_group
    经常这样折行），也允许 `#` / `;` / `//` 整行注释。无法识别的行不在这里
    丢弃，交给上层统计（要求「遇到无法识别的行要记录到统计里而不是崩」）。
    """
    pending = ""
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        # 行尾续行符：拼接后继续累积
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1] + " "
            continue
        merged = pending + line
        pending = ""
        stripped = merged.strip()
        if not stripped:
            continue
        if stripped.startswith(("#", ";", "//")):
            continue
        yield stripped
    if pending.strip():
        yield pending.strip()


def split_fields(body: str) -> list:
    """按 ` 反引号切分（subconverter 的字段分隔符）。"""
    return [part.strip() for part in body.split("`")]


def parse_group_line(body: str):
    """解析 custom_proxy_group：

        NAME`TYPE`URL`INTERVAL`MEMBER1`MEMBER2...

    MEMBER 三种形态：
      * `[]组名`      -> 引用另一个策略组（child_refs）
      * DIRECT/REJECT -> 内建策略（builtins）
      * 其它          -> 节点名过滤正则（filters，最终写进 mihomo 的 filter:）
    """
    fields = split_fields(body)
    if len(fields) < 2 or not fields[0]:
        return None, "custom_proxy_group 字段不足 2 段"
    name = fields[0]
    gtype = (fields[1] or "select").lower()
    if gtype not in VALID_GROUP_TYPES:
        return None, "未知策略组类型 %r" % fields[1]

    # 关键：第 3 段起的字段【顺序并不固定】，必须按内容判定，不能按位置硬取。
    # 实测 ACL4SSR ini 的两种真实形态：
    #   select 形态:    NAME`select`[]REJECT`[]DIRECT                （全是成员）
    #   url-test 形态:  NAME`url-test`(港|HK|...)`http://...`300,,50 （正则、URL、参数混合）
    # 早期实现按位置取 fields[2]=URL / fields[3]=INTERVAL / fields[4:]=members，
    # 会把 url-test 的节点过滤正则当 URL 吞掉、把 300,,50 当成节点过滤器，
    # 直接导致地区组一个节点都匹配不到（进而触发不该有的空组兜底）。
    url = ""
    interval_raw = ""
    members = []
    for token in fields[2:]:
        if not token:
            continue
        if re.match(r"(?i)^https?://", token):
            # 测试 URL；仅首个有效
            if not url:
                url = token
            continue
        if re.match(r"^\d+(,\d*)*$", token):
            # 形如 300 / 300,50 / 300,,50 —— 测试间隔与容差参数
            if not interval_raw:
                interval_raw = token
            continue
        members.append(token)

    interval, tolerance = 300, 50
    if interval_raw:
        tokens = [t.strip() for t in interval_raw.split(",")]
        if len(tokens) > 0 and tokens[0].isdigit():
            interval = int(tokens[0])
        # 第二个参数是「测试容差」（URL 与 INTERVAL 之间的位置差异都兼容处理）
        if len(tokens) > 1 and tokens[1].isdigit():
            tolerance = int(tokens[1])
        if len(tokens) > 2 and tokens[2].isdigit():
            tolerance = int(tokens[2])

    child_refs, builtins, filters, bad_regex = [], [], [], []
    # ⚠️ order 记录成员在 ini 里的【原始声明顺序】，三种形态混在一条时间线上。
    #    为什么必须记：subconverter/ACL4SSR 的 select 组语义是「第一项 = 默认选中项」，
    #    而本文件早先把 child_refs / builtins 分桶存、再在 build_groups 里
    #    「children → 正则匹配节点 → builtins」依次拼接 —— 等价于无条件把
    #    DIRECT/REJECT 挪到列表【末尾】。于是 ini 里写 `[]DIRECT` 打头的组
    #    （例如「🎮 游戏下载」）在产物里变成「节点选择打头、DIRECT 垫底」，
    #    默认出口被静默改写：本该直连的游戏下载全部走了代理。
    #    ini 的书写顺序是唯一事实源，这里逐字保留。
    order = []
    for member in members:
        if member.startswith("[]"):
            ref = member[2:].strip()
            if not ref:
                continue
            # 关键：[]DIRECT / []REJECT 这类是【内建策略选项】，不是子组引用。
            # 早期实现一律当子组，导致 `🚀 节点选择` 尾巴上的 []DIRECT 被当成组名，
            # 既不进 members 也不报错 —— 用户就少了"直连"这个可选项。
            if ref.upper() in BUILTIN_POLICIES:
                builtins.append(ref.upper())
                order.append(("builtin", ref.upper()))
            else:
                child_refs.append(ref)
                order.append(("child", ref))
            continue
        upper = member.upper()
        if upper in BUILTIN_POLICIES:
            builtins.append(upper)
            order.append(("builtin", upper))
            continue
        pattern = normalize_filter_pattern(member)
        if pattern is None:
            bad_regex.append(member)
            continue
        filters.append(pattern)
        order.append(("filter", pattern))

    return (
        {
            "name": name,
            "type": gtype,
            "url": url,
            "interval": interval,
            "tolerance": tolerance,
            "children": child_refs,
            "builtins": builtins,
            "filters": filters,
            "order": order,
            "bad_regex": bad_regex,
        },
        None,
    )


def normalize_filter_pattern(member: str):
    """把 ini 里的节点名过滤条件转成 mihomo filter 能用的正则。

    两个坑：
      * ini 写作 `\\(`（转义左括号），而 mihomo 的 filter 是 Go 正则，
        `\\(` 在 Go 里同样是「字面左括号」—— 但 YAML 双引号标量会把 \\ 当转义，
        所以必须先转成字符类 `[()]`，语义一致（匹配字面括号）且无转义歧义。
      * ACL4SSR 的地区正则靠中文/英文大小写混排（如 (日本|JP|jp)），
        统一加 (?i) 前缀保证能命中小写 tag。
    正则非法则返回 None（由上层计入统计），绝不抛异常。
    """
    if not member:
        return None
    pattern = member.strip()
    # 排除型条件（subconverter 支持 -开头 表示排除）
    exclude = pattern.startswith("-")
    if exclude:
        pattern = pattern[1:].strip()
    if not pattern:
        return None
    # \( -> [()]  （字面左括号；同理 \) ）—— 消除 YAML/Go 双重转义歧义
    body = pattern.replace(r"\(", "[()]").replace(r"\)", "[()]")
    body = re.sub(r"\s+", "", body)
    if not body:
        return None
    try:
        # `[[]SELF[]]` is ACL4SSR's portable spelling for a literal
        # `[SELF]`. Python warns about the nested character-set syntax even
        # though it is valid and intentionally supported here.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Possible nested set")
            re.compile(body)
    except re.error:
        return None
    if body.startswith("(?i)"):
        normalized = body
    else:
        normalized = "(?i)" + body
    return ("-" + normalized) if exclude else normalized


def parse_ruleset_line(body: str):
    """解析 ruleset=：

        GROUP,URL[,INTERVAL]     普通形态（URL 也可以是内联规则）
        GROUP,[]FINAL            终局策略声明
        GROUP,[]GEOIP,CN         内建 GEOIP 规则集
        GROUP,[]GEOSITE,cn       内建 GEOSITE 规则集
    """
    fields = [f.strip() for f in body.split(",")]
    if len(fields) < 2 or not fields[0]:
        return None, "ruleset 字段不足 2 段"
    group = fields[0]
    target = fields[1]
    rest = [f for f in fields[2:] if f]
    interval = 86400
    for token in rest:
        if token.isdigit():
            interval = int(token)
            break
    if not target:
        return None, "ruleset 缺少 URL/内联规则"

    # ⚠️ [] 前缀的内建指令，payload 在第二段【之后】，必须先把逗号后面的部分接回来。
    #
    #   ruleset=🎯 全球直连,[]GEOIP,CN
    #       fields = ['🎯 全球直连', '[]GEOIP', 'CN']
    #       target = fields[1] = '[]GEOIP'        ← CN 被丢在外面
    #   而 PREFIX_GEOIP = '[]GEOIP,'（带逗号）→ startswith 恒为假 →
    #   落到下面的 startswith('[]') 分支，被当成"无有效内容的内建指令"静默跳过。
    #
    #   实测后果（本修复前）：
    #       parsed.json  kind 分布 = {'url': 42, 'inline': 1, 'final': 1}
    #       {'group': '🎯 全球直连', 'kind': 'inline', 'target': '[]GEOIP'}
    #       产物里 GEOIP 规则 = 0 条，stats 的丢弃明细里也【不点名】——
    #       国内白名单因此失去最重要的兜底：ChinaDomain.list 只有 726 条，
    #       而国内域名有几十万，没被它覆盖的小站本应由 GEOIP,CN 兜住直连，
    #       结果全部落到 FINAL 走代理（绕道境外，明显变慢）。
    #
    #   为什么一直没被发现：[]FINAL 不带逗号，恰好能正常匹配 ——
    #   于是"[] 前缀已处理"这件事看着是通的。
    #
    #   同一个 bug 也让 []GEOSITE,cn 失效。
    if target.startswith("[]"):
        extra = [f for f in fields[2:] if f and not f.isdigit()]
        if extra:
            target = target + "," + ",".join(extra)

    upper = target.upper()

    kind = "url"
    payload = target
    if upper == PREFIX_FINAL:
        kind = "final"
        payload = ""
    elif upper.startswith(PREFIX_GEOIP):
        kind = "geoip"
        payload = target[len(PREFIX_GEOIP):]
    elif upper.startswith(PREFIX_GEOSITE):
        kind = "geosite"
        payload = target[len(PREFIX_GEOSITE):]
    elif upper.startswith("[]"):
        # 兜底：任何 [] 开头的都是 subconverter 内建指令，绝不是可下载 URL。
        # 实测 ini 里存在不带国家码的畸形条目 "[]GEOIP"，它不含逗号，
        # 若落到下面的 kind="url" 分支就会去下载 .../master/[]GEOIP 并必然 404，
        # 进而让整个 fetch 阶段判失败（40 个真实规则集其实都已成功）。
        # 这类条目无有效规则内容，标记为 inline 让 build 阶段当作空规则跳过。
        kind = "inline"
        payload = target
    elif "," in target:
        # 内联规则（例如 "DOMAIN-SUFFIX,example.com"）
        kind = "inline"
    return (
        {"group": group, "kind": kind, "target": payload, "interval": interval, "raw": body},
        None,
    )


def parse_rules_line(body: str):
    """解析 rules= 行：整行可能是多条规则，用 ` 或 , 分隔的混合形态。

    真实 ini 里常见：
        rules=DOMAIN-SUFFIX,example.com,策略组`DOMAIN-KEYWORD,foo,策略组
    也有把整行当一条规则的写法，这里按 反引号 优先、其次按 3 段一组切分。
    """
    items = []
    if "`" in body:
        candidates = [c.strip() for c in body.split("`") if c.strip()]
    else:
        fields = [f.strip() for f in body.split(",")]
        candidates = []
        # 3 段一组的启发式切分：TYPE,PAYLOAD,POLICY
        index = 0
        while index < len(fields):
            chunk = fields[index:index + 3]
            if len(chunk) == 3:
                candidates.append(",".join(chunk))
                index += 3
            else:
                break
        if not candidates:
            candidates = [body]
    for item in candidates:
        items.append(item)
    return items, None


def parse_ini(text: str, stats: dict):
    """解析整份 ini，返回 (groups, rulesets, inline_rules)。

    所有无法识别的行都被记进 stats['unrecognized_lines']，绝不抛异常。
    """
    groups, rulesets, inline_rules = [], [], []
    seen_group_names = set()

    for line in logical_lines(text):
        # ACL4SSR 文件通常带有 [custom] 这样的 INI 段标题；它不是配置项，
        # 不应被当成“无法识别的行”写入统计并触发误报。
        if re.match(r"^\s*\[[^\]]+\]\s*$", line):
            continue
        if "=" not in line:
            stats["unrecognized_lines"].append(line[:160])
            stats["unrecognized_count"] += 1
            continue
        key, _, body = line.partition("=")
        key = key.strip()
        body = body.strip()
        lowered = key.lower()

        if lowered in ("custom_proxy_group", "custom_proxy_groups"):
            if not body:
                stats["empty_lines"] += 1
                continue
            group, error = parse_group_line(body)
            if error:
                stats["unrecognized_lines"].append("%s=%s (%s)" % (key, body[:100], error))
                stats["unrecognized_count"] += 1
                continue
            if group["name"] in seen_group_names:
                stats["duplicate_groups"] += 1
                continue
            seen_group_names.add(group["name"])
            groups.append(group)
            continue

        if lowered in ("ruleset", "rulesets"):
            if not body:
                stats["empty_lines"] += 1
                continue
            item, error = parse_ruleset_line(body)
            if error:
                stats["unrecognized_lines"].append("%s=%s (%s)" % (key, body[:100], error))
                stats["unrecognized_count"] += 1
                continue
            rulesets.append(item)
            continue

        if lowered in ("rules", "rule"):
            if not body:
                stats["empty_lines"] += 1
                continue
            items, error = parse_rules_line(body)
            if error:
                stats["unrecognized_lines"].append("%s=%s (%s)" % (key, body[:100], error))
                stats["unrecognized_count"] += 1
                continue
            if lowered == "rule":
                # 仅 rule= 行支持 `策略` 后缀绑定单个策略组
                inline_rules.append({"policy": "", "rules": items})
            else:
                inline_rules.append({"policy": "", "rules": items})
            continue

        # 常见但无需处理/无法识别的键：仍然计数，保持「透明」
        stats["ignored_keys"][key] = stats["ignored_keys"].get(key, 0) + 1

    return groups, rulesets, inline_rules


# ===========================================================================
#  第 2 部分：规则归一化（坑 1 白名单 + 坑 3 四段式）
# ===========================================================================
def filter_rule_type(rule_type: str):
    """坑 1：返回 (是否支持, 规范类型)。白名单之外一律不支持。

    注意两种「不支持」在语义上不同，排查时别混：
      * 在 KNOWN_LEGACY_TYPES 里 = mihomo 压根不认这种写法（写了也是废的）
      * 在 EXCLUDED_RULE_TYPES 里 = mihomo 认，但我们主动不要（代价 > 收益）
        例如 IP-ASN：它会把 12 MB 的 ASN.mmdb 变成硬依赖，
        首次加载从 34ms 涨到 1319ms，下载失败还会让整份配置起不来。
    """
    upper = (rule_type or "").strip().upper()
    if not upper:
        return False, ""
    if upper in SUPPORTED_RULE_TYPES:
        return True, upper
    return False, upper


def normalize_rule(raw_line: str, policy: str):
    """把一行规则归一化成 mihomo 能吃的字符串。

    返回 (rule_or_None, dropped_type_or_None, reason_or_None)
      * 坑 1：类型不在白名单 -> 返回 (None, TYPE, None) 由上层计数
      * 坑 3：IP-CIDR,x,no-resolve -> IP-CIDR,x,<策略组>,no-resolve
    """
    line = (raw_line or "").strip()
    if not line:
        return None, None, "blank"
    if line.startswith(("#", ";", "//")):
        return None, None, "comment"
    if line.startswith("- "):                      # 兼容 Clash YAML payload 形态
        line = line[2:].strip()
    line = line.strip().strip("'\"")
    if not line:
        return None, None, "blank"
    if line.upper() in ("PAYLOAD:", "RULES:", "PROXY:", "PROXY-GROUPS:"):
        return None, None, "header"

    fields = [f.strip() for f in line.split(",")]
    if len(fields) < 2:
        return None, None, "fields<2"

    supported, rule_type = filter_rule_type(fields[0])
    if not supported:
        return None, rule_type, None                    # 坑 1 命中

    if rule_type == "MATCH":
        # MATCH 是终局规则：策略取原第 2 段（若原本没写策略则由上层兜底）
        payload_policy = fields[1] if len(fields) > 1 and fields[1] else policy
        return "MATCH,%s" % payload_policy, None, None

    # 规则集文件里普遍是【两段式】TYPE,VALUE —— 策略由所属 ruleset 的组决定，
    # 例如 `DOMAIN-SUFFIX,acl4.ssr`。这里【只能】拒绝"payload 为空"，不能要求自带策略：
    # 下面 effective_policy = policies[0] if policies else policy 正是为两段式准备的兜底。
    # 早期误写成 `len(fields) < 3`，等于强迫每行都写策略，结果 10527 条域名规则被静默
    # 丢弃，产物只剩 IP-CIDR —— 它们恰好因为带 no-resolve 修饰符凑够 3 段而幸存。
    # 后果极其隐蔽：mihomo -t 语法校验照样通过，代理照样能连，但所有域名请求都落到
    # 终极 MATCH，分流（游戏/直连/广告拦截）全部失效。已由 scripts/functional-test.sh 守住。
    if not fields[1]:
        return None, None, "payload-empty"

    payload = fields[1]
    tail = [f for f in fields[2:] if f]

    # ---- 坑 3：把修饰符从策略位置摘出来 ----
    modifiers, policies = [], []
    for token in tail:
        if token.lower() in RULE_MODIFIERS:
            if token.lower() not in modifiers:
                modifiers.append(token.lower())
        elif token:
            policies.append(token)

    # 策略：优先用规则行自带的第 3 段，否则用规则集归属的策略组
    effective_policy = policies[0] if policies else policy
    if not effective_policy:
        return None, None, "policy-empty"
    extra_policies = policies[1:]

    parts = [rule_type, payload, effective_policy] + extra_policies + modifiers
    return ",".join(parts), None, None


def rule_policy_of(rule: str):
    """从归一化后的规则里取出策略组名（用于策略存在性校验）。"""
    fields = [f.strip() for f in rule.split(",")]
    if not fields:
        return ""
    rule_type = fields[0].upper()
    if rule_type == "MATCH":
        return fields[1] if len(fields) > 1 else ""
    if len(fields) < 3:
        return ""
    return fields[2]


def verify_ip_cidr_modifier(rule: str) -> bool:
    """坑 3 反向断言：IP-CIDR 若带 no-resolve，必须是
    IP-CIDR,<cidr>,<策略组>,no-resolve 这种四段式。"""
    fields = [f.strip() for f in rule.split(",")]
    if not fields or fields[0].upper() not in ("IP-CIDR", "IP-CIDR6", "IP-SUFFIX"):
        return True
    if "no-resolve" not in [f.lower() for f in fields]:
        return True
    return len(fields) == 4 and fields[3].lower() == "no-resolve" and bool(fields[2])


# ===========================================================================
#  第 3 部分：规则集下载（带镜像回退 + 重试 + 幂等复用）
# ===========================================================================
class Fetcher:
    def __init__(self, timeout: float = 25.0, retries: int = 3, cache_dir: str = ""):
        self.timeout = max(1.0, float(timeout))
        self.retries = max(1, int(retries))
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.stats = {"hit_cache": 0, "fetch": 0, "fail": 0}

    @staticmethod
    def mirror_urls(url: str):
        """github raw <-> jsdelivr 镜像回退（同源内容，不引入第三方改写）。"""
        candidates = [url]
        if not url.startswith(("http://", "https://")):
            # file:// 等本地协议：不做镜像回退（方便离线/本机自测）
            return candidates
        match = re.match(r"^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$", url)
        if match:
            user, repo, branch, path = match.groups()
            candidates.append("https://cdn.jsdelivr.net/gh/%s/%s@%s/%s" % (user, repo, branch, path))
            candidates.append("https://ghproxy.net/https://raw.githubusercontent.com/%s/%s/%s/%s" % (user, repo, branch, path))
        match = re.match(r"^https://github\.com/([^/]+)/([^/]+)/raw/([^/]+)/(.+)$", url)
        if match:
            user, repo, branch, path = match.groups()
            candidates.append("https://cdn.jsdelivr.net/gh/%s/%s@%s/%s" % (user, repo, branch, path))
        match = re.match(r"^https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$", url)
        if match:
            user, repo, branch, path = match.groups()
            candidates.append("https://raw.githubusercontent.com/%s/%s/%s/%s" % (user, repo, branch, path))
            candidates.append("https://cdn.jsdelivr.net/gh/%s/%s@%s/%s" % (user, repo, branch, path))
        # 去重保序
        seen, ordered = set(), []
        for item in candidates:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
        return ordered

    def _cache_file(self, url: str):
        if not self.cache_dir:
            return None
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / (digest + ".cache")

    def get(self, url: str, use_cache: bool = True):
        """返回 (text, source)。全部候选失败则抛 RuntimeError。"""
        cached = self._cache_file(url)
        if use_cache and cached is not None and cached.is_file() and cached.stat().st_size > 0:
            self.stats["hit_cache"] += 1
            return read_text(cached), "cache:" + url

        last_error = None
        for candidate in self.mirror_urls(url):
            for attempt in range(1, self.retries + 1):
                try:
                    request = urllib.request.Request(
                        candidate,
                        headers={
                            "User-Agent": USER_AGENT,
                            "Accept": "text/plain,*/*",
                            "Cache-Control": "no-cache",
                        },
                    )
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        payload = response.read().decode("utf-8", errors="replace")
                    if not payload.strip():
                        raise ValueError("空响应")
                    if cached is not None:
                        cached.write_text(payload, encoding="utf-8")
                    self.stats["fetch"] += 1
                    return payload, candidate
                except Exception as exc:  # noqa: BLE001 - 单候选失败要能看到并继续
                    last_error = exc
                    time.sleep(min(1.5 * attempt, 4.0))
        self.stats["fail"] += 1
        raise RuntimeError("拉取失败 %s (%s)" % (url, last_error))


def resolve_ruleset_url(target: str):
    """把 ini 里的规则集地址还原成完整 URL。

    ACL4SSR 系 ini 里既有完整 URL，也有 `ACL4SSR/Clash/xxx.list` 这类短路径。
    """
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", target):
        return target
    if target.lstrip().startswith("[]"):
        # 防线：[] 前缀是 subconverter 内建指令（[]FINAL / []GEOIP / []GEOSITE），
        # 拼成 URL 必然是 404。正常解析路径不会走到这里，走到说明分类有漏，
        # 明确抛错以便定位，而不是静默发出一个注定失败的请求。
        raise RuntimeError("非 URL 的 subconverter 内建指令被误当作规则集: %r" % target)
    stripped = target.lstrip("/")
    return ACL4SSR_RAW_BASES[0] + stripped


def ruleset_list_path(rules_dir: Path, index: int) -> Path:
    """规则集落盘路径：纯 ASCII 文件名 + 两位数字编号（契约要求）。"""
    return rules_dir / ("rule%02d.list" % index)


# ===========================================================================
#  第 4 部分：proxies 文件解析（内联节点）
# ===========================================================================
def parse_scalar(token: str):
    """解析 YAML 标量（保留 emoji / 中文；去引号；识别 bool / 数字 / null）。"""
    text = token.strip()
    if not text:
        return ""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        inner = text[1:-1]
        if text[0] == '"':
            try:
                return json.loads(text)
            except ValueError:
                return inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner.replace("''", "'")
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "~"):
        return None
    if re.match(r"^-?\d+$", text):
        try:
            return int(text)
        except ValueError:
            return text
    if re.match(r"^-?\d+\.\d+$", text):
        try:
            return float(text)
        except ValueError:
            return text
    return text


def _split_flow(body: str, sep: str = ","):
    """按分隔符切分 flow 集合，忽略引号/括号内的分隔符。"""
    items, buffer = [], ""
    depth = 0
    quote = ""
    for char in body:
        if quote:
            buffer += char
            if char == quote:
                quote = ""
            continue
        if char in ("'", '"'):
            quote = char
            buffer += char
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        if char == sep and depth == 0:
            items.append(buffer)
            buffer = ""
            continue
        buffer += char
    items.append(buffer)
    return items


def parse_flow(body: str):
    """解析 YAML flow 集合（[] / {}），支持嵌套。"""
    text = body.strip()
    if not text:
        return None
    if text[0] == "[":
        inner = text[1:-1].strip() if text.endswith("]") else text[1:]
        if not inner:
            return []
        return [parse_value(item) for item in _split_flow(inner)]
    if text[0] == "{":
        inner = text[1:-1].strip() if text.endswith("}") else text[1:]
        result = {}
        if not inner:
            return result
        for item in _split_flow(inner):
            if ":" not in item:
                continue
            key, _, value = item.partition(":")
            result[str(parse_scalar(key))] = parse_value(value)
        return result
    return None


def parse_value(token: str):
    text = token.strip()
    if text[:1] in ("[", "{"):
        parsed = parse_flow(text)
        if parsed is not None:
            return parsed
    return parse_scalar(text)


def parse_simple_yaml(text: str):
    """极简 YAML 解析器：只覆盖 mihomo 代理清单用到的子集。

    支持：块映射 / 块序列 / 嵌套映射 / flow 集合 / 双引号转义（含 emoji）。
    不支持锚点、多行标量等高级特性 —— 本场景用不到，遇到不认识的形态直接跳过。
    """
    raw_lines = []
    for raw in text.splitlines():
        line = raw.replace("\t", "    ").rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        raw_lines.append((len(line) - len(stripped), stripped, len(raw) - len(raw.lstrip(" "))))

    def parse_block(index: int, indent: int):
        # 判定块类型
        is_sequence = False
        for depth, content, _spaces in raw_lines[index:]:
            if depth < indent:
                break
            is_sequence = content.startswith("- ")
            break
        container = [] if is_sequence else {}

        while index < len(raw_lines):
            depth, content, _spaces = raw_lines[index]
            if depth < indent:
                break
            if depth > indent:
                # 缩进异常：跳过该行，保证解析不崩
                index += 1
                continue

            if is_sequence:
                if not content.startswith("- "):
                    break
                item_text = content[2:].strip()
                # `- key: value` 的映射项
                if ":" in item_text and not item_text.startswith(("[", "{", "'", '"')):
                    key, _, value = item_text.partition(":")
                    entry = {}
                    if value.strip():
                        entry[str(parse_scalar(key))] = parse_value(value)
                        index += 1
                    else:
                        index += 1
                        child, index = parse_block(index, indent + 2) if index < len(raw_lines) and raw_lines[index][0] > indent else ({}, index)
                        entry[str(parse_scalar(key))] = child
                    # 继续消费同层缩进的兄弟键
                    while index < len(raw_lines):
                        cdepth, ccontent, _ = raw_lines[index]
                        if cdepth != indent + 2 or ccontent.startswith("- "):
                            break
                        if ":" not in ccontent:
                            index += 1
                            continue
                        ckey, _, cvalue = ccontent.partition(":")
                        if cvalue.strip():
                            entry[str(parse_scalar(ckey))] = parse_value(cvalue)
                            index += 1
                        else:
                            index += 1
                            if index < len(raw_lines) and raw_lines[index][0] > indent + 2:
                                child, index = parse_block(index, raw_lines[index][0])
                                entry[str(parse_scalar(ckey))] = child
                            else:
                                entry[str(parse_scalar(ckey))] = {}
                    container.append(entry)
                    continue
                container.append(parse_value(item_text))
                index += 1
                continue

            if content.startswith("- "):
                break
            if ":" not in content:
                index += 1
                continue
            key, _, value = content.partition(":")
            key_text = str(parse_scalar(key))
            if value.strip():
                container[key_text] = parse_value(value)
                index += 1
                continue
            index += 1
            if index < len(raw_lines) and raw_lines[index][0] > indent:
                child, index = parse_block(index, raw_lines[index][0])
                container[key_text] = child
            else:
                container[key_text] = {}
        return container, index

    if not raw_lines:
        return None
    parsed, _ = parse_block(0, raw_lines[0][0])
    return parsed


def _yaml_module():
    try:
        import yaml  # type: ignore
    except Exception:  # noqa: BLE001 - 系统没装 PyYAML 是常态（Debian 11 默认没有）
        return None
    return yaml


def load_yaml_document(text: str):
    """优先用 PyYAML（若系统恰好装了），否则退回内置极简解析器。"""
    module = _yaml_module()
    if module is not None:
        try:
            return module.safe_load(text)
        except Exception as exc:  # noqa: BLE001
            log("WARN: PyYAML 解析失败(%s)，改用内置解析器" % exc)
    return parse_simple_yaml(text)


def parse_proxy_uri(line: str):
    """兜底：解析 URI 形态的节点行（vless:// / hysteria2:// ...）。

    仅用于 proxies 文件是纯文本链接清单的场景。
    """
    from urllib.parse import parse_qs, unquote, urlparse

    scheme, _, rest = line.partition("://")
    scheme = scheme.lower()
    if scheme not in ("vless", "vmess", "trojan", "hysteria2", "hy2", "ss", "tuic", "anytls"):
        return None
    if "#" in rest:
        rest, _, fragment = rest.partition("#")
    else:
        fragment = ""
    name = unquote(fragment) or ("%s-node" % scheme)
    query = ""
    if "?" in rest:
        rest, _, query = rest.partition("?")
    params = {k: v[-1] for k, v in parse_qs(query, keep_blank_values=True).items()}
    if "@" in rest:
        credential, _, address = rest.rpartition("@")
    else:
        credential, address = "", rest
    credential = unquote(credential)
    address = address.rstrip("/")
    port = 443
    if ":" in address:
        host, _, port_text = address.rpartition(":")
        address = host
        if port_text.isdigit():
            port = int(port_text)

    if scheme == "vless":
        node = {
            "name": name,
            "type": "vless",
            "server": address,
            "port": port,
            "uuid": credential,
        }
        if params.get("flow"):
            node["flow"] = params["flow"]
        if params.get("security") in ("tls", "reality"):
            node["tls"] = True
        if params.get("sni"):
            node["servername"] = params["sni"]
        if params.get("pbk"):
            node["reality-opts"] = {"public-key": params["pbk"], "short-id": params.get("sid", "")}
        if params.get("fp"):
            node["client-fingerprint"] = params["fp"]
        if params.get("type") in ("ws", "grpc", "http", "h2"):
            node["network"] = params["type"]
            if params["type"] == "ws":
                node["ws-opts"] = {"path": params.get("path", "/"), "headers": {"Host": params.get("host", address)}}
            if params["type"] == "grpc":
                node["grpc-opts"] = {"grpc-service-name": params.get("serviceName", "")}
        if params.get("insecure") == "1":
            node["skip-cert-verify"] = True
        return node
    if scheme in ("hysteria2", "hy2"):
        node = {
            "name": name,
            "type": "hysteria2",
            "server": address,
            "port": port,
            "password": credential,
        }
        if params.get("sni"):
            node["sni"] = params["sni"]
        if params.get("insecure") == "1":
            node["skip-cert-verify"] = True
        if params.get("obfs"):
            node["obfs"] = params["obfs"]
            if params.get("obfs-password"):
                node["obfs-password"] = params["obfs-password"]
        return node
    if scheme == "trojan":
        return {"name": name, "type": "trojan", "server": address, "port": port, "password": credential}
    if scheme == "vmess":
        import base64

        try:
            padded = credential + "=" * (-len(credential) % 4)
            payload = json.loads(base64.b64decode(padded).decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return None
        node = {
            "name": name,
            "type": "vmess",
            "server": address,
            "port": port,
            "uuid": payload.get("id", ""),
            "alterId": payload.get("aid", 0),
            "cipher": payload.get("scy", "auto"),
        }
        if payload.get("tls"):
            node["tls"] = True
        if payload.get("net"):
            node["network"] = payload["net"]
        return node
    if scheme == "ss":
        import base64

        try:
            padded = credential + "=" * (-len(credential) % 4)
            decoded = base64.b64decode(padded).decode("utf-8", "replace")
            method, _, password = decoded.partition(":")
        except Exception:  # noqa: BLE001
            return None
        return {
            "name": name,
            "type": "ss",
            "server": address,
            "port": port,
            "cipher": method,
            "password": password,
        }
    return None


SINGBOX_ONLY_KEYS = frozenset(
    {
        "tag",
        "detour",
        "domain_strategy",
        "domain-strategy",
        "multiplex",
        "transport",
        "tls",
        "type",
        "server",
        "server_port",
        "method",
        "password",
        "uuid",
        "obfs",
        "up_mbps",
        "down_mbps",
        "network",
        "security",
        "flow",
        "alter_id",
        "packet_encoding",
        "tls_config",
        "tls-config",
    }
)


def normalize_proxy_entry(entry: dict, stats: dict):
    """把任意形态的节点对象规范成 mihomo proxies 条目。

    支持两种来源：
      * mihomo 原生命名（kebab-case，如 skip-cert-verify / ws-opts）
      * sing-box outbound 命名（snake_case + tls/transport 嵌套）
    """
    if not isinstance(entry, dict):
        stats["proxies_dropped"] += 1
        stats["proxies_dropped_reasons"]["非字典条目"] = (
            stats["proxies_dropped_reasons"].get("非字典条目", 0) + 1
        )
        return None

    node = dict(entry)

    # ---- sing-box outbound -> mihomo 字段映射 ----
    ptype = str(node.get("type", "")).lower()
    if ptype in ("hysteria2", "hysteria") and "server_port" in node:
        pass
    if "server_port" in node and "port" not in node:
        node["port"] = node.pop("server_port")
    if node.get("method") and "cipher" not in node:
        node["cipher"] = node.pop("method")
    if node.get("password") and ptype == "hysteria2" and "password" not in node:
        node["password"] = node["password"]
    if "alter_id" in node and "alterId" not in node:
        node["alterId"] = node.pop("alter_id")
    if "packet_encoding" in node and "packet-encoding" not in node:
        node["packet-encoding"] = node.pop("packet_encoding")
    if node.get("up_mbps") and "up" not in node:
        node["up"] = node.pop("up_mbps")
    if node.get("down_mbps") and "down" not in node:
        node["down"] = node.pop("down_mbps")

    tls_block = node.get("tls")
    if isinstance(tls_block, dict):
        tls_enabled = bool(tls_block.get("enabled", True))
        node["tls"] = tls_enabled
        if tls_block.get("insecure"):
            node["skip-cert-verify"] = True
        if tls_block.get("server_name") and "servername" not in node and "sni" not in node:
            if ptype in ("hysteria2", "hysteria", "tuic"):
                node["sni"] = tls_block["server_name"]
            else:
                node["servername"] = tls_block["server_name"]
        if tls_block.get("alpn") and "alpn" not in node:
            node["alpn"] = tls_block["alpn"]
        if isinstance(tls_block.get("reality"), dict) and "reality-opts" not in node:
            reality = tls_block["reality"]
            opts = {}
            if reality.get("public_key"):
                opts["public-key"] = reality["public_key"]
            if reality.get("short_id") is not None:
                opts["short-id"] = reality["short_id"]
            if opts:
                node["reality-opts"] = opts
        if tls_block.get("utls", {}).get("fingerprint") and "client-fingerprint" not in node:
            node["client-fingerprint"] = tls_block["utls"]["fingerprint"]

    transport = node.get("transport")
    if isinstance(transport, dict):
        ttype = str(transport.get("type", "")).lower()
        if ttype:
            node["network"] = ttype
        if ttype == "ws":
            opts = node.get("ws-opts") if isinstance(node.get("ws-opts"), dict) else {}
            opts.setdefault("path", transport.get("path", "/"))
            headers = dict(opts.get("headers") or {})
            host = transport.get("host") or (transport.get("headers") or {}).get("Host")
            if host:
                headers.setdefault("Host", host)
            if headers:
                opts["headers"] = headers
            node["ws-opts"] = opts
        elif ttype == "grpc":
            opts = node.get("grpc-opts") if isinstance(node.get("grpc-opts"), dict) else {}
            opts.setdefault("grpc-service-name", transport.get("service_name", ""))
            node["grpc-opts"] = opts
        elif ttype in ("http", "h2"):
            opts = node.get("h2-opts") if isinstance(node.get("h2-opts"), dict) else {}
            hosts = transport.get("host") or []
            if hosts:
                opts.setdefault("host", hosts if isinstance(hosts, list) else [hosts])
            opts.setdefault("path", transport.get("path", "/"))
            node["h2-opts"] = opts

    # ---- 过滤掉 mihomo 不认识的键 ----
    name = str(node.get("name") or node.get("tag") or "").strip()
    raw_type = str(node.get("type", "")).strip().lower()
    if raw_type == "hysteria":
        raw_type = "hysteria"
    if raw_type in ("hy2",):
        raw_type = "hysteria2"
    if raw_type == "shadowsocks":
        raw_type = "ss"
    if raw_type == "socks":
        raw_type = "socks5"

    if not name:
        stats["proxies_dropped"] += 1
        stats["proxies_dropped_reasons"]["缺少 name/tag"] = (
            stats["proxies_dropped_reasons"].get("缺少 name/tag", 0) + 1
        )
        return None
    if raw_type not in SUPPORTED_PROXY_TYPES:
        stats["proxies_dropped"] += 1
        key = "不支持的协议类型 %s" % (raw_type or "<空>")
        stats["proxies_dropped_reasons"][key] = stats["proxies_dropped_reasons"].get(key, 0) + 1
        return None

    node["name"] = name
    node["type"] = raw_type
    if not node.get("server"):
        stats["proxies_dropped"] += 1
        stats["proxies_dropped_reasons"]["缺少 server"] = (
            stats["proxies_dropped_reasons"].get("缺少 server", 0) + 1
        )
        return None
    if not node.get("port"):
        stats["proxies_dropped"] += 1
        stats["proxies_dropped_reasons"]["缺少 port"] = (
            stats["proxies_dropped_reasons"].get("缺少 port", 0) + 1
        )
        return None

    allowed = set(ALLOWED_PROXY_KEYS["_common"])
    allowed |= {str(k) for k in node.keys() if k.endswith("-opts")}
    cleaned = {}
    for key, value in node.items():
        if key in SINGBOX_ONLY_KEYS and key not in ("type", "server", "uuid", "password", "obfs", "network", "flow", "tls"):
            continue
        if key in allowed or key in ("type", "name", "server", "port"):
            cleaned[key] = value
    cleaned["name"] = name
    cleaned["type"] = raw_type
    return cleaned


def parse_proxies_file(path, stats: dict):
    """解析 /etc/sing-box/subscribe/proxies，返回 mihomo 节点列表。

    依次尝试：JSON -> mihomo YAML(proxies:) -> YAML 序列 -> 单对象 -> URI 行。
    """
    raw = read_text(path)
    entries = []
    mode = "unknown"

    if not raw.strip():
        raise ValueError("proxies 文件为空: %s" % path)

    stripped = raw.lstrip()
    # ---- 形态 1：JSON ----
    if stripped[:1] in ("[", "{"):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                entries = data
                mode = "json-array"
            elif isinstance(data, dict):
                for key in ("proxies", "outbounds", "nodes", "servers"):
                    if isinstance(data.get(key), list):
                        entries = data[key]
                        mode = "json-object.%s" % key
                        break
                else:
                    if data.get("type") and data.get("server"):
                        entries = [data]
                        mode = "json-single"
        except ValueError:
            entries = []

    # ---- 形态 2：YAML ----
    if not entries:
        document = load_yaml_document(raw)
        if isinstance(document, list):
            entries = document
            mode = "yaml-sequence"
        elif isinstance(document, dict):
            for key in ("proxies", "outbounds", "nodes"):
                if isinstance(document.get(key), list):
                    entries = document[key]
                    mode = "yaml-map.%s" % key
                    break
            else:
                if document.get("type") and document.get("server"):
                    entries = [document]
                    mode = "yaml-single"

    # ---- 形态 3：URI 文本行 ----
    if not entries:
        uri_entries = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            node = parse_proxy_uri(line)
            if node:
                uri_entries.append(node)
        if uri_entries:
            entries = uri_entries
            mode = "uri-lines"

    if not entries:
        raise ValueError(
            "无法识别 proxies 文件格式（既不是 JSON / YAML 代理清单，也不是 URI 行）: %s" % path
        )

    nodes, names = [], set()
    for entry in entries:
        node = normalize_proxy_entry(entry, stats)
        if not node:
            continue
        if node["name"] in names:
            # mihomo 不允许重名节点：自动去重（同时统计）
            stats["proxies_renamed"] += 1
            base = node["name"]
            index = 2
            while "%s #%d" % (base, index) in names:
                index += 1
            node["name"] = "%s #%d" % (base, index)
        names.add(node["name"])
        nodes.append(node)

    if not nodes:
        raise ValueError("proxies 文件解析出 0 个可用节点: %s" % path)

    stats["proxies_mode"] = mode
    stats["proxies_total"] = len(entries)
    stats["proxies_count"] = len(nodes)
    return nodes


# ===========================================================================
#  第 5 部分：策略组组装
# ===========================================================================
def build_groups(parsed: dict, proxy_names, stats: dict):
    """按 ini 的 custom_proxy_group 生成策略组，并做引用校验。

    返回 (groups, ordered_names, auto_created)
    """
    raw_groups = parsed.get("groups") or []
    declared_names = set()
    groups = []
    for item in raw_groups:
        name = item["name"]
        if name in declared_names:
            continue
        declared_names.add(name)
        groups.append(
            {
                "name": name,
                "type": item.get("type", "select"),
                "url": item.get("url", ""),
                "interval": int(item.get("interval", 300) or 300),
                "tolerance": int(item.get("tolerance", 50) or 50),
                "children": list(item.get("children") or []),
                "builtins": list(item.get("builtins") or []),
                "order": list(item.get("order") or []),
                "filters": list(item.get("filters") or []),
                "bad_regex": list(item.get("bad_regex") or []),
            }
        )
        for bad in item.get("bad_regex") or []:
            stats["bad_filter_regex"].append({"group": name, "pattern": bad})

    # ---- 策略引用存在性校验（引用了不存在的策略组要计数并记录）----
    for group in groups:
        missing = [child for child in group["children"] if child not in declared_names]
        if missing:
            group["children"] = [child for child in group["children"] if child in declared_names]
            stats["missing_group_refs"] += len(missing)
            stats["missing_group_ref_details"].append({"group": group["name"], "missing": missing})

    # ---- 规则里引用的策略组如果不存在，自动补一个 select 组（否则 mihomo 直接报错）----
    referenced = set()
    for rule_entry in parsed.get("rulesets") or []:
        group_name = rule_entry.get("group", "")
        if group_name and group_name.upper() not in BUILTIN_POLICIES:
            referenced.add(group_name)
    for rule_entry in parsed.get("inline_rules") or []:
        for rule in rule_entry.get("rules") or []:
            policy = rule_policy_of(rule)
            if policy and policy.upper() not in BUILTIN_POLICIES:
                referenced.add(policy)
    for rule in parsed.get("all_rules") or []:
        policy = rule_policy_of(rule)
        if policy and policy.upper() not in BUILTIN_POLICIES:
            referenced.add(policy)
    final_policy = parsed.get("final_policy") or ""
    if final_policy and final_policy.upper() not in BUILTIN_POLICIES:
        referenced.add(final_policy)

    auto_created = []
    for name in sorted(referenced - declared_names):
        groups.append(
            {
                "name": name,
                "type": "select",
                "url": "",
                "interval": 300,
                "tolerance": 50,
                "children": [],
                "builtins": ["DIRECT"],
                "filters": [],
                "bad_regex": [],
            }
        )
        declared_names.add(name)
        auto_created.append(name)

    # ---- 拓扑排序：mihomo 是顺序解析策略组的，被引用的组必须先出现 ----
    ordered = topo_sort(groups)

    # ---- 计算每个组的实际成员 ----
    # 本文件不使用 proxy-providers（要求全内联），因此 ini 里写的是「节点名过滤
    # 正则」的那些策略组，必须在这里把正则展开成真实节点名，否则策略组会是空组。
    all_proxy_names = list(proxy_names)
    for group in ordered:
        per_filter = {}
        matched = match_proxies(proxy_names, group["filters"], stats, per_filter=per_filter)
        members = []
        order = group.get("order") or []
        if order:
            # ⭐ 按 ini 的【原始声明顺序】合并三类成员。
            #    select 组的第一项即默认选中项，所以顺序是有语义的 —— 不能重排。
            #    例：`🎮 游戏下载\`select\`[]DIRECT\`[]🚀 节点选择...` 必须让 DIRECT 留在首位，
            #    否则默认出口变成代理，游戏下载会整包走 VPS 流量。
            for kind, value in order:
                if kind == "child":
                    if value not in members:
                        members.append(value)
                elif kind == "filter":
                    # 每个过滤器在【它自己的原始位置】展开它命中的节点
                    for name in per_filter.get(value) or []:
                        if name not in members:
                            members.append(name)
                elif kind == "builtin":
                    if value not in members:
                        members.append(value)
        else:
            # 兼容没有 order 的旧 parsed.json（不该出现，仅防御）
            for child in group["children"]:
                if child not in members:
                    members.append(child)
            for name in matched:
                if name not in members:
                    members.append(name)
            for builtin in group["builtins"]:
                if builtin not in members:
                    members.append(builtin)
        # 防御：order 展开有遗漏时补齐（正常情况不会触发）
        for name in matched:
            if name not in members:
                members.append(name)
        for builtin in group["builtins"]:
            if builtin not in members:
                members.append(builtin)
        if group["filters"] and not matched:
            # 忠于 ini 原版：本机没有匹配该地区/特征的节点时，该组就是空的。
            # 不再"兜底并入全部节点" —— 那会让「香港节点」组里塞满日本节点，
            # 语义错误（选"香港"实际走日本）。实测 mihomo v1.19.31 对空成员数组
            # 与空组被规则引用均能正常加载（-t 通过），所以空着是安全的。
            stats["groups_without_matched_nodes"].append(group["name"])
        group["members"] = members
        group["matched_nodes"] = matched
        group["no_members"] = (not members) and (not matched)

    return ordered, declared_names, auto_created


def topo_sort(groups):
    """被引用的组优先输出（同时天然打断环）。"""
    by_name = {g["name"]: g for g in groups}
    order, state = [], {}

    def visit(name):
        flag = state.get(name, 0)
        if flag:  # 0=未访问 1=访问中(成环) 2=已完成
            return
        state[name] = 1
        node = by_name.get(name)
        if node is not None:
            for child in node["children"]:
                if child in by_name:
                    visit(child)
            order.append(node)
        state[name] = 2

    for group in groups:
        visit(group["name"])
    return order


def match_proxies(proxy_names, filters, stats: dict, per_filter: dict = None):
    """用 ini 的节点名过滤正则去匹配内联节点名（正则会否命中要能看得见）。

    per_filter 非 None 时，会被填入 {正向过滤器: [命中的节点名]}，
    供调用方按 ini 的书写顺序逐个展开过滤器。
    """
    if not filters:
        return []
    includes, excludes = [], []
    for pattern in filters:
        if pattern.startswith("-"):
            excludes.append(pattern[1:])
        else:
            includes.append(pattern)
    matched = []
    for name in proxy_names:
        hit = False
        if includes:
            for pattern in includes:
                try:
                    if re.search(pattern, name):
                        hit = True
                        break
                except re.error:
                    stats["bad_filter_regex"].append({"group": "<filter>", "pattern": pattern})
                    continue
        if hit:
            for pattern in excludes:
                try:
                    if re.search(pattern, name):
                        hit = False
                        break
                except re.error:
                    continue
        if hit:
            matched.append(name)
    if per_filter is not None:
        # 额外回填「每个正向过滤器各自命中了谁」——供 build_groups 按 ini 原始
        # 顺序展开使用（多个过滤器各自出节点，顺序要跟着 ini 走）。
        for pattern in includes:
            per_filter[pattern] = []
        for name in matched:
            for pattern in includes:
                try:
                    if re.search(pattern, name):
                        per_filter[pattern].append(name)
                except re.error:
                    continue
    return matched


# ===========================================================================
#  第 6 部分：规则收集（内联规则集 + 内联 rules=）
# ===========================================================================
def collect_rules(parsed: dict, rules_dir: Path, stats: dict, max_rules: int):
    """把规则集文件 + 内联 rules= 全部展开成最终 rules 列表（内联，零在线依赖）。"""
    seen = set()
    rules = []
    final_policy = parsed.get("final_policy") or ""
    counters = stats["dropped_rule_types"]

    def push(rule: str, origin: str, policy: str):
        if not rule:
            return
        if rule in seen:
            stats["duplicate_rules"] += 1
            return
        if len(rules) >= max_rules:
            stats["truncated_rules"] += 1
            return
        seen.add(rule)
        rules.append(rule)
        # 策略存在性校验（第 6 项自检数据）
        if policy and policy.upper() not in BUILTIN_POLICIES:
            stats["rule_policy_total"] += 1
            if policy not in parsed.get("_group_names", set()):
                stats["rule_policy_anomalies"] += 1
                if len(stats["rule_policy_anomaly_samples"]) < 20:
                    stats["rule_policy_anomaly_samples"].append(
                        {"rule": rule, "policy": policy, "origin": origin}
                    )

    # ---- 1) 内联 rules= 行 ----
    for entry in parsed.get("inline_rules") or []:
        for raw in entry.get("rules") or []:
            rule, dropped, reason = normalize_rule(raw, entry.get("policy", "") or final_policy)
            if dropped:
                counters[dropped] = counters.get(dropped, 0) + 1
                continue
            if not rule:
                stats["unparsable_rules"] += 1
                continue
            push(rule, "inline", rule_policy_of(rule))

    # ---- 2) 规则集 ----
    for index, item in enumerate(parsed.get("rulesets") or [], start=1):
        group = item.get("group", "")
        kind = item.get("kind", "url")
        if kind == "final":
            continue
        if kind in ("geoip", "geosite"):
            payload = (item.get("target") or "").strip()
            if payload:
                rule = "GEOIP,%s,%s" % (payload, group)
                if "no-resolve" not in payload.lower():
                    rule += ",no-resolve" if kind == "geoip" else ""
                push(rule, "builtin:%s" % kind, group)
            continue
        if kind == "inline":
            raw = item.get("target") or ""
            rule, dropped, _reason = normalize_rule(raw, group)
            if dropped:
                counters[dropped] = counters.get(dropped, 0) + 1
                continue
            if rule:
                push(rule, "inline-ruleset", rule_policy_of(rule))
            continue

        list_path = ruleset_list_path(rules_dir, index)
        if not list_path.is_file():
            stats["missing_ruleset_files"].append(str(list_path))
            continue
        text = read_text(list_path)
        file_rules = 0
        for raw in text.splitlines():
            rule, dropped, reason = normalize_rule(raw, group)
            if dropped:
                counters[dropped] = counters.get(dropped, 0) + 1
                continue
            if not rule:
                if reason not in ("blank", "comment", "header"):
                    stats["unparsable_rules"] += 1
                continue
            push(rule, "ruleset:%02d" % index, rule_policy_of(rule))
            file_rules += 1
        stats["ruleset_files"].append(
            {
                "index": index,
                "group": group,
                "path": str(list_path),
                "lines": file_rules,
            }
        )

    # ---- 3) 兜底 MATCH ----
    if not final_policy or (
        final_policy.upper() not in BUILTIN_POLICIES
        and final_policy not in parsed.get("_group_names", set())
    ):
        fallback = ""
        for group in parsed.get("groups") or []:
            if group.get("type") == "select":
                fallback = group["name"]
                break
        if not fallback:
            for group in parsed.get("groups") or []:
                fallback = group["name"]
                break
        fallback = fallback or "DIRECT"
        if final_policy:
            log("WARN: []FINAL 指向的策略组 %s 不存在，兜底改用 %s" % (final_policy, fallback))
        else:
            log("WARN: ini 未声明 []FINAL，兜底使用首个 select 组: %s" % fallback)
        stats["final_policy_fallback"] = fallback
        final_policy = fallback

    match_rule = "MATCH,%s" % final_policy
    if match_rule not in seen:
        rules.append(match_rule)

    return rules, final_policy


# ===========================================================================
#  第 7 部分：YAML 生成
# ===========================================================================
def emit_proxy(node: dict):
    """输出一个 proxies 条目（双引号标量，emoji 原样保留）。"""
    lines = ["  - name: %s" % q(node["name"]), "    type: %s" % q(node["type"])]

    def dump(value, indent):
        pad = " " * indent
        out = []
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, (dict, list)):
                    out.append("%s%s:" % (pad, key))
                    out.extend(dump(item, indent + 2))
                else:
                    out.append("%s%s: %s" % (pad, key, scalar(item)))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    out.append("%s-" % pad)
                    out.extend(dump(item, indent + 2))
                else:
                    out.append("%s- %s" % (pad, scalar(item)))
        return out

    def scalar(value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if value is None:
            return '""'
        return q(value)

    for key, value in node.items():
        if key in ("name", "type"):
            continue
        if isinstance(value, (dict, list)):
            lines.append("    %s:" % key)
            lines.extend(dump(value, 6))
        else:
            lines.append("    %s: %s" % (key, scalar(value)))
    return lines


def pick_empty_placeholder(groups, nodes) -> str:
    """决定「没有任何成员的策略组」该塞什么占位成员。

    背景：mihomo v1.19.31 有硬约束 —— 每个 proxy-group 必须有 use 或 proxies。
    实测：完全不写 proxies 键 = test failed；显式写 proxies: [] 也 = test failed。
    所以空组必须塞一个占位成员。

    ⚠️ 为什么【不能】用 DIRECT（本文件的旧行为，已废弃）：
    这些空组全都是「🇭🇰 香港节点」这类【地区组】。当用户（或某条规则）选中它时，
    表达的是「我要走香港」。而本机没有香港节点 —— 此时塞 DIRECT 等于
    「没有香港节点，那就用你家宽带直连」：
      * 概念错误 —— 香港直连 ≠ 香港，用户以为走了香港，实际走的是本地 ISP；
      * 而且是【静默泄漏真实 IP】—— 在代理配置里最不该发生的事。
    实测产物里一次就有 6 个地区组落到这个状态（香港/台湾/狮城/美国/韩国/奈飞），
    每一个被选中都是一次真实 IP 外泄，而界面上的表现和"正常走代理"完全一样。

    ⚠️⚠️ 为什么也【不能】用「🚀 节点选择」这类看起来最合理的默认组：
    第一版就是这么写的，结果 mihomo -t 直接拒绝加载：
        loop is detected in ProxyGroup, please check following ProxyGroups:
        [🚀 节点选择 🇨🇳 台湾节点 🇸🇬 狮城节点 🇭🇰 香港节点 🇺🇲 美国节点 🇰🇷 韩国节点]
    因为「🚀 节点选择」自己就引用了这些地区组：
        🚀 节点选择 → [🇭🇰 香港节点, 🇨🇳 台湾节点, ...]
        🇭🇰 香港节点 → [🚀 节点选择]        ← 占位塞出来的环
    教训：占位候选必须是【叶子】，而"哪个组引用了空组"是全局信息，
    在本函数里推不出来。所以别再找组了 —— 直接用【节点】：
    节点不是 ProxyGroup，永远不可能构成环。

    策略：第一个真实节点 → 都无法用时 REJECT。
    宁可连不上，也不要把真实 IP 静默送出去；但能连就用真实节点连，
    而不是拿"地区不符"当借口去直连。
    """
    for node in (nodes or []):
        name = node.get("name")
        if name:
            return name
    # 一个节点都没有（订阅异常）—— 退回 fail closed
    return "REJECT"


def emit_group(group: dict, default_test_url: str, default_interval: int, warn: list,
               empty_placeholder: str = "REJECT"):
    lines = ["  - name: %s" % q(group["name"]), "    type: %s" % group["type"]]

    gtype = group["type"]
    if gtype in ("url-test", "fallback", "load-balance") or group.get("url"):
        lines.append("    url: %s" % q(group.get("url") or default_test_url))
        lines.append("    interval: %d" % int(group.get("interval") or default_interval))
    if gtype in ("url-test", "load-balance"):
        lines.append("    tolerance: %d" % int(group.get("tolerance") or 50))
    if gtype == "load-balance":
        lines.append("    strategy: consistent-hashing")
    if gtype == "select":
        lines.append("    lazy: true")

    members = list(group.get("members") or [])
    if members:
        lines.append("    proxies:")
        for member in members:
            lines.append("      - %s" % q(member))

    if group.get("filters") and group.get("matched_nodes"):
        lines.append("    filter: %s" % q("|".join(group["filters"])))

    if not members and not group.get("matched_nodes"):
        warn.append(group["name"])
        # 忠于 ini 原版：本机没有匹配该地区/特征的节点时，该组没有任何真实成员。
        # 但 mihomo 要求必须有成员，所以塞一个占位。
        # 占位【不用 DIRECT】—— 理由见 pick_empty_placeholder() 的文档串。
        if group.get("filters"):
            lines.append("    filter: %s" % q("|".join(group["filters"])))
        lines.append("    # 本机暂无匹配该组的节点。占位成员 %s 仅为满足 mihomo"
                     % empty_placeholder)
        lines.append("    # [组内必须至少有一个成员] 的硬约束；刻意不用 DIRECT，")
        lines.append("    # 避免出现「选到空地区组 = 真实 IP 直连」这种静默泄漏。")
        lines.append("    proxies:")
        lines.append("      - %s" % q(empty_placeholder))
    return lines


def build_yaml(args, parsed: dict, nodes, groups, rules, final_policy, stats) -> str:
    out = []
    out.append("# =============================================================================")
    out.append("# mihomo 配置 · ACL4SSR 游戏分流增强（全内联）")
    out.append("# 由 sb-oneclick-new/assets/acl4ssr_build.py v%s 自动生成，请勿手改。" % __version__)
    out.append("# 生成时间(UTC): %s" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    out.append("# 规则来源 ini: %s" % (parsed.get("ini_source") or "<未知>"))
    out.append("# ini sha256: %s" % (parsed.get("ini_sha256") or "<未计算>"))
    out.append("# 策略组 %d 个 / 节点 %d 个 / 规则 %d 条（规则与节点均为内联，无在线依赖）"
               % (len(groups), len(nodes), len(rules) + 1))
    out.append("#")
    out.append("# 三大坑处理说明：")
    out.append("#   坑1 URL-REGEX 等旧版 Clash 类型：按 mihomo v1.19.x 类型白名单过滤，已丢弃 %d 条"
               % sum(stats["dropped_rule_types"].values()))
    out.append("#   坑2 global-client-fingerprint 已被 mihomo v1.19.31 移除：本文件刻意不生成该键")
    out.append("#   坑3 IP-CIDR,x,no-resolve：no-resolve 是修饰符不是策略，已改写成 "
               "IP-CIDR,x,<策略组>,no-resolve 四段式")
    out.append("#")
    out.append("# 【严禁】dns.fallback 绝不可写成 tls://8.8.4.4 / tls://1.1.1.1 这类直连 DoT：")
    out.append("#   respect-rules 为 false 时它们不走代理、从物理网卡直连，国内必然被墙，")
    out.append("#   所有命中 fallback 的域名每次新建连接都要干等约 5 秒；同时会把「本机正在")
    out.append("#   向境外 DNS 发加密查询」这一元数据暴露给本地 ISP。故 fallback 只用 https:// DoH。")
    out.append("# =============================================================================")
    out.append("")
    out.append("# 混合代理端口（HTTP/SOCKS5 共用）")
    out.append("mixed-port: %d" % args.mixed_port)
    out.append("mode: rule")
    out.append("# 关闭 IPv6，规避部分机房 IPv6 黑洞导致的连接挂起")
    out.append("ipv6: false")
    out.append("unified-delay: true")
    out.append("tcp-concurrent: true")
    out.append("log-level: %s" % args.log_level)
    out.append("")
    out.append("# 域名嗅探：HTTP + TLS + QUIC 全开，保证 fake-ip 下规则仍能按域名命中")
    out.append("sniffer:")
    out.append("  enable: true")
    out.append("  force-dns-mapping: true")
    out.append("  parse-pure-ip: true")
    out.append("  override-destination: false")
    out.append("  sniff:")
    out.append("    HTTP:")
    out.append("      ports: [80, '8080-8880']")
    out.append("      override-destination: true")
    out.append("    TLS:")
    out.append("      ports: [443, 8443]")
    out.append("    QUIC:")
    out.append("      ports: [443, 8443]")
    out.append("  skip-domain:")
    out.append("    - '+.push.apple.com'")
    out.append("    - 'Mijia Cloud'")
    out.append("")
    out.append("# DNS 顶配：fake-ip + respect-rules + 国内 DoH + 境外加密 DoH")
    out.append("dns:")
    out.append("# 注意是 enable 不是 enabled —— 拼错整段 DNS 会静默失效（坑点）")
    out.append("  enable: true")
    out.append("  enhanced-mode: fake-ip")
    out.append("  fake-ip-range: 198.18.0.1/16")
    out.append("  fake-ip-filter:")
    for entry in FAKE_IP_FILTER:
        out.append("    - %s" % q(entry))
    out.append("# 让 DNS 查询本身也走规则：解析结果与分流策略保持一致，避免 DNS 泄漏")
    out.append("  respect-rules: true")
    out.append("  nameserver:")
    for server in DOMESTIC_DOH:
        out.append("    - %s" % q(server))
    out.append("# 分域解析：AI 域名固定用境外 DoH —— 国内 DoH 对它们的答案可能被污染，")
    out.append("# 而 fallback-filter(geoip CN) 对「非 CN 答案」永不触发，等于没有兜底。")
    out.append("  nameserver-policy:")
    for pattern, servers in NAMESERVER_POLICY:
        out.append("    %s:" % q(pattern))
        for server in servers:
            out.append("      - %s" % q(server))
    out.append("# respect-rules 打开时【必须】有 proxy-server-nameserver，否则 mihomo 起不来")
    out.append("  proxy-server-nameserver:")
    out.append("    - %s" % q(DOMESTIC_DOH[0]))
    out.append("# fallback 只用境外【加密 DoH】；严禁 tls:// 直连 DoT（原因见文件头）")
    out.append("  fallback:")
    for server in OVERSEAS_DOH:
        out.append("    - %s" % q(server))
    out.append("  fallback-filter:")
    out.append("    geoip: true")
    out.append("    geoip-code: CN")
    out.append("")
    out.append("# 节点全部内联（来源: %s，解析形态 %s）"
               % (args.proxies, stats.get("proxies_mode", "未知")))
    # 两种模式都写 `proxies:` 这一行（见 NODE_INJECT_MARKER 的说明）
    out.append("proxies:")
    if getattr(args, "emit_template", False):
        # ── 模板模式：产物的「规则 + 策略组 + dns」全部与正式产物逐字节相同，
        #    只有 proxies 段的内容被换成一个注入标记。
        #    为什么需要它：规则集每天变、节点凭据不变。把"拉规则 + 编译"搬到 CI 后，
        #    公开仓库里只能放【不含节点凭据】的模板，节点由机器侧在部署时注入。
        out.append(NODE_INJECT_MARKER)
    else:
        for node in nodes:
            out.extend(emit_proxy(node))
    out.append("")
    out.append("# 策略组全部来自 ini 的 custom_proxy_group（含 url-test 参数）")
    out.append("proxy-groups:")
    empty_placeholder = pick_empty_placeholder(groups, nodes)
    for group in groups:
        out.extend(emit_group(group, args.test_url, args.test_interval,
                              stats["groups_forced_direct"], empty_placeholder))
    out.append("")
    out.append("# 规则全部内联（来自 ini 的 ruleset 下载结果 + rules= 内联规则）")
    out.append("rules:")
    for rule in rules:
        out.append("  - %s" % q(rule))
    out.append("")
    return "\n".join(out)


# ===========================================================================
#  第 8 部分：自检
# ===========================================================================
def assert_no_banned_keys(yaml_text: str, stats: dict):
    """坑 2 与「严禁直连 DoT」的反向断言（对生成产物本身做检查）。

    注意：必须只检查【非注释行】。产物头部会刻意用中文注释说明
    「本文件刻意不生成 global-client-fingerprint」「严禁 tls:// 直连 DoT」，
    若把注释也算进去，自检会把「解释为什么不用」误判成「用了」，属于自伤式误报。
    """
    # 只保留真正的配置行（丢掉 YAML 注释行与空行）
    config_lines = [
        ln for ln in yaml_text.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    config_text = "\n".join(config_lines)

    problems = []
    for pattern in BANNED_KEY_PATTERNS:
        if pattern in config_text:
            problems.append("产物中出现已移除/禁用键: %s" % pattern)
    for pattern in BANNED_VALUE_PATTERNS:
        if pattern in config_text:
            problems.append("产物中出现严禁的直连 DoT: %s" % pattern)

    # dns 段单独截出来：只有 dns 段里的 enabled: 才是拼写错误。
    # smux.enabled / brutal-opts.enabled 是 mihomo 合法的子键，不能误判。
    dns_match = re.search(
        r"^dns:[ \t]*$(.*?)(?=^[A-Za-z][A-Za-z0-9_-]*:[ \t]*$|\Z)",
        config_text, re.MULTILINE | re.DOTALL,
    )
    dns_section = dns_match.group(1) if dns_match else ""
    if re.search(r"^[ \t]+enabled:", dns_section, re.MULTILINE):
        problems.append("dns 段中出现 enabled:（必须是 enable:，拼错会整段静默失效）")

    if re.search(r"^[ \t]*global-client-fingerprint", config_text, re.MULTILINE):
        problems.append("产物中出现 global-client-fingerprint")

    stats["self_check_problems"] = problems
    for item in problems:
        log("SELF-CHECK FAIL: %s" % item)
    return not problems


def final_self_check(parsed: dict, nodes, groups, rules, final_policy, stats: dict):
    """产物落地后的最终自检（结构、类型、坑 3 反向验证）。"""
    problems = list(stats.get("self_check_problems") or [])
    if not groups:
        problems.append("策略组数为 0")
    if not nodes:
        problems.append("内联节点数为 0")
    if not rules:
        problems.append("规则数为 0")
    if not rules or not rules[-1].startswith("MATCH,"):
        problems.append("最后一条规则不是 MATCH 兜底")

    for rule in rules:
        fields = [f.strip() for f in rule.split(",")]
        rule_type = fields[0].upper() if fields else ""
        if rule_type and rule_type not in SUPPORTED_RULE_TYPES:
            problems.append("白名单漏网类型: %s" % rule[:60])
        if rule_type in ("IP-CIDR", "IP-CIDR6", "IP-SUFFIX") and not verify_ip_cidr_modifier(rule):
            problems.append("坑3 未修正的三段式: %s" % rule[:60])
        policy = rule_policy_of(rule)
        if policy and policy.upper() not in BUILTIN_POLICIES:
            names = parsed.get("_group_names") or set()
            if policy not in names and policy not in {g["name"] for g in groups}:
                problems.append("悬空策略引用: %s" % rule[:60])

    stats["self_check_problems"] = problems
    return problems


def print_stats(stats: dict, parsed: dict, nodes, groups, rules, final_policy, stream=sys.stdout):
    """打印自检统计：策略组数 / 节点数 / 规则数 / 被过滤类型统计 / 策略引用异常条数。"""
    dropped = stats.get("dropped_rule_types") or {}
    dropped_total = sum(dropped.values())
    lines = []
    lines.append("---- ACL4SSR 自检统计 ----")
    lines.append("策略组数        : %d" % len(groups))
    lines.append("节点数(内联)    : %d" % len(nodes))
    lines.append("规则数(含MATCH) : %d" % len(rules))
    lines.append("终局策略        : MATCH,%s" % final_policy)
    lines.append("规则集文件数    : %d" % len(stats.get("ruleset_files") or []))
    lines.append("被过滤类型统计  : 共 %d 条" % dropped_total)
    if dropped:
        for rule_type, count in sorted(dropped.items(), key=lambda item: (-item[1], item[0])):
            note = KNOWN_LEGACY_TYPES.get(rule_type, "")
            lines.append("  - %-18s %6d 条  %s" % (rule_type, count, note))
    else:
        lines.append("  - （无）")
    lines.append("策略引用异常条数: %d" % stats.get("rule_policy_anomalies", 0))
    lines.append("  - 策略组内子组悬空引用: %d 条" % stats.get("missing_group_refs", 0))
    lines.append("  - 过滤器正则非法      : %d 条" % len(stats.get("bad_filter_regex") or []))
    lines.append("  - 无法识别 ini 行     : %d 条" % stats.get("unrecognized_count", 0))
    lines.append("去重丢弃规则    : %d 条" % stats.get("duplicate_rules", 0))
    if stats.get("groups_without_matched_nodes"):
        lines.append("过滤器零命中组  : %d 个（忠于 ini 原版，保持为空）"
                     % len(stats["groups_without_matched_nodes"]))
        lines.append("  - 例: %s" % ", ".join(stats["groups_without_matched_nodes"][:6]))
    if stats.get("auto_created_groups"):
        lines.append("自动补全策略组  : %d 个（ini 未定义但被规则引用）" % len(stats["auto_created_groups"]))
    if stats.get("groups_forced_direct"):
        lines.append("空组(占位成员)  : %d 个（不兜底 DIRECT）" % len(stats["groups_forced_direct"]))
    lines.append("节点解析        : 形态=%s 原始=%d 可用=%d 丢弃=%d"
                 % (stats.get("proxies_mode", "-"), stats.get("proxies_total", 0),
                    stats.get("proxies_count", 0), stats.get("proxies_dropped", 0)))
    lines.append("自检问题        : %d 项" % len(stats.get("self_check_problems") or []))
    for item in stats.get("self_check_problems") or []:
        lines.append("  - %s" % item)
    text = "\n".join(lines)
    stream.write(text + "\n")
    stream.flush()
    return text


# ===========================================================================
#  第 9 部分：模式实现
# ===========================================================================
def make_stats():
    return {
        "dropped_rule_types": {},
        "rule_policy_anomalies": 0,
        "rule_policy_total": 0,
        "rule_policy_anomaly_samples": [],
        "missing_group_refs": 0,
        "missing_group_ref_details": [],
        "duplicate_groups": 0,
        "duplicate_rules": 0,
        "truncated_rules": 0,
        "unrecognized_count": 0,
        "unrecognized_lines": [],
        "ignored_keys": {},
        "empty_lines": 0,
        "unparsable_rules": 0,
        "bad_filter_regex": [],
        "groups_without_matched_nodes": [],
        "groups_filter_fallback_all": [],
        "groups_forced_direct": [],
        "ruleset_files": [],
        "missing_ruleset_files": [],
        "proxies_mode": "",
        "proxies_total": 0,
        "proxies_count": 0,
        "proxies_dropped": 0,
        "proxies_dropped_reasons": {},
        "proxies_renamed": 0,
        "self_check_problems": [],
        "fetch": {},
    }


def mode_parse(args) -> int:
    """模式 parse：只解析 ini，落 parsed.json + stats.json（不联网、不写 etc）。"""
    stats = make_stats()
    ini_path = Path(args.ini)
    if not ini_path.is_file():
        log("FATAL: ini 不存在: %s" % ini_path)
        return 2
    raw = ini_path.read_bytes()
    text = read_text(ini_path)
    stats["ini_sha256"] = hashlib.sha256(raw).hexdigest()
    stats["ini_lines"] = len(text.splitlines())

    groups, rulesets, inline_rules = parse_ini(text, stats)

    final_policy = ""
    for item in rulesets:
        if item["kind"] == "final":
            final_policy = item["group"]
    if not final_policy:
        for item in rulesets:
            if item["kind"] == "url" or item["kind"] == "inline":
                final_policy = item["group"]
                break

    fetchable = [item for item in rulesets if item["kind"] == "url"]
    parsed = {
        "version": __version__,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ini_path": str(ini_path),
        "ini_source": args.ini_source or str(ini_path),
        "ini_sha256": stats["ini_sha256"],
        "ini_lines": stats["ini_lines"],
        "group_count": len(groups),
        "groups": groups,
        "ruleset_count": len(rulesets),
        "rulesets": rulesets,
        "fetchable_count": len(fetchable),
        "inline_rule_blocks": len(inline_rules),
        "inline_rules": inline_rules,
        "final_policy": final_policy,
        "unrecognized_count": stats["unrecognized_count"],
        "unrecognized_lines": stats["unrecognized_lines"][:50],
        "ignored_keys": stats["ignored_keys"],
    }

    write_text_atomic(args.parsed_json, json.dumps(parsed, ensure_ascii=False, indent=2) + "\n")
    write_text_atomic(args.stats_json, json.dumps(stats, ensure_ascii=False, indent=2, default=str) + "\n")
    log("parse OK: 策略组=%d ruleset=%d(可下载=%d) 内联规则块=%d 终局=%s"
        % (len(groups), len(rulesets), len(fetchable), len(inline_rules), final_policy or "<无>"))
    if stats["unrecognized_count"]:
        log("WARN: 无法识别的 ini 行 %d 条（已记录到 stats.json）" % stats["unrecognized_count"])
    if stats["duplicate_groups"]:
        log("WARN: ini 内重复定义的策略组 %d 个（保留首个）" % stats["duplicate_groups"])
    print("ACL4SSR_PARSE_OK groups=%d rulesets=%d fetchable=%d final=%s parsed_json=%s"
          % (len(groups), len(rulesets), len(fetchable), final_policy or "-", args.parsed_json))
    return 0


def mode_fetch(args) -> int:
    """模式 fetch：按 parsed.json 把每个 ruleset 下载到 rule<NN>.list（纯 ASCII 命名）。"""
    stats = make_stats()
    parsed_path = Path(args.parsed_json)
    if not parsed_path.is_file():
        log("FATAL: parsed.json 不存在: %s（请先跑 parse 模式）" % parsed_path)
        return 2
    parsed = json.loads(read_text(parsed_path))
    rules_dir = Path(args.rules_dir)
    rules_dir.mkdir(parents=True, exist_ok=True)

    fetcher = Fetcher(timeout=args.timeout, retries=args.retries, cache_dir=args.cache_dir)
    ok, fail, skipped = 0, 0, 0
    results = []
    for index, item in enumerate(parsed.get("rulesets") or [], start=1):
        if item.get("kind") != "url":
            continue
        target = item.get("target") or ""
        list_path = ruleset_list_path(rules_dir, index)
        group = item.get("group", "")
        if list_path.is_file() and list_path.stat().st_size > 0 and not args.force:
            line_count = len(read_text(list_path).splitlines())
            skipped += 1
            results.append({"index": index, "group": group, "path": str(list_path),
                            "lines": line_count, "status": "REUSE"})
            print("RULEFILE\t%02d\t%s\tREUSE\t%d\t%s" % (index, group, line_count, list_path.name))
            continue
        url = resolve_ruleset_url(target)
        try:
            text, source = fetcher.get(url, use_cache=not args.no_cache)
        except RuntimeError as exc:
            fail += 1
            results.append({"index": index, "group": group, "path": str(list_path),
                            "lines": 0, "status": "FAIL", "error": str(exc), "url": url})
            print("RULEFILE\t%02d\t%s\tFAIL\t0\t%s" % (index, group, url))
            log("FAIL: 规则集下载失败 [%s] %s" % (group, exc))
            continue
        # 规则集统一按 LF + UTF-8 落盘（去掉 BOM/CR），后续 build 直接按行读
        # write_text_atomic 内部已强制 LF，这里只需先把 CR 归一化
        clean = text.replace("\r\n", "\n").replace("\r", "\n")
        write_text_atomic(list_path, clean)
        line_count = len([ln for ln in clean.splitlines() if ln.strip()])
        ok += 1
        results.append({"index": index, "group": group, "path": str(list_path),
                        "lines": line_count, "status": "OK", "url": url, "source": source})
        print("RULEFILE\t%02d\t%s\tOK\t%d\t%s" % (index, group, line_count, list_path.name))

    stats["fetch"] = {
        "ok": ok,
        "fail": fail,
        "reuse": skipped,
        "files": results,
        "fetcher": fetcher.stats,
    }
    write_text_atomic(args.stats_json, json.dumps(stats, ensure_ascii=False, indent=2, default=str) + "\n")
    log("fetch 完成: 下载成功=%d 失败=%d 复用=%d (网络=%d 缓存=%d)"
        % (ok, fail, skipped, fetcher.stats["fetch"], fetcher.stats["hit_cache"]))
    print("ACL4SSR_FETCH_OK ok=%d fail=%d reuse=%d rules_dir=%s" % (ok, fail, skipped, rules_dir))
    return 1 if fail and not ok else 0


def mode_build(args) -> int:
    """模式 build：规则集 + proxies -> 全内联 mihomo YAML（+ 自检统计）。"""
    stats = make_stats()
    parsed_path = Path(args.parsed_json)
    if not parsed_path.is_file():
        log("FATAL: parsed.json 不存在: %s（请先跑 parse 模式）" % parsed_path)
        return 2
    parsed = json.loads(read_text(parsed_path))
    if args.ini_sha256:
        parsed["ini_sha256"] = args.ini_sha256

    # ---- 节点 ----
    proxies_path = Path(args.proxies)
    if not proxies_path.is_file() or proxies_path.stat().st_size == 0:
        log("FATAL: proxies 文件不存在或为空: %s" % proxies_path)
        log("       请先完成阶段 1/2（生成 /etc/sing-box/subscribe/proxies）后重试。")
        return 3
    try:
        nodes = parse_proxies_file(proxies_path, stats)
    except ValueError as exc:
        log("FATAL: %s" % exc)
        return 3
    if args.max_nodes and len(nodes) > args.max_nodes:
        log("WARN: 节点数 %d 超过 --max-nodes=%d，截断" % (len(nodes), args.max_nodes))
        nodes = nodes[: args.max_nodes]

    # ---- 策略组 ----
    parsed["_group_names"] = {g["name"] for g in parsed.get("groups") or []}
    groups, group_names, auto_created = build_groups(parsed, [n["name"] for n in nodes], stats)
    parsed["_group_names"] = group_names
    if auto_created:
        log("WARN: ini 未定义但被规则引用，已自动补全策略组 %d 个: %s"
            % (len(auto_created), ", ".join(auto_created)))
    stats["auto_created_groups"] = auto_created

    # ---- 规则 ----
    rules, final_policy = collect_rules(parsed, Path(args.rules_dir), stats, args.max_rules)
    if stats["missing_ruleset_files"]:
        log("WARN: 缺少 %d 个规则集文件（未执行 fetch 或下载失败），这些规则集被跳过"
            % len(stats["missing_ruleset_files"]))
    if not rules:
        log("FATAL: 未收集到任何规则")
        return 4

    # ---- 生成 YAML ----
    yaml_text = build_yaml(args, parsed, nodes, groups, rules, final_policy, stats)
    output_path = write_text_atomic(args.output, yaml_text)
    stats["output"] = str(output_path)
    stats["output_bytes"] = output_path.stat().st_size

    # ---- 自检 ----
    assert_no_banned_keys(yaml_text, stats)
    problems = final_self_check(parsed, nodes, groups, rules, final_policy, stats)

    stats["group_count"] = len(groups)
    stats["node_count"] = len(nodes)
    stats["rule_count"] = len(rules)
    stats["final_policy"] = final_policy
    stats["group_names"] = [g["name"] for g in groups]
    stats["dropped_rule_total"] = sum(stats["dropped_rule_types"].values())
    write_text_atomic(args.stats_json, json.dumps(stats, ensure_ascii=False, indent=2, default=str) + "\n")

    print_stats(stats, parsed, nodes, groups, rules, final_policy)
    log("build OK -> %s (%.1f KiB)" % (output_path, stats["output_bytes"] / 1024.0))
    print("ACL4SSR_BUILD_OK groups=%d nodes=%d rules=%d final=%s dropped=%d policy_anomalies=%d output=%s"
          % (len(groups), len(nodes), len(rules), final_policy,
             sum(stats["dropped_rule_types"].values()), stats["rule_policy_anomalies"], output_path))
    if stats["groups_forced_direct"]:
        log("WARN: 以下策略组没有任何成员，已用【第一个真实节点】占位（不用 DIRECT，"
            "避免选到空地区组时静默直连泄漏真实 IP）: %s"
            % ", ".join(stats["groups_forced_direct"][:10]))
    return 1 if problems else 0


# ===========================================================================
#  第 10 部分：命令行入口
# ===========================================================================
def env_default(key: str, fallback):
    value = os.environ.get(key)
    if value is None or str(value).strip() == "":
        return fallback
    return value


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="acl4ssr_build.py",
        description="ACL4SSR(subconverter .ini) -> mihomo 全内联 YAML 编译器（阶段 5 资产）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="模式: parse 只解析 ini / fetch 下载规则集 / build 生成 YAML\n"
               "全部路径都可用参数覆盖，默认值遵循 sb-oneclick 统一接口契约。",
    )
    parser.add_argument("--version", action="version", version="acl4ssr_build.py %s" % __version__)
    sub = parser.add_subparsers(dest="mode")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--ini", default=env_default("ACL4SSR_INI", DEF_INI_PATH),
                        help="ini 文件路径（默认 %s）" % DEF_INI_PATH)
    common.add_argument("--parsed-json", default=env_default("ACL4SSR_PARSED_JSON", DEF_PARSED_JSON),
                        help="解析结果 JSON（默认 %s）" % DEF_PARSED_JSON)
    common.add_argument("--stats-json", default=env_default("ACL4SSR_STATS_JSON", DEF_STATS_JSON),
                        help="统计 JSON（默认 %s）" % DEF_STATS_JSON)

    p_parse = sub.add_parser("parse", parents=[common], help="只解析 ini -> parsed.json")
    p_parse.add_argument("--ini-source", default="", help="ini 来源描述（写进产物注释）")
    p_parse.set_defaults(func=mode_parse)

    p_fetch = sub.add_parser("fetch", parents=[common], help="下载规则集 -> rule<NN>.list")
    p_fetch.add_argument("--rules-dir", default=env_default("ACL4SSR_RULE_DIR", DEF_RULE_DIR),
                         help="规则集目录（默认 %s）" % DEF_RULE_DIR)
    p_fetch.add_argument("--cache-dir", default="/tmp/sb-acl4ssr-cache", help="下载缓存目录")
    p_fetch.add_argument("--timeout", type=float, default=25.0)
    p_fetch.add_argument("--retries", type=int, default=3)
    p_fetch.add_argument("--no-cache", action="store_true", help="忽略磁盘缓存")
    p_fetch.add_argument("--force", action="store_true", help="已存在的 rule<NN>.list 也重新下载")
    p_fetch.set_defaults(func=mode_fetch)

    p_build = sub.add_parser("build", parents=[common], help="生成 YAML")
    p_build.add_argument("--rules-dir", default=env_default("ACL4SSR_RULE_DIR", DEF_RULE_DIR),
                         help="规则集目录（默认 %s）" % DEF_RULE_DIR)
    p_build.add_argument("--proxies", "--proxies-file", dest="proxies",
                         default=env_default("ACL4SSR_PROXIES", DEF_PROXIES),
                         help="订阅节点文件（默认 %s）" % DEF_PROXIES)
    p_build.add_argument("--conf-dir", default=env_default("SB_CONF_DIR", DEF_CONF_DIR),
                         help="sing-box 配置目录（仅用于提示，凭据一律运行时回读，绝不硬编码）")
    p_build.add_argument("--output", default=env_default("ACL4SSR_OUT", DEF_OUTPUT),
                         help="输出 YAML（默认 %s）" % DEF_OUTPUT)
    p_build.add_argument("--sub-port", type=int, default=int(env_default("SUB_PORT", DEF_SUB_PORT)),
                         help="订阅上游端口（默认 %d）" % DEF_SUB_PORT)
    p_build.add_argument("--traffic-port", type=int,
                         default=int(env_default("TRAFFIC_PORT", DEF_TRAFFIC_PORT)),
                         help="流量端口（默认 %d）" % DEF_TRAFFIC_PORT)
    p_build.add_argument("--mixed-port", type=int,
                         default=int(env_default("ACL4SSR_MIXED_PORT", 7890)),
                         help="mixed-port（默认 7890）")
    p_build.add_argument("--max-rules", type=int,
                         default=int(env_default("ACL4SSR_MAX_RULES", 200000)))
    p_build.add_argument("--max-nodes", type=int, default=int(env_default("ACL4SSR_MAX_NODES", 0)),
                         help="0 表示不限制")
    p_build.add_argument("--test-url", default="https://www.gstatic.com/generate_204")
    p_build.add_argument("--test-interval", type=int, default=300)
    p_build.add_argument("--log-level", default="info")
    p_build.add_argument("--ini-sha256", default="", help="ini 的 sha256（写进产物注释，便于追溯）")
    p_build.add_argument("--emit-template", action="store_true",
                         help="模板模式：proxies 段内容换成 @NODE_INJECT_POINT@ 标记"
                              "（供公开仓库存放不含凭据的产物）")
    p_build.set_defaults(func=mode_build)

    return parser, parser.parse_args(argv)


def main(argv=None) -> int:
    configure_stdio()
    parser, args = parse_args(sys.argv[1:] if argv is None else argv)
    if not getattr(args, "mode", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log("中断")
        return 130
    except OSError as exc:
        log("FATAL: 文件系统错误: %s" % exc)
        return 5


if __name__ == "__main__":
    sys.exit(main())
