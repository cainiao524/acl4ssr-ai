"""Tests for the Japan-group / AI-filter / auto-group pinning patches.

run: python -m unittest discover -s tests
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build

# 自建节点的规范名（最后一段必须是协议名 —— 机器侧 sing-box 靠剥末尾协议名反推）
SELF_REALITY_NAME = "🇯🇵 日本 [SELF] xtls-reality"


MS_RULESET_LINE = "ruleset=Ⓜ️ 微软服务,https://example.com/Microsoft.list"


def fixture(with_japan=True, with_auto=True, with_steam=True,
            with_ms_ruleset=True, ms_ruleset_line=None):
    lines = [
        '[custom]',
        build.ANCHOR,
        'custom_proxy_group=💬 Ai平台`select`[]DIRECT',
        'ruleset=🎮 游戏下载,https://example.com/game.list',
        'ruleset=🎮 Steam 商店/社区,https://example.com/web.list',
        ';clash_rule_base=https://example.com/base.yaml',
    ]
    # 上游形态：微软规则集声明在 AI 锚点【之前】—— 这正是 openai/chatgpt
    # 会被 azure.com / windows.net / azureedge.net 抢先的原因。
    if with_ms_ruleset:
        lines.insert(1, ms_ruleset_line or MS_RULESET_LINE)
    if with_japan:
        lines.insert(3, 'custom_proxy_group=🇯🇵 日本节点`url-test`日本`http://example.com`300')
    if with_auto:
        lines.insert(4, 'custom_proxy_group=♻️ 自动选择`url-test`.*`http://example.com`300,,50')
        lines.insert(5, 'custom_proxy_group=🔯 故障转移`fallback`.*`http://example.com`300,,50')
        lines.insert(6, 'custom_proxy_group=🔮 负载均衡`load-balance`.*`http://example.com`300,,50')
    if with_steam:
        # 上游形态：游戏下载/游戏平台已是 DIRECT 优先，商店社区是节点选择优先
        lines.insert(7, 'custom_proxy_group=🎮 游戏下载`select`[]DIRECT`[]🚀 节点选择')
        lines.insert(8, 'custom_proxy_group=🎮 游戏平台`select`[]DIRECT`[]🚀 节点选择')
        lines.insert(9, 'custom_proxy_group=🎮 Steam 商店/社区`select`[]🚀 节点选择`[]DIRECT')
    return '\n'.join(lines) + '\n'


class JapanGroupTests(unittest.TestCase):
    def test_japan_group_is_pinned_select_not_flapping(self):
        output, report = build.apply_patch(fixture())
        self.assertTrue(report['japan_fallback_replaced'])
        self.assertIn(build.JAPAN_FALLBACK_GROUP, output)

        # 必须是 select（恒定出口），不能再是 fallback/url-test（会自动切协议）。
        # 这条断言防的是一次真实用户反馈："连接方式一直变、不稳定"。
        for flapping in ('`fallback`', '`url-test`', '`load-balance`'):
            self.assertNotIn(flapping, build.JAPAN_FALLBACK_GROUP)

        # 两个协议都要在组里（保留手动切换能力），且 REALITY 在首位 = 默认出口
        self.assertIn('xtls-reality', build.JAPAN_FALLBACK_GROUP)
        self.assertIn('hysteria2', build.JAPAN_FALLBACK_GROUP)
        self.assertLess(build.JAPAN_FALLBACK_GROUP.index('eality'),
                        build.JAPAN_FALLBACK_GROUP.index('hysteria2'))

        # 协议名必须在节点名末尾（机器侧 sing-box 内核靠"剥掉末尾协议名"反推节点名）
        self.assertNotIn('xtls-reality [SELF]', build.JAPAN_FALLBACK_GROUP)
        self.assertNotIn('hysteria2 [SELF]', build.JAPAN_FALLBACK_GROUP)

        # 必须是【过滤器】形式，不能是 `[]节点名` 引用 ——
        # 转换站的 append_type=true 会把节点改名成 `🇯🇵 [VLESS] 日本 [SELF] ...`，
        # 名字引用当场悬空，客户端整份配置加载失败（见 ConverterRenameImmunityTests）。
        self.assertNotIn('[]🇯🇵 日本 [SELF]', build.JAPAN_FALLBACK_GROUP)
        self.assertNotIn('[]🇯🇵 [VLESS]', build.JAPAN_FALLBACK_GROUP)

        self.assertNotIn('🇯🇵 日本节点`url-test`', output)

    def test_japan_group_keeps_airport_japan_nodes_after_the_self_hosted_ones(self):
        """「🇯🇵 日本节点」是【地区组】，机场的日本节点必须在里面 —— 但排在自建之后。

        ⚠️ 这里防的是两个方向相反的错，必须同时成立：

        ① 只有自建 2 个 —— 组名叫「日本节点」却一个机场日本节点都没有。
           它是 ACL4SSR 的地区组，另外 13 个组（油管/奈飞/国外媒体/漏网之鱼/
           Steam 商店…）都 `[]🇯🇵 日本节点` 引用它，于是那 13 个组一起
           失去了"选一个机场日本节点"的能力。

        ② 机场节点排到前面 —— 默认出口就漂到机场了。
           select 组【首位即默认出口】，而 AI 链路虽然另走 🔒 AI 专用，
           地区组被手动选中时同样要保证落在自己的机器上。
        """
        # ① 三个成员，顺序必须是 自建Reality → 自建Hysteria2 → 机场日本
        self.assertEqual(
            build.JAPAN_FALLBACK_GROUP,
            "custom_proxy_group=🇯🇵 日本节点`select`"
            + build.SELF_REALITY_FILTER + "`"
            + build.SELF_HYSTERIA2_FILTER + "`"
            + build.JAPAN_REGION_FILTER,
        )
        # ② 默认出口 = 首位 = 自建 Reality，且不是任何"自己会换"的类型
        self.assertLess(build.JAPAN_FALLBACK_GROUP.index('xtls-reality'),
                        build.JAPAN_FALLBACK_GROUP.index(build.JAPAN_REGION_FILTER))
        for flapping in ('`fallback`', '`url-test`', '`load-balance`'):
            self.assertNotIn(flapping, build.JAPAN_FALLBACK_GROUP)

    def test_japan_region_filter_is_positive_and_overlaps_on_purpose(self):
        """地区过滤器必须是【正向】的，并且会命中自建节点 —— 这是有意的。

        ⚠️ 别"优化"成 `^(?!.*\\[SELF\\]).*`（先把自建排除掉）：
           subconverter-ng 不支持前瞻，实测匹配 0 个节点，
           而失败方式是静默的 —— select 组退化成 [DIRECT]，配置照样能加载。
        重叠不会造成重复：实测转换站会去重，最终 20 个成员、0 重复。
        """
        rx = re.compile(build.JAPAN_REGION_FILTER)
        # 正向：不能含任何前瞻
        self.assertNotIn("(?!", build.JAPAN_REGION_FILTER)
        # 机场日本节点必须命中
        for name in ("🇯🇵 日本S01 | IEPL", "🇯🇵 日本S05 | 下载专用 | x0.01",
                     "🇯🇵 免费-日本1-Ver.7", "🇯🇵 [SS] 日本S02 | IEPL"):
            self.assertTrue(rx.search(name), "机场日本节点没被命中：%r" % name)
        # 自建节点也会被命中（同含「日本」）—— 靠转换站去重，不靠前瞻
        for name in ("🇯🇵 日本 [SELF] xtls-reality",
                     "🇯🇵 [VLESS] 日本 [SELF] xtls-reality"):
            self.assertTrue(rx.search(name), "自建节点应当被命中（随后去重）：%r" % name)
        # 别的地区不能被误伤
        for name in ("🇭🇰 香港S01", "🇸🇬 新加坡S01 | IEPL | x2",
                     "🇺🇸 美国S01 | IEPL | x1.5", "🇰🇷 韩国S01", "🇹🇷 土耳其S01"):
            self.assertFalse(rx.search(name), "不该命中：%r" % name)

    def test_auto_select_groups_pinned_to_self_hosted(self):
        """♻️/🔯/🔮 必须从 `.*` 改成只指向自建节点。

        上游这三个组是 url-test/fallback/load-balance + filter `.*`，
        也就是【从所有节点里挑最快】。加了机场订阅后默认
             🚀 节点选择 → ♻️ 自动选择
        会变成"最快的机场节点"—— 出口 IP 漂到机场，对 AI 账号是头号封号信号。
        """
        output, report = build.apply_patch(fixture())
        self.assertEqual(report['auto_select_pinned'], 3)
        for g in build.AUTO_SELECT_GROUPS:
            want = "custom_proxy_group=%s`select`%s" % (g, build.SELF_REALITY_FILTER)
            self.assertIn(want, output, "%s 未被钉死" % g)
        # 不能再留 `.*` 那种通配
        for g in build.AUTO_SELECT_GROUPS:
            self.assertNotIn("custom_proxy_group=%s`url-test`" % g, output)
            self.assertNotIn("custom_proxy_group=%s`fallback`" % g, output)
            self.assertNotIn("custom_proxy_group=%s`load-balance`" % g, output)

    def test_missing_auto_group_fails_closed(self):
        """三个自动组缺失时必须报错，而不是静默生成一份"默认走机场"的配置。"""
        with self.assertRaisesRegex(RuntimeError, '自动选择'):
            build.apply_patch(fixture(with_auto=False))

    def test_ai_filter_is_ownership_based_not_brand_based(self):
        """AI 组按【归属】过滤（[SELF]），不再按"名字里有没有 AI/专线"。

        按品牌名过滤时，机场节点「🇯🇵 日本 IEPL 专线」会被吸进 AI 组，
        而 select 首位即默认出口 -> AI 出口 IP 悄悄变成机场节点。
        """
        output, report = build.apply_patch(fixture())
        self.assertEqual(build.AI_NODE_FILTER, '[[]SELF[]]')
        self.assertIn('custom_proxy_group=🔒 AI 专用`select`%s' % build.AI_NODE_FILTER, output)
        for brand in ('专线', 'Dedicated', '落地', '解锁', 'Claude', 'GPT'):
            self.assertNotIn(brand, build.AI_NODE_FILTER)

    def test_steam_download_direct_but_store_keeps_nodes(self):
        """Steam 三分：下载直连 / 商店社区（含登录）走节点。

        ⚠️ 不要"为了 Steam 不碰 VPS"把商店组也改成 DIRECT ——
           store.steampowered.com / steamcommunity.com 在国内基本打不开。
           真正减少 VPS 占用的是【下载】，它本来就是 DIRECT。
        """
        output, report = build.apply_patch(fixture())
        self.assertEqual(report['steam_download_direct'], 2)

        for g in ("🎮 游戏下载", "🎮 游戏平台"):
            self.assertIn("custom_proxy_group=%s`select`[]DIRECT" % g, output,
                          "%s 首位不是 DIRECT" % g)

        # 商店组必须保持上游的「🚀 节点选择」优先。
        # 行格式：custom_proxy_group=名`type`第一位成员`第二位成员…
        #   所以 split("`")[2] 才是第一位成员（[3] 已经是第二个了）
        store_lines = [l for l in output.splitlines()
                       if l.startswith("custom_proxy_group=🎮 Steam 商店/社区")]
        self.assertTrue(store_lines, "商店组丢失")
        first_member = store_lines[0].split("`")[2]
        self.assertIn("节点选择", first_member,
                      "商店组首位被改成了 %r —— 国内会打不开" % first_member)

        # 「🔑 Steam 登录」已被移除；登录域名应回落到商店组
        self.assertNotIn("🔑 Steam 登录", output,
                         "登录组已决定去掉，不该再出现")
        self.assertNotIn("rules=DOMAIN,login.steampowered.com", output,
                         "登录规则已决定去掉（回落到 DOMAIN-SUFFIX,steampowered.com）")

    def test_missing_steam_group_fails_closed(self):
        """Steam 下载组缺失时必须报错，而不是静默留一份下载走代理的配置。"""
        with self.assertRaisesRegex(RuntimeError, '游戏下载'):
            build.apply_patch(fixture(with_steam=False))

    def test_other_rules_unchanged(self):
        output, _ = build.apply_patch(fixture())
        self.assertIn('ruleset=🎮 游戏下载,https://example.com/game.list', output)
        self.assertIn('ruleset=🎮 Steam 商店/社区,https://example.com/web.list', output)
        self.assertIn('custom_proxy_group=💬 Ai平台`select`[]🔒 AI 专用', output)
        self.assertIn('clash_rule_base=' + build.CLASH_RULE_BASE, output)

    def test_missing_group_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, '日本节点'):
            build.apply_patch(fixture(False))


class IsolationGroupTests(unittest.TestCase):
    """「🚀 自建节点 / ✈️ 机场节点」两个显式入口。

    这条线（给订阅转换站用的）比自建那条线更需要它们：产物里自建 2 个 +
    机场 46 个混在同一份订阅里，没有显式入口就只能从 48 项里靠名字认
    哪台是自己的机器 —— 选错的代价是 AI 出口漂到机场，属于账号风险。
    """

    def test_both_isolation_groups_are_added(self):
        output, report = build.apply_patch(fixture())
        self.assertEqual(report['isolation_groups_inserted'], 2)
        for g in build.ISOLATION_GROUPS:
            self.assertIn(g, output)

    def test_isolation_groups_are_select_and_never_auto_picking(self):
        """必须是 select —— 一旦是 url-test/fallback/load-balance 就"自己会换"。"""
        for g in build.ISOLATION_GROUPS:
            self.assertIn('`select`', g)
            for flapping in ('`url-test`', '`fallback`', '`load-balance`'):
                self.assertNotIn(flapping, g)

    def test_isolation_groups_use_filters_not_hardcoded_names(self):
        """成员得是过滤器/组引用，不能写死节点名（转换站会改名）。"""
        # 自建那一侧：用归属标识过滤器
        self.assertIn(build.AI_NODE_FILTER, build.ISOLATION_GROUPS[0])
        for g in build.ISOLATION_GROUPS:
            for member in g.split("`")[2:]:
                self.assertFalse(member.startswith("[]") and "SELF" in member,
                                 "写死了自建节点名：%r" % member)

    def test_airport_entry_references_region_groups_and_excludes_japan(self):
        """机场那一侧引用【只含机场节点】的地区组。

        为什么不用 `^(?!.*\\[SELF\\]).*`：subconverter-ng 不支持前瞻，实测匹配 0 个，
        组会静默退化成 [DIRECT]。为什么不用 `!!GROUPID=1`：同样实测无效，
        而且把语义绑在订阅顺序上。
        为什么不引用「🇯🇵 日本节点」：它【同时含自建节点】，放进来就不再是"机场那一侧"。
        """
        g = build.ISOLATION_GROUPS[1]
        for name in build.AIRPORT_REGION_GROUPS:
            self.assertIn("[]" + name, g)
        self.assertNotIn("[]🇯🇵 日本节点", g, "「日本节点」含自建节点，不能算机场入口")
        self.assertNotIn("(?!", g)

    def test_no_lookahead_anywhere_in_group_definitions(self):
        """禁止前瞻 —— 它的失败方式是【静默】的，必须钉成断言。

        实测：`(^(?!.*\\[SELF\\]).*)` 和 `(^(?!.*SELF).*)` 都匹配 0 个节点；
        而 subconverter 官方 README 里写着前瞻可用 —— 所以不能照文档推断。
        一个匹配 0 个的 select 组会退化成 `[DIRECT]`：配置能加载、看着正常，
        实际上整个组废掉，而且没有任何报错。
        """
        output, _ = build.apply_patch(fixture())
        for line in output.splitlines():
            if line.startswith("custom_proxy_group="):
                self.assertNotIn("(?!", line, "组定义里出现前瞻：%r" % line)
        for const in (build.SELF_REALITY_FILTER, build.SELF_HYSTERIA2_FILTER,
                      build.JAPAN_REGION_FILTER, build.AI_NODE_FILTER):
            self.assertNotIn("(?!", const, "常量里出现前瞻：%r" % const)

    def test_isolation_groups_are_placed_right_after_the_ai_group(self):
        output, _ = build.apply_patch(fixture())
        lines = output.splitlines()
        ai = next(i for i, l in enumerate(lines) if l.startswith("custom_proxy_group=🔒 AI 专用`"))
        got = [l.split("=", 1)[1].split("`", 1)[0] for l in lines[ai + 1:ai + 3]]
        want = [g.split("=", 1)[1].split("`", 1)[0] for g in build.ISOLATION_GROUPS]
        self.assertEqual(got, want, "两个入口必须紧跟「🔒 AI 专用」，方便一眼找到")

    def test_isolation_groups_are_not_referenced_by_any_group(self):
        """不被任何规则/组引用 —— 即使其中一个为空，也不影响任何流量走向。"""
        output, _ = build.apply_patch(fixture())
        names = [g.split("=", 1)[1].split("`", 1)[0] for g in build.ISOLATION_GROUPS]
        for name in names:
            users = [l for l in output.splitlines()
                     if l.startswith("custom_proxy_group=") and ("[]" + name) in l]
            self.assertEqual(users, [], "「%s」被引用了，空组时会连带出错" % name)


class AiPriorityKeywordTests(unittest.TestCase):
    """把落在微软/Azure 主机名上的 OpenAI 域拉回 AI 组。

    微软规则集（Microsoft.list → azure.com / windows.net / azureedge.net）
    声明在 AI 规则集【之前】，先匹配者胜 => 这些域会先被微软吃掉、
    落进「Ⓜ️ 微软服务」，而那一组首位是 DIRECT —— 于是 ChatGPT 的文件存储
    与实时/语音信令走真实 IP，且毫无报错。

    修法是最前面插一个只含 openai / chatgpt 两条关键字的规则集：
    只动那几个域，微软其余流量（Windows Update / Office / Teams）保持直连。
    """

    SIX = (
        "openaiapi-site.azureedge.net",
        "openaiassets.blob.core.windows.net",
        "openaicomproductionae4b.blob.core.windows.net",
        "production-openaicom-storage.azureedge.net",
        "openaipublic.blob.core.windows.net",
        "chatgpt-async-webps-prod-us-east-1.webpubsub.azure.com",
    )

    def test_priority_ruleset_sorted_before_microsoft(self):
        output, report = build.apply_patch(fixture())
        self.assertTrue(report['ai_priority_ruleset'].startswith("inserted-before"),
                        report['ai_priority_ruleset'])
        lines = output.splitlines()
        pri = next(i for i, l in enumerate(lines)
                   if l.strip() == "ruleset=💬 Ai平台,%s" % build.AI_PRIORITY_LIST)
        ms = next(i for i, l in enumerate(lines)
                  if l.strip().startswith(build.MICROSOFT_RULESET_PREFIX))
        self.assertLess(pri, ms, "AI 优先规则集必须排在微软规则集【之前】")

    def test_priority_list_actually_covers_all_six_domains(self):
        """逐个验那 6 个域都被关键字覆盖 —— 不靠"看起来对"。"""
        text = (ROOT / "base" / "ai-priority.list").read_text(encoding="utf-8")
        kws = [l.split(",", 1)[1].strip() for l in text.splitlines()
               if l.startswith("DOMAIN-KEYWORD,")]
        self.assertTrue(kws, "优先规则集里没有 DOMAIN-KEYWORD")
        for d in self.SIX:
            self.assertTrue(any(k in d for k in kws),
                            "%s 没有任何关键字能命中（关键字=%r）" % (d, kws))

    def test_missing_microsoft_ruleset_fails_closed(self):
        """找不到微软规则集必须报错 —— 静默跳过就等于留一份域名单仍直连的配置。"""
        with self.assertRaisesRegex(RuntimeError, '微软'):
            build.apply_patch(fixture(with_ms_ruleset=False))


class ConverterRenameImmunityTests(unittest.TestCase):
    """转换站会给节点改名，组引用不能因此悬空。

    ⚠️ 真实事故（FlClash 无法加载配置）：
        proxy group[3]: 日本节点: '🇯🇵 日本 [SELF] xtls-reality' not found
    因为转换站 append_type=true 产出的节点名其实是
        🇯🇵 [VLESS] 日本 [SELF] xtls-reality
    改名【只作用于节点名，不作用于组里写死的 `[]名字`】→ 引用悬空。
    对照组：「🔒 AI 专用」用的是过滤器，所以它一直正常 —— 这就是修法。
    """

    # 转换站可能产出的自建 Reality 节点名（append_type / emoji 各种组合）
    REALITY_NAMES = (
        SELF_REALITY_NAME,
        "🇯🇵 [VLESS] 日本 [SELF] xtls-reality",
        "日本 [SELF] xtls-reality",
        "[VLESS] 日本 [SELF] xtls-reality",
    )
    # 不该被 Reality 过滤器命中的
    NON_REALITY_NAMES = (
        "🇯🇵 日本 [SELF] hysteria2",
        "🇯🇵 [HYSTERIA2] 日本 [SELF] hysteria2",
        "🇭🇰 [SS] 香港S01",
        "🇯🇵 [SS] 日本S01 | IEPL",
        "🇯🇵 日本 IEPL 专线",
    )

    def test_reality_filter_matches_every_renamed_form(self):
        rx = re.compile(build.SELF_REALITY_FILTER)
        for name in self.REALITY_NAMES:
            self.assertTrue(rx.search(name), "改名后匹配不到：%r" % name)
        for name in self.NON_REALITY_NAMES:
            self.assertFalse(rx.search(name), "不该命中：%r" % name)

    def test_hysteria2_filter_matches_every_renamed_form(self):
        rx = re.compile(build.SELF_HYSTERIA2_FILTER)
        for name in ("🇯🇵 日本 [SELF] hysteria2",
                     "🇯🇵 [HYSTERIA2] 日本 [SELF] hysteria2"):
            self.assertTrue(rx.search(name), "改名后匹配不到：%r" % name)
        for name in self.REALITY_NAMES + ("🇭🇰 [SS] 香港S01",):
            self.assertFalse(rx.search(name), "不该命中：%r" % name)

    def test_filters_have_no_literal_whitespace(self):
        """过滤器里不能出现字面空格。

        assets/acl4ssr_build.py 的 normalize_filter_pattern 会做
        re.sub(r"\\s+", "", body) —— 写 "日本 [SELF] xtls-reality" 会被压成
        "日本[SELF]xtls-reality"，一个节点都匹配不到、组直接空掉。
        """
        for pat in (build.SELF_REALITY_FILTER, build.SELF_HYSTERIA2_FILTER):
            self.assertNotRegex(pat, r"\s", "过滤器里不能有字面空格：%r" % pat)
            # [SELF] 必须转义，否则 `[SELF]` 是字符类（匹配 S/E/L/F 之一）
            self.assertNotIn("[SELF]", pat, "裸 [SELF] 是字符类，要写 \\[SELF\\]")

    def test_no_group_hardcodes_a_self_hosted_node_name(self):
        """任何组都不许写死自建节点名 —— 这是上面那起事故的根因类别。"""
        output, _ = build.apply_patch(fixture())
        offenders = []
        for line in output.splitlines():
            if not line.startswith("custom_proxy_group="):
                continue
            # 行格式：custom_proxy_group=名`type`成员`成员…
            for member in line.split("=", 1)[1].split("`")[2:]:
                if member.startswith("[]") and "[SELF]" in member:
                    offenders.append(member)
        self.assertEqual(offenders, [],
                         "组里写死了自建节点名，转换站改名后会悬空：%r" % offenders)


if __name__ == '__main__':
    unittest.main()
