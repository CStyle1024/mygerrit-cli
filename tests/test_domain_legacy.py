import json, shutil, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from mygerrit_cli.common import digest, write_json_atomic
from mygerrit_cli.gerrit_cli import main
from tests.helpers import invoke, ScriptedClient

class DomainLegacyTests(unittest.TestCase):
    def test_old_upload_receipts_through_cli_never_push(self):
        fixture = Path(__file__).parent / 'fixtures'
        for status in ('confirmed', 'unknown'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as d:
                op = 'legacy-gerrit-' + status
                saved = json.loads((fixture / ('gerrit-' + status + '.json')).read_text())
                shutil.copyfile(fixture / ('gerrit-' + status + '.json'), Path(d) / (digest(op) + '.json'))
                preview = saved['recovery']
                def query(*args):
                    return {'project':preview['project'],'branch':preview['branch'],'change_id':preview['change_id'],'_number':123,'current_revision':preview['commit_sha'],'revisions':{preview['commit_sha']:{'_number':2}}}
                client = ScriptedClient(query)
                config = str(Path(d) / 'config.json')
                write_json_atomic(config, {'gerrit': {'base_url':client.base_url},'state_dir':d})
                args = ['--config',config,'upload','--execute','--operation-id',op]
                for flag, value in [('repo',preview['repo']),('remote',preview['remote']),('branch',preview['branch']),('project',preview['project']),('base',preview['base_sha']),('commit',preview['commit_sha']),('topic',preview['topic'])]:
                    args += ['--'+flag,value]
                with patch('mygerrit_cli.gerrit_cli.Client',return_value=client), patch('mygerrit_cli.gerrit_cli.preview_upload',side_effect=AssertionError('must not preview old upload')), patch('mygerrit_cli.gerrit.git_run',side_effect=AssertionError('must not push again')):
                    code, out, err = invoke(main,args)
                self.assertEqual(code,0,out)
                self.assertEqual(json.loads(out)['status'],'SUCCESS')
