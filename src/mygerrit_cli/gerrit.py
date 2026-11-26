"""Gerrit change operations; upload never bypasses review refs."""
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from .common import CliError, digest, result
from .actions import run_action


def git_run(repo, argv, write=False, timeout=30, raw=False, extra_env=None):
    command = ['git', '-C', str(repo)] + list(argv)
    environment = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull)
    for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE', 'GIT_NAMESPACE',
                 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_SHALLOW_FILE'):
        environment.pop(name, None)
    if extra_env:
        if set(extra_env) - {'GIT_INDEX_FILE'}:
            raise CliError('INPUT', 'Unsupported Git environment override.')
        environment.update(extra_env)
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=environment, start_new_session=True)
        try:
            out, _ = process.communicate(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise CliError('RESULT_UNKNOWN' if write else 'PRECONDITION', 'Git operation was interrupted; check remote result before retrying a write.')
    except OSError:
        raise CliError('LOCAL_ERROR', 'Cannot start Git.')
    if process.returncode:
        raise CliError('RESULT_UNKNOWN' if write else 'PRECONDITION', 'Git operation did not succeed.', {'returncode': process.returncode})
    return out if raw else out.decode('utf-8', 'replace').strip()


def preview_upload(repo: str, remote: str, branch: str, base_sha: str, commit_sha: str, topic: str, project=None) -> dict:
    repo = str(Path(repo).resolve())
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', remote):
        raise CliError('INPUT', 'Use a configured Git remote name.')
    if not branch or branch.startswith('refs/') or branch.startswith('-'):
        raise CliError('INPUT', 'Use a target branch name, not a refs/heads or refs/for path.')
    git_run(repo, ['check-ref-format', 'refs/heads/' + branch])
    for sha in (base_sha, commit_sha):
        if not re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', sha):
            raise CliError('INPUT', 'Specify full base and commit SHAs.')
    if not isinstance(project, str) or not project or any(x in project for x in ('~', '\n', '\r', '\x00')):
        raise CliError('INPUT', 'An explicit Gerrit project mapping is required.')
    if not isinstance(topic, str) or not topic or any(x in topic for x in ('\n', '\r', '\x00')):
        raise CliError('INPUT', 'A valid issue topic is required.')
    commit = git_run(repo, ['rev-parse', '--verify', commit_sha + '^{commit}'])
    base = git_run(repo, ['rev-parse', '--verify', base_sha + '^{commit}'])
    parents = git_run(repo, ['rev-list', '--parents', '-n', '1', commit]).split()
    if parents != [commit, base]:
        raise CliError('PRECONDITION', 'Only one non-merge candidate commit directly above base is allowed.')
    message = git_run(repo, ['show', '-s', '--format=%B', commit])
    footer = message.rsplit('\n\n', 1)[-1]
    identifiers = re.findall(r'^Change-Id: (I[0-9a-fA-F]{40})$', footer, re.MULTILINE)
    if len(identifiers) != 1:
        raise CliError('PRECONDITION', 'Candidate must have exactly one valid Change-Id footer.')
    urls = git_run(repo, ['remote', 'get-url', '--push', '--all', remote]).splitlines()
    if len(urls) != 1:
        raise CliError('PRECONDITION', 'Remote must have exactly one push URL.')
    if re.match(r'^[A-Za-z0-9+.-]+::', urls[0]):
        raise CliError('INPUT', 'Custom Git remote-helper URLs are not supported for bounded uploads.')
    parsed = urlsplit(urls[0])
    if parsed.password or parsed.query or parsed.fragment or '\n' in urls[0]:
        raise CliError('INPUT', 'Remote URL must not embed credentials or query tokens.')
    refs = git_run(repo, ['ls-remote', '--heads', urls[0], 'refs/heads/' + branch]).splitlines()
    matching = [line.split()[0] for line in refs if line.split()[-1:] == ['refs/heads/' + branch]]
    if len(matching) != 1:
        raise CliError('PRECONDITION', 'Target branch was not confirmed on the push remote.')
    git_run(repo, ['merge-base', '--is-ancestor', base, matching[0]])
    command = ['git', '-C', repo, '--no-replace-objects', 'push', '--porcelain', '--no-follow-tags', '--recurse-submodules=no',
               '-o', 'topic=' + topic, '--', urls[0], commit + ':refs/for/' + branch]
    return {'mode': 'preview', 'repo': repo, 'remote': remote, 'branch': branch, 'project': project,
            'base_sha': base, 'commit_sha': commit, 'commits': [commit], 'change_id': identifiers[0],
            'tree': git_run(repo, ['rev-parse', commit + '^{tree}']), 'topic': topic,
            'remote_fingerprint': digest(urls[0]), 'push_argv': command}


class Gerrit:
    def __init__(self, client):
        self.client = client
        self.prefix = '/a' if getattr(client, 'authorization', '') else ''

    def query(self, query: str, limit=None):
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise CliError('INPUT', 'Result limit must be a positive integer.')
        values, seen, start = [], set(), 0
        for _ in range(10000):
            page = self.client.request('GET', self.prefix + '/changes/', {'q': query, 'S': start, 'n': min(100, limit - len(values)) if limit else 100, 'o': ['CURRENT_REVISION', 'DETAILED_LABELS']})
            if not isinstance(page, list) or any(not isinstance(x, dict) for x in page):
                raise CliError('REMOTE_ERROR', 'Expected a Gerrit change list.')
            for item in page:
                key = item.get('_number')
                if key is None or key in seen:
                    raise CliError('REMOTE_ERROR', 'Gerrit pagination is missing identities or overlapping.')
                seen.add(key)
            values.extend(page[:limit - len(values)] if limit else page)
            if limit is not None and len(values) >= limit:
                return values
            if not page or not page[-1].get('_more_changes'):
                return values
            start += len(page)
        raise CliError('REMOTE_ERROR', 'Gerrit pagination exceeded the safety limit.')

    def get_change(self, project: str, branch: str, change_id: str) -> dict:
        identity = quote(project + '~' + branch + '~' + change_id, safe='')
        data = self.client.request('GET', self.prefix + '/changes/' + identity + '/detail', {'o': ['CURRENT_REVISION', 'DETAILED_LABELS']})
        if not isinstance(data, dict):
            raise CliError('REMOTE_ERROR', 'Expected Gerrit change details.')
        if any(data.get(k) != v for k, v in (('project', project), ('branch', branch), ('change_id', change_id))):
            raise CliError('CONFLICT', 'Gerrit change identity does not match project, branch and Change-Id.')
        return data

    def upload(self, preview: dict, operation_id: str, state_dir: str) -> dict:
        needed = ('repo', 'remote', 'branch', 'project', 'base_sha', 'commit_sha', 'topic', 'change_id', 'push_argv')
        if not isinstance(preview, dict) or any(k not in preview for k in needed):
            raise CliError('INPUT', 'Upload requires a complete preview.')

        def reconcile():
            for attempt in range(3):
                try:
                    change = self.get_change(preview['project'], preview['branch'], preview['change_id'])
                    revision = change.get('current_revision')
                    patchset = change.get('revisions', {}).get(revision, {}).get('_number')
                    number = change.get('_number')
                    if revision == preview['commit_sha'] and isinstance(number, int) and isinstance(patchset, int):
                        return result('SUCCESS', {'project': preview['project'], 'branch': preview['branch'],
                                                 'change_id': preview['change_id'], 'revision': revision,
                                                 'patch_set': patchset, 'number': number, 'status': change.get('status'),
                                                 'url': self.client.base_url + '/c/' + quote(preview['project'], safe='/') + '/+/' + str(number)})
                except CliError:
                    pass
                if attempt < 2:
                    time.sleep(0.05)
            return result('UNKNOWN', error={'code': 'RESULT_UNKNOWN', 'message': 'Expected project/branch/Change-Id revision was not confirmed; do not blindly push again.'})

        def execute():
            fresh = preview_upload(preview['repo'], preview['remote'], preview['branch'], preview['base_sha'],
                                   preview['commit_sha'], preview['topic'], preview['project'])
            if digest(fresh) != digest(preview):
                raise CliError('CONFLICT', 'Upload preview changed; generate and review a new preview.')
            try:
                git_run(fresh['repo'], fresh['push_argv'][3:], write=True)
            except CliError as exc:
                if exc.code not in ('RESULT_UNKNOWN', 'LOCAL_ERROR'):
                    raise
            return reconcile()

        return run_action(state_dir, operation_id, {'service': self.client.base_url, 'recovery': preview}, execute, reconcile)
