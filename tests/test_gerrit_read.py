import unittest
from urllib.parse import unquote

from mygerrit_cli.common import CliError
from mygerrit_cli.http import Client
from mygerrit_cli.gerrit import Gerrit
from tests.helpers import ScriptedClient, Response, Opener


class GerritReadTests(unittest.TestCase):
    def test_limit_caps_total_across_pages(self):
        def page(m, p, q, b):
            start = q['S']
            return [{'_number': start + i + 1, '_more_changes': i == 1} for i in range(2)]
        client = ScriptedClient(page)
        out = Gerrit(client).query('owner:self', limit=3)
        self.assertEqual([x['_number'] for x in out], [1, 2, 3])
        self.assertEqual(len(client.calls), 2)

    def test_xssi_json_and_more_changes(self):
        c = Client('https://review.test', {'type': 'none'}, 1)
        c.opener = Opener(Response(b")]}'\n[{\"_number\":1}]", url='https://review.test/changes/'))
        self.assertEqual(Gerrit(c).query('status:open')[0]['_number'], 1)
        def page(m, p, q, b):
            return [{'_number': 1, '_more_changes': True}] if q['S'] == 0 else [{'_number': 2}]
        self.assertEqual(len(Gerrit(ScriptedClient(page)).query('status:open')), 2)

    def test_change_id_scoped_to_project_branch(self):
        def handler(m, p, q, b):
            self.assertIn('platform/base~release/one~I' + 'a' * 40, unquote(p))
            return {'project': 'platform/base', 'branch': 'release/one', 'change_id': 'I' + 'a' * 40, 'current_revision': 'c' * 40, 'status': 'NEW'}
        c = ScriptedClient(handler)
        out = Gerrit(c).get_change('platform/base', 'release/one', 'I' + 'a' * 40)
        self.assertEqual(out['status'], 'NEW')

    def test_html_is_not_empty_search(self):
        c = Client('https://review.test', {'type': 'none'}, 1)
        c.opener = Opener(Response(b'<html>login</html>', content_type='text/html'))
        with self.assertRaises(CliError):
            Gerrit(c).query('status:open')

    def test_wrong_identity_is_rejected(self):
        c = ScriptedClient(lambda *a: {'project': 'wrong', 'branch': 'main', 'change_id': 'I' + 'a' * 40})
        with self.assertRaises(CliError):
            Gerrit(c).get_change('right', 'main', 'I' + 'a' * 40)
