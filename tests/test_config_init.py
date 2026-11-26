import base64
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml

from mygerrit_cli.gerrit_cli import main


class ProbeServer(BaseHTTPRequestHandler):
    paths = {'/a/accounts/self': {'_account_id': 42}, '/a/config/server/version': '3.9.0'}

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.headers.get('Authorization') not in ('Bearer sample-secret', 'Basic ' + base64.b64encode(b'tester: sample-secret ').decode()):
            self.send_error(401)
            return
        value = self.paths.get(self.path)
        if value is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(value).encode())


class ConfigInitTests(unittest.TestCase):
    def test_verified_init_and_doctor_with_default_config(self):
        with tempfile.TemporaryDirectory() as tmp, HTTPServer(('127.0.0.1', 0), ProbeServer) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                url = 'http://127.0.0.1:' + str(server.server_port)
                path = Path(tmp) / 'mygerrit-cli' / 'config.json'

                def initialize(name=''):
                    answers = io.StringIO(url + '\nbearer\nGERRIT_TOKEN\n' + name + '\n')
                    answers.isatty = lambda: True
                    output = io.StringIO()
                    with patch('sys.stdin', answers), contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                        status = main(['config', 'init', '--auth-env'])
                    return status, json.loads(output.getvalue())

                with patch.dict(os.environ, {'XDG_CONFIG_HOME': tmp, 'GERRIT_TOKEN': 'wrong',
                                             'ISSUE_RESOLVER_CONFIG': ''}):
                    status, failed = initialize()
                    self.assertNotEqual(status, 0, failed)
                    self.assertFalse(path.exists())
                    os.environ['GERRIT_TOKEN'] = 'sample-secret'
                    status, initialized = initialize()
                    self.assertEqual(status, 0, initialized)
                    saved = json.loads(path.read_text())
                    self.assertNotIn('sample-secret', path.read_text())
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(main(['doctor']), 0, output.getvalue())
                    loaded = json.loads(output.getvalue())
                    self.assertEqual(loaded['data']['account_id'], 42)
                    second_status, second = initialize('framework')
                    self.assertEqual(second_status, 0, second)
                    configs = json.loads(path.read_text())['gerrits']
                    self.assertEqual(set(configs), {'default', 'framework'})
                    self.assertEqual(configs['default'], saved['gerrit'])
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(['--server', 'framework', 'doctor']), 0)
            finally:
                server.shutdown()
                worker.join()

    def test_hidden_basic_and_bearer_round_trip_with_yaml_doctor(self):
        for mode, field in (('basic', 'password_file'), ('bearer', 'token_file')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, HTTPServer(('127.0.0.1', 0), ProbeServer) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    url = 'http://127.0.0.1:' + str(server.server_port)
                    argv = ['--server', 'test', 'config', 'init']
                    path = Path(tmp) / 'mygerrit-cli' / 'config.json'

                    def initialize(secret):
                        answers = io.StringIO(url + '\n' + mode + '\n' + ('tester\n' if mode == 'basic' else ''))
                        answers.isatty = lambda: True
                        out, err = io.StringIO(), io.StringIO()
                        with patch('sys.stdin', answers), patch('getpass.getpass', return_value=secret) as prompt:
                            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                                code = main(argv)
                        prompt.assert_called_once()
                        self.assertNotIn(secret, out.getvalue() + err.getvalue())
                        return code

                    with patch.dict(os.environ, {'XDG_CONFIG_HOME': tmp, 'ISSUE_RESOLVER_CONFIG': ''}):
                        self.assertNotEqual(initialize('bad-secret'), 0)
                        self.assertFalse(path.exists())
                        self.assertFalse(list(path.parent.glob('gerrit-credential-*')))
                        secret = ' sample-secret ' if mode == 'basic' else 'sample-secret'
                        self.assertEqual(initialize(secret), 0)
                        auth = json.loads(path.read_text())['gerrits']['test']['auth']
                        self.assertNotIn('sample-secret', path.read_text())
                        credential = Path(auth[field])
                        self.assertEqual(credential.read_text(), secret)
                        self.assertEqual(credential.stat().st_mode & 0o777, 0o600)
                        results = []
                        for fmt, decode in (('json', json.loads), ('yaml', yaml.safe_load)):
                            out = io.StringIO()
                            with contextlib.redirect_stdout(out):
                                self.assertEqual(main(['--server', 'test', 'doctor', '--format', fmt]), 0)
                            results.append(decode(out.getvalue()))
                        self.assertEqual(results[0], results[1])
                        self.assertEqual(results[0]['data']['account_id'], 42)
                finally:
                    server.shutdown()
                    worker.join()
