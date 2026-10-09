"""Tests for the Japan-group / AI-filter / auto-group pinning patches.

run: python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build

SELF = "🇯🇵 日本 [SELF] xtls-reality"


def fixture(with_japan=True, with_auto=True):
    lines = [
        '[custom]',
        build.ANCHOR,
        'custom_proxy_group=💬 Ai平台`select`[]DIRECT',
        'ruleset=🎮 游戏下载,https://example.com/game.list',
        'ruleset=🎮 Steam 商店/社区,https://example.com/web.list',
        ';clash_rule_base=https://example.com/base.yaml',
    ]
    if with_japan:
        lines.insert(3, 'custom_proxy_group=🇯🇵 日本节点`url-test`日本`http://example.com`300')
    if with_auto:
        lines.insert(4, 'custom_proxy_group=♻️ 自动选择`url-test`.*`http://example.com`300,,50')
        lines.insert(5, 'custom_proxy_group=🔯 故障转移`fallback`.*`http://example.com`300,,50')
        lines.insert(6, 'custom_proxy_group=🔮 负载均衡`load-balance`.*`http://example.com`300,,50')
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

        # 节点名必须带 [SELF]（自建/机场隔离的边界），且协议名在最后
        # （机器侧 sing-box 内核靠"剥掉末尾协议名"反推节点名）
        self.assertIn('🇯🇵 日本 [SELF] xtls-reality', build.JAPAN_FALLBACK_GROUP)
        self.assertIn('🇯🇵 日本 [SELF] hysteria2', build.JAPAN_FALLBACK_GROUP)
        self.assertNotIn('xtls-reality [SELF]', build.JAPAN_FALLBACK_GROUP)
        self.assertNotIn('hysteria2 [SELF]', build.JAPAN_FALLBACK_GROUP)

        self.assertNotIn('🇯🇵 日本节点`url-test`', output)

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
            want = "custom_proxy_group=%s`select`[]%s" % (g, SELF)
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

    def test_other_rules_unchanged(self):
        output, _ = build.apply_patch(fixture())
        self.assertIn('ruleset=🎮 游戏下载,https://example.com/game.list', output)
        self.assertIn('ruleset=🎮 Steam 商店/社区,https://example.com/web.list', output)
        self.assertIn('custom_proxy_group=💬 Ai平台`select`[]🔒 AI 专用', output)
        self.assertIn('clash_rule_base=' + build.CLASH_RULE_BASE, output)

    def test_missing_group_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, '日本节点'):
            build.apply_patch(fixture(False))


if __name__ == '__main__':
    unittest.main()
