"""AI routing contract; optional real-core test uses loopback only."""
import http.client
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml
import build

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('compiler', ROOT / 'assets/acl4ssr_build.py')
compiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compiler)


def groups_for(names, overlay=False):
    lines = build.AI_GROUP_REPLACEMENT
    if overlay:
        lines = [s for s in (ROOT / 'assets/local-overlay.ini').read_text('utf-8').splitlines()
                 if s.startswith(('custom_proxy_group=💬 Ai平台`', 'custom_proxy_group=🔒 AI专用`'))]
    stats = compiler.make_stats()
    parts = compiler.parse_ini('\n'.join(lines), stats)
    parsed = dict(zip(('groups', 'rulesets', 'inline_rules'), parts))
    groups, _, _ = compiler.build_groups(parsed, names, stats)
    text = 'proxy-groups:\n' + '\n'.join(
        line for group in groups
        for line in compiler.emit_group(group, '', 300, [], empty_placeholder='unrelated-airport'))
    return yaml.safe_load(text)['proxy-groups']


class AiContractTests(unittest.TestCase):
    def test_no_self_is_reject_not_direct_or_airport(self):
        for overlay in (False, True):
            groups = {g['name']: g for g in groups_for(['airport'], overlay)}
            dedicated = next(g for n, g in groups.items() if n.startswith('🔒'))
            self.assertEqual(dedicated['proxies'], ['REJECT'])
            self.assertNotIn('🚀 手动切换', groups['💬 Ai平台']['proxies'])

    def test_self_stays_first_and_reject_is_last(self):
        for overlay in (False, True):
            groups = groups_for(['airport', '[SELF] primary'], overlay)
            dedicated = next(g for g in groups if g['name'].startswith('🔒'))
            self.assertEqual(dedicated['proxies'], ['[SELF] primary', 'REJECT'])

    def test_missing_policy_is_reject(self):
        stats = compiler.make_stats()
        parts = compiler.parse_ini('rules=DOMAIN,claude.ai,missing-ai-group', stats)
        parsed = dict(zip(('groups', 'rulesets', 'inline_rules'), parts))
        groups, _, _ = compiler.build_groups(parsed, ['airport'], stats)
        self.assertEqual(groups[0]['members'], ['REJECT'])

    def test_priority_ruleset_precedes_even_direct_rulesets(self):
        from test_japan_fallback import fixture
        source = fixture().replace('[custom]', '[custom]\nruleset=DIRECT,https://example.com/direct.list')
        output, _ = build.apply_patch(source)
        first = next(line for line in output.splitlines() if line.startswith('ruleset='))
        self.assertEqual(first, 'ruleset=💬 Ai平台,' + build.AI_PRIORITY_LIST)

    def test_priority_rules_and_overlay_are_in_sync(self):
        overlay = (ROOT / 'assets/local-overlay.ini').read_text('utf-8')
        rules = [r for r in (ROOT / 'base/ai-priority.list').read_text('utf-8').splitlines()
                 if r and not r.startswith('#')]
        self.assertGreaterEqual(len(rules), 27)
        for rule in rules:
            fields = rule.split(',')
            fields.insert(2, '💬 Ai平台')
            self.assertIn('rules=' + ','.join(fields), overlay)


def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@unittest.skipUnless(os.environ.get('MIHOMO_BIN'), 'set MIHOMO_BIN for real-core test')
class AiRuntimeTests(unittest.TestCase):
    def test_live_node_then_offline_and_empty_group_never_reaches_direct_target(self):
        hits = {'target': 0, 'proxy': 0}

        def handler(kind):
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    hits[kind] += 1
                    body = kind.encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *args):
                    pass
            return Handler

        target = ThreadingHTTPServer(('127.0.0.1', 0), handler('target'))
        upstream = ThreadingHTTPServer(('127.0.0.1', 0), handler('proxy'))
        for server in (target, upstream):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        upstream_port = upstream.server_port
        upstream_stopped = False
        try:
            for empty in (False, True):
                with tempfile.TemporaryDirectory() as directory:
                    proxy_port = port()
                    node = {'name': '[SELF] primary', 'type': 'http', 'server': '127.0.0.1',
                            'port': upstream_port}
                    config = {'mixed-port': proxy_port, 'bind-address': '127.0.0.1',
                              'mode': 'rule', 'log-level': 'debug', 'ipv6': False,
                              'dns': {'enable': False},
                              'hosts': {'claude.test': '127.0.0.1', 'control.test': '127.0.0.1'},
                              'proxies': [] if empty else [node],
                              'proxy-groups': groups_for([] if empty else [node['name']]),
                              'rules': ['DOMAIN,claude.test,💬 Ai平台', 'MATCH,DIRECT']}
                    path = Path(directory) / 'config.yaml'
                    path.write_text(yaml.safe_dump(config, allow_unicode=True), 'utf-8')
                    with (Path(directory) / 'core.log').open('w+', encoding='utf-8') as log:
                        proc = subprocess.Popen([os.environ['MIHOMO_BIN'], '-d', directory,
                                                 '-f', str(path)], stdout=log, stderr=log)
                        try:
                            for _ in range(100):
                                try:
                                    with socket.create_connection(('127.0.0.1', proxy_port), .1):
                                        break
                                except OSError:
                                    if proc.poll() is not None:
                                        log.seek(0)
                                        self.fail(log.read())
                                    time.sleep(.05)
                            else:
                                self.fail('isolated core did not start')

                            def request(host):
                                c = http.client.HTTPConnection('127.0.0.1', proxy_port, timeout=5)
                                try:
                                    c.request('GET', f'http://{host}:{target.server_port}/probe',
                                              headers={'Connection': 'close'})
                                    r = c.getresponse()
                                    return r.status, r.read()
                                except (OSError, http.client.HTTPException):
                                    return None, b''
                                finally:
                                    c.close()

                            # Prove the direct sink is reachable; a hidden DIRECT fallback would hit it.
                            self.assertEqual(request('control.test'), (200, b'target'))
                            before = hits['target']
                            if not empty:
                                self.assertEqual(request('claude.test'), (200, b'proxy'))
                                upstream.shutdown()
                                upstream.server_close()
                                upstream_stopped = True
                            for _ in range(3):
                                self.assertNotEqual(request('claude.test')[0], 200)
                            self.assertEqual(hits['target'], before,
                                             'AI request leaked to direct target')
                        finally:
                            proc.terminate()
                            proc.wait(timeout=10)
            self.assertEqual(hits['proxy'], 1)
        finally:
            target.shutdown()
            target.server_close()
            if not upstream_stopped:
                upstream.shutdown()
                upstream.server_close()
