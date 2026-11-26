import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mygerrit_cli.common import CliError, digest, write_json_atomic
from mygerrit_cli.gerrit import Gerrit, git_run
from mygerrit_cli.gerrit_cli import main
from tests.helpers import invoke, ScriptedClient
from tests.test_gerrit_preview import RepoFixture


class GerritCliTests(unittest.TestCase):
    def test_unknown_receipt_cannot_be_reconciled_against_other_server(self):
        with tempfile.TemporaryDirectory() as d:
            repo = RepoFixture(d)
            config = str(Path(d) / 'config.json')
            write_json_atomic(config, {'gerrits': {'app': {'base_url': 'https://app.test'}, 'aosp': {'base_url': 'https://aosp.test'}}})
            saved = {'repo': str(repo.repo.resolve()), 'remote': 'origin', 'branch': 'main', 'base_sha': repo.base,
                     'commit_sha': repo.commit, 'topic': 'BUG-1', 'project': 'platform/base'}
            receipt = {'operation_id': 'op', 'status': 'UNKNOWN', 'recovery': saved,
                       'payload_hash': digest({'service': 'https://app.test', 'recovery': saved})}
            write_json_atomic(d + '/actions/' + digest('op') + '.json', receipt)
            argv = ['--config', config, '--server', 'aosp', '--state-dir', d + '/actions', 'upload', '--repo', str(repo.repo),
                    '--remote', 'origin', '--branch', 'main', '--project', 'platform/base', '--base', repo.base,
                    '--commit', repo.commit, '--topic', 'BUG-1', '--execute', '--operation-id', 'op']
            with patch('mygerrit_cli.gerrit_cli.preview_upload') as preview, patch('mygerrit_cli.gerrit_cli.Gerrit.upload') as upload:
                code, output, _ = invoke(main, argv)
            self.assertEqual(code, 4, output)
            preview.assert_not_called()
            upload.assert_not_called()

    def test_named_server_selects_requested_service(self):
        with tempfile.TemporaryDirectory() as d:
            config = str(Path(d) / 'config.json')
            write_json_atomic(config, {'gerrits': {'app': {'base_url': 'https://app.test'}, 'aosp': {'base_url': 'https://aosp.test'}}})
            client = ScriptedClient(lambda *args: [])
            with patch('mygerrit_cli.gerrit_cli.Client', return_value=client) as make:
                code, output, _ = invoke(main, ['--config', config, '--server', 'aosp', 'changes', 'list', '--query', 'owner:self', '--limit', '5'])
            self.assertEqual(code, 0, output)
            self.assertEqual(make.call_args.args[0], 'https://aosp.test')
            self.assertEqual(client.calls[0][2]['n'], 5)

    def test_multiple_servers_require_explicit_choice(self):
        with tempfile.TemporaryDirectory() as d:
            config = str(Path(d) / 'config.json')
            write_json_atomic(config, {'gerrits': {'app': {'base_url': 'https://app.test'}, 'aosp': {'base_url': 'https://aosp.test'}}})
            code, output, _ = invoke(main, ['--config', config, 'changes', 'list', '--query', 'owner:self'])
            self.assertEqual(code, 2, output)

    def test_expected_preview_digest_blocks_unapproved_destination(self):
        with tempfile.TemporaryDirectory() as d:
            repo = RepoFixture(d)
            config = str(Path(d) / 'config.json')
            write_json_atomic(config, {'gerrit': {'base_url': 'https://review.test', 'project': 'platform/base'}})
            with patch('mygerrit_cli.gerrit_cli.Gerrit.upload') as upload:
                code, out, _ = invoke(main, ['--config', config, 'upload', '--repo', str(repo.repo), '--remote', 'origin', '--branch', 'main',
                                            '--base', repo.base, '--commit', repo.commit, '--topic', 'BUG-1', '--execute', '--operation-id', 'op',
                                            '--expected-preview-digest', '0' * 64, '--state-dir', d + '/actions'])
                self.assertEqual(code, 4, out)
                upload.assert_not_called()

    def test_unknown_upload_reconciles_without_git_preflight(self):
        with tempfile.TemporaryDirectory() as d:
            repo = RepoFixture(d)
            config = str(Path(d) / 'config.json')
            write_json_atomic(config, {'gerrit': {'base_url': 'https://jira.test', 'project': 'platform/base', 'state_dir': d + '/actions'}})
            state = {'revision': 'old', 'pushes': 0}
            def details(*a):
                return {'project': 'platform/base', 'branch': 'main', 'change_id': 'I' + 'a' * 40,
                        'current_revision': state['revision'], '_number': 123, 'revisions': {state['revision']: {'_number': 1}}}
            client = ScriptedClient(details)
            g = Gerrit(client)
            argv = ['--config', config, 'upload', '--repo', str(repo.repo), '--remote', 'origin', '--branch', 'main',
                    '--base', repo.base, '--commit', repo.commit, '--topic', 'BUG-1', '--execute', '--operation-id', 'recover-op']
            def run(r, args, write=False, **kwargs):
                if write:
                    state['pushes'] += 1
                    return ''
                return git_run(r, args, **kwargs)
            with patch('mygerrit_cli.gerrit_cli.Gerrit', return_value=g), patch('mygerrit_cli.gerrit.git_run', side_effect=run):
                self.assertEqual(invoke(main, argv)[0], 6)
            calls = len(client.calls)
            state['revision'] = repo.commit
            with patch('mygerrit_cli.gerrit_cli.Gerrit', return_value=g), patch('mygerrit_cli.gerrit_cli.preview_upload', side_effect=CliError('PRECONDITION', 'Git unavailable')):
                code, out, _ = invoke(main, argv)
                self.assertEqual(code, 0, out)
                self.assertEqual(json.loads(out)['status'], 'SUCCESS')
            self.assertGreater(len(client.calls), calls)
            self.assertEqual(state['pushes'], 1)

    def test_preview_cli_keeps_remote_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            repo = RepoFixture(d)
            config = str(Path(d) / 'config.json')
            write_json_atomic(config, {'gerrit': {'base_url': 'https://review.test', 'project': 'platform/base'}})
            code, out, _ = invoke(main, ['upload', '--repo', str(repo.repo), '--remote', 'origin', '--branch', 'main', '--base', repo.base,
                                        '--commit', repo.commit, '--topic', 'BUG-1', '--config', config])
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)['data']['mode'], 'preview')

    def test_upload_requires_operation_id_before_client(self):
        with patch('mygerrit_cli.gerrit_cli.Client') as client:
            code, out, _ = invoke(main, ['upload', '--repo', '/tmp/repo', '--remote', 'origin', '--branch', 'main', '--base', 'b' * 40,
                                        '--commit', 'c' * 40, '--topic', 'BUG-1', '--execute'])
            self.assertEqual(code, 2)
            self.assertIn('operation', json.loads(out)['error']['message'])
            client.assert_not_called()

    def test_invalid_command_keeps_json_contract(self):
        code, out, _ = invoke(main, ['nonexistent'])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)['schema_version'], 1)
