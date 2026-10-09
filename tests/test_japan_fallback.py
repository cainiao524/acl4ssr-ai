"""Tests for Japan fallback generator patch (run: python -m unittest discover -s tests)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build

def fixture(with_japan=True):
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
    return '\n'.join(lines) + '\n'

class JapanFallbackTests(unittest.TestCase):
    def test_ordered_fallback_and_other_rules_unchanged(self):
        output, report = build.apply_patch(fixture())
        self.assertTrue(report['japan_fallback_replaced'])
        self.assertIn(build.JAPAN_FALLBACK_GROUP, output)
        self.assertIn('`fallback`', build.JAPAN_FALLBACK_GROUP)
        self.assertLess(build.JAPAN_FALLBACK_GROUP.index('hysteria2'),
                        build.JAPAN_FALLBACK_GROUP.index('eality'))
        self.assertNotIn('🇯🇵 日本节点`url-test`', output)
        self.assertIn('ruleset=🎮 游戏下载,https://example.com/game.list', output)
        self.assertIn('ruleset=🎮 Steam 商店/社区,https://example.com/web.list', output)
        self.assertIn('custom_proxy_group=💬 Ai平台`select`[]🔒 AI 专用', output)
        self.assertIn('clash_rule_base=' + build.CLASH_RULE_BASE, output)

    def test_missing_group_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, '日本节点'):
            build.apply_patch(fixture(False))

if __name__ == '__main__':
    unittest.main()
