import subprocess
import tempfile
import unittest
from pathlib import Path

from mygerrit_cli.common import CliError
from mygerrit_cli.gerrit import preview_upload


def git(repo, *argv):
    return subprocess.check_output(['git', '-C', str(repo)] + list(argv), stderr=subprocess.DEVNULL).decode().strip()


class RepoFixture:
    def __init__(self, root):
        self.repo, self.remote = Path(root) / 'repo with spaces', Path(root) / 'remote.git'
        self.repo.mkdir()
        git(self.repo, 'init', '-q')
        git(self.repo, 'symbolic-ref', 'HEAD', 'refs/heads/main')
        git(self.repo, 'config', 'user.name', 'Test')
        git(self.repo, 'config', 'user.email', 'test@example.invalid')
        (self.repo / 'file').write_text('base')
        git(self.repo, 'add', 'file')
        git(self.repo, 'commit', '-qm', 'base')
        self.base = git(self.repo, 'rev-parse', 'HEAD')
        subprocess.run(['git', 'init', '--bare', '-q', str(self.remote)], check=True)
        git(self.repo, 'remote', 'add', 'origin', str(self.remote))
        git(self.repo, 'push', '-q', 'origin', 'main')
        self.commit = self.add_commit('fix\n\nChange-Id: I' + 'a' * 40)

    def add_commit(self, message):
        with (self.repo / 'file').open('a') as f:
            f.write('\nfix')
        git(self.repo, 'commit', '-qam', message)
        return git(self.repo, 'rev-parse', 'HEAD')

    def preview(self, **kwargs):
        values = dict(repo=str(self.repo), remote='origin', branch='main', base_sha=self.base,
                      commit_sha=self.commit, topic='BUG-1', project='platform/base')
        values.update(kwargs)
        return preview_upload(**values)


class GerritPreviewTests(unittest.TestCase):
    def test_remote_helper_is_rejected_before_it_executes(self):
        with tempfile.TemporaryDirectory() as d:
            r = RepoFixture(d)
            marker = Path(d) / 'executed'
            helper = Path(d) / 'helper'
            helper.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n')
            helper.chmod(0o755)
            git(r.repo, 'config', 'protocol.ext.allow', 'always')
            git(r.repo, 'remote', 'set-url', '--push', 'origin', 'ext::' + str(helper))
            with self.assertRaises(CliError):
                r.preview()
            self.assertFalse(marker.exists())

    def test_preview_pins_the_validated_push_url(self):
        with tempfile.TemporaryDirectory() as d:
            r = RepoFixture(d)
            preview = r.preview()
            self.assertIn(str(r.remote), preview['push_argv'])

    def test_follow_tags_config_cannot_expand_push(self):
        with tempfile.TemporaryDirectory() as d:
            r = RepoFixture(d)
            git(r.repo, 'config', 'push.followTags', 'true')
            git(r.remote, 'config', 'receive.advertisePushOptions', 'true')
            git(r.repo, 'tag', '-am', 'not approved', 'unapproved', r.base)
            command = r.preview()['push_argv']
            command.insert(command.index('push') + 1, '--dry-run')
            out = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertNotIn(b'refs/tags/unapproved', out.stdout + out.stderr)
            self.assertIn('--recurse-submodules=no', command)

    def test_replace_parent_cannot_hide_extra_commit(self):
        with tempfile.TemporaryDirectory() as d:
            r = RepoFixture(d)
            extra = r.add_commit('extra\n\nChange-Id: I' + 'b' * 40)
            git(r.repo, 'replace', '--graft', extra, r.base)
            with self.assertRaises(CliError):
                r.preview(commit_sha=extra)

    def test_preview_never_pushes(self):
        with tempfile.TemporaryDirectory() as d:
            r = RepoFixture(d)
            before = git(r.remote, 'show-ref')
            preview = r.preview()
            self.assertEqual(git(r.remote, 'show-ref'), before)
            self.assertEqual(preview['commit_sha'], r.commit)
            self.assertIn(r.commit + ':refs/for/main', preview['push_argv'])
            self.assertNotIn('refs/heads/main', preview['push_argv'])

    def test_extra_commits_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            r = RepoFixture(d)
            extra = r.add_commit('extra\n\nChange-Id: I' + 'b' * 40)
            with self.assertRaises(CliError):
                r.preview(commit_sha=extra)

    def test_direct_branch_push_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            r = RepoFixture(d)
            with self.assertRaises(CliError):
                r.preview(branch='refs/heads/main')

    def test_invalid_ref_and_shell_metacharacters_not_executed(self):
        with tempfile.TemporaryDirectory() as d:
            r = RepoFixture(d)
            with self.assertRaises(CliError):
                r.preview(branch='bad branch')
            preview = r.preview(topic='BUG-1;echo surprise')
            self.assertIn('topic=BUG-1;echo surprise', preview['push_argv'])
            self.assertFalse((r.repo / 'surprise').exists())

    def test_missing_change_id_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            r = RepoFixture(d)
            git(r.repo, 'commit', '--amend', '-qm', 'no footer')
            with self.assertRaises(CliError):
                r.preview(commit_sha=git(r.repo, 'rev-parse', 'HEAD'))
