"""Real loopback sockets using an address treated as remote by URL policy."""
import base64
import errno
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mygerrit_cli.common import write_json_atomic


class GerritHttpIntegrationTests(unittest.TestCase):
    def test_cli_http_and_separate_credential_permission(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append((self.path, self.headers.get('Authorization')))
                raw = b")]}'\n[]"
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        try:
            server = ThreadingHTTPServer(('127.0.0.2', 0), Handler)
        except PermissionError as exc:
            if exc.errno == errno.EPERM:
                self.skipTest('Environment prohibits sockets; run on host.')
            raise
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                cfg = Path(directory) / 'config.json'
                service = {'base_url': 'http://127.0.0.2:' + str(server.server_port)}
                env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'),
                           NO_PROXY='*', no_proxy='*', TEST_GERRIT_PASSWORD='fixture-password')

                def cli(expected, module='gerrit_cli'):
                    write_json_atomic(str(cfg), {'gerrit': service, 'jira': service})
                    args = ['changes', 'list', '--query', 'status:open'] if module == 'gerrit_cli' else ['me']
                    p = subprocess.run([sys.executable, '-m', 'mygerrit_cli.' + module, '--config', str(cfg)] + args,
                                       env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                    self.assertEqual(p.returncode, expected, p.stdout.decode() + p.stderr.decode())
                    return json.loads(p.stdout)

                self.assertEqual(cli(2)['status'], 'FAILED')
                self.assertEqual(seen, [])
                service['transport'] = {'allow_http': True}
                self.assertEqual(cli(0)['data'], [])
                self.assertIsNone(seen[-1][1])
                service['auth'] = {'type': 'basic', 'username': 'fixture-user', 'password_env': 'TEST_GERRIT_PASSWORD'}
                cli(2)
                self.assertEqual(len(seen), 1)
                service['transport']['allow_http_credentials'] = True
                self.assertEqual(cli(0)['data'], [])
                self.assertTrue(seen[-1][0].startswith('/a/changes/'))
                self.assertEqual(seen[-1][1], 'Basic ' + base64.b64encode(b'fixture-user:fixture-password').decode())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
