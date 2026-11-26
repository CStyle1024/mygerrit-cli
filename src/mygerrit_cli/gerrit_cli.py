"""Standalone Gerrit CLI."""
import json
import os
import sys
from pathlib import Path

from . import __version__
from .common import ToolInfoAction, CliError, EXIT_CODES, Parser, digest, load_config, result, gerrit_config
from .actions import load_action
from .http import Client
from .gerrit import Gerrit, preview_upload


def parser():
    p = Parser(prog='mygerrit-cli')
    p.add_argument('--server', help='Named Gerrit server from gerrits')
    p.add_argument('--version', action='version', version='mygerrit-cli ' + __version__)
    p.add_argument('--tool-info', action=ToolInfoAction, nargs=0, const=json.dumps({'schema_version': 1, 'distribution': 'mygerrit-cli', 'command': 'mygerrit-cli', 'version': __version__, 'result_schema': 1}))
    p.set_defaults(config=os.environ.get('ISSUE_RESOLVER_CONFIG', 'project.json'), format='json', timeout=30, state_dir=None)
    commands = p.add_subparsers(dest='command', required=True)
    changes = commands.add_parser('changes').add_subparsers(dest='verb', required=True)
    listing = changes.add_parser('list')
    listing.add_argument('--query', required=True)
    listing.add_argument('--limit', type=int, help='Maximum total results across all pages')
    change = commands.add_parser('change').add_subparsers(dest='verb', required=True)
    get = change.add_parser('get')
    for name in ('project', 'branch', 'change-id'):
        get.add_argument('--' + name, required=True)
    upload = commands.add_parser('upload')
    for name in ('repo', 'remote', 'branch', 'base', 'commit', 'topic'):
        upload.add_argument('--' + name, required=True)
    upload.add_argument('--project', help='Explicit Gerrit project, or gerrit.project in configuration')
    mode = upload.add_mutually_exclusive_group()
    mode.add_argument('--preview', action='store_true')
    mode.add_argument('--execute', action='store_true')
    upload.add_argument('--operation-id')
    upload.add_argument('--expected-preview-digest', help='Require the exact previously reviewed preview SHA-256')
    return p


def dispatch(args, config):
    cfg = gerrit_config(config, args.server)
    gerrit = Gerrit(Client(cfg['base_url'], cfg.get('auth', {'type': 'none'}), args.timeout,
                          transport=cfg.get('transport')))
    if args.command == 'upload':
        default_state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local' / 'state'))) / 'issue-resolver' / 'actions' / 'gerrit'
        state_dir = args.state_dir or cfg.get('state_dir') or config.get('state_dir') or str(default_state)
        if args.execute:
            prior = load_action(state_dir, args.operation_id)
            if prior is not None:
                saved = prior.get('recovery')
                if not isinstance(saved, dict):
                    raise CliError('RESULT_UNKNOWN', 'Existing upload receipt lacks recovery metadata; inspect change details without pushing again.')
                expected = {'repo': str(Path(args.repo).resolve()), 'remote': args.remote, 'branch': args.branch,
                            'base_sha': args.base.lower(), 'commit_sha': args.commit.lower(), 'topic': args.topic,
                            'project': args.project or cfg.get('project')}
                if any(saved.get(k) != v for k, v in expected.items()) or prior.get('payload_hash') != digest({'service': cfg['base_url'].rstrip('/'), 'recovery': saved}):
                    raise CliError('CONFLICT', 'Upload arguments differ from the persisted operation; use the original inputs to reconcile.')
                if args.expected_preview_digest and digest(saved) != args.expected_preview_digest:
                    raise CliError('CONFLICT', 'Saved upload differs from the approved preview.')
                return gerrit.upload(saved, args.operation_id, state_dir)
        data = preview_upload(args.repo, args.remote, args.branch, args.base, args.commit, args.topic, args.project or cfg.get('project'))
        if args.expected_preview_digest and digest(data) != args.expected_preview_digest:
            raise CliError('CONFLICT', 'Current upload destination or content differs from the approved preview.')
        if args.execute:
            return gerrit.upload(data, args.operation_id, state_dir)
        return result('SUCCESS', data)
    if args.command == 'changes':
        return result('SUCCESS', gerrit.query(args.query, limit=args.limit))
    return result('SUCCESS', gerrit.get_change(args.project, args.branch, args.change_id))


def main(argv=None):
    args = None
    try:
        args = parser().parse_args(argv)
        if args.command == 'upload' and args.execute and not args.operation_id:
            raise CliError('INPUT', '--execute requires --operation-id for result reconciliation.')
        output = dispatch(args, load_config(args.config))
        code = 0 if output['status'] == 'SUCCESS' else (6 if output['status'] == 'UNKNOWN' else EXIT_CODES.get((output.get('error') or {}).get('code'), 5))
    except CliError as exc:
        code = EXIT_CODES.get(exc.code, 7)
        output = result('UNKNOWN' if code == 6 else 'FAILED', error=exc.as_dict())
    except SystemExit as exc:
        return int(exc.code or 0)
    except KeyboardInterrupt:
        code, output = 6, result('UNKNOWN', error={'code': 'RESULT_UNKNOWN', 'message': 'Interrupted; inspect receipts before retrying.'})
    except Exception:
        code, output = 7, result('FAILED', error={'code': 'LOCAL_ERROR', 'message': 'Local Gerrit operation failed; check configuration and inputs.'})
    print(json.dumps(output, ensure_ascii=False, indent=2 if args and args.format == 'text' else None))
    return code


if __name__ == '__main__':
    sys.exit(main())
