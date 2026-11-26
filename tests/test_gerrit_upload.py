import tempfile
import unittest
from unittest.mock import patch

from mygerrit_cli.common import CliError
from mygerrit_cli.gerrit import Gerrit
from tests.helpers import ScriptedClient


class GerritUploadTests(unittest.TestCase):
    def fixture(self):
        preview = {'repo': '/tmp/repo', 'remote': 'origin', 'branch': 'main', 'project': 'platform/base',
                   'base_sha': 'b' * 40, 'commit_sha': 'c' * 40, 'topic': 'BUG-1', 'change_id': 'I' + 'a' * 40,
                   'push_argv': ['git', '-C', '/tmp/repo', 'push', '--', 'origin', 'c' * 40 + ':refs/for/main']}
        state = {'revision': 'old'}
        def query(m, p, q, body):
            return {'project': 'platform/base', 'branch': 'main', 'change_id': 'I' + 'a' * 40,
                    '_number': 123, 'status': 'NEW', 'current_revision': state['revision'],
                    'revisions': {state['revision']: {'_number': 2}}}
        return preview, state, Gerrit(ScriptedClient(query))

    def test_push_timeout_remote_success_is_confirmed(self):
        preview, state, g = self.fixture()
        def push(*a, **k):
            state['revision'] = preview['commit_sha']
            raise CliError('RESULT_UNKNOWN', 'lost push response')
        with tempfile.TemporaryDirectory() as d, patch('mygerrit_cli.gerrit.preview_upload', return_value=preview), patch('mygerrit_cli.gerrit.git_run', side_effect=push) as runner:
            out = g.upload(preview, 'op', d)
            self.assertEqual(out['status'], 'SUCCESS')
            self.assertEqual(out['data']['patch_set'], 2)
            self.assertEqual(runner.call_count, 1)

    def test_revision_mismatch_stays_unknown(self):
        preview, state, g = self.fixture()
        with tempfile.TemporaryDirectory() as d, patch('mygerrit_cli.gerrit.preview_upload', return_value=preview), patch('mygerrit_cli.gerrit.git_run', return_value='') as runner:
            self.assertEqual(g.upload(preview, 'op', d)['status'], 'UNKNOWN')
            self.assertEqual(g.upload(preview, 'op', d)['status'], 'UNKNOWN')
            self.assertEqual(runner.call_count, 1)

    def test_retry_reuses_receipt(self):
        preview, state, g = self.fixture()
        state['revision'] = preview['commit_sha']
        with tempfile.TemporaryDirectory() as d, patch('mygerrit_cli.gerrit.preview_upload', return_value=preview), patch('mygerrit_cli.gerrit.git_run', return_value='') as runner:
            first = g.upload(preview, 'op', d)
            second = g.upload(preview, 'op', d)
            self.assertEqual(first['data']['revision'], second['data']['revision'])
            self.assertEqual(runner.call_count, 1)

    def test_existing_change_gets_expected_patchset(self):
        preview, state, g = self.fixture()
        state['revision'] = preview['commit_sha']
        with tempfile.TemporaryDirectory() as d, patch('mygerrit_cli.gerrit.preview_upload', return_value=preview), patch('mygerrit_cli.gerrit.git_run', return_value=''):
            out = g.upload(preview, 'op', d)
            self.assertEqual(out['data']['number'], 123)
            self.assertEqual(out['data']['project'], 'platform/base')

    def test_modified_preview_is_rejected_before_push(self):
        preview, state, g = self.fixture()
        with tempfile.TemporaryDirectory() as d, patch('mygerrit_cli.gerrit.preview_upload', return_value=dict(preview, topic='another')), patch('mygerrit_cli.gerrit.git_run') as runner:
            out = g.upload(preview, 'op', d)
            self.assertEqual(out['status'], 'FAILED')
            runner.assert_not_called()
