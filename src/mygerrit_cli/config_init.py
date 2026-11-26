"""交互式初始化独立连接配置。"""
import getpass
import os
import re
import sys
import tempfile
import warnings
from pathlib import Path
from urllib.parse import urlsplit

from .common import CliError, file_lock, load_config, result, write_json_atomic
from .http import Client

TOOL = 'mygerrit-cli'


def standalone_config():
    root = os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config'
    return Path(root) / TOOL / 'config.json'


def config_path(explicit):
    # 显式工作流配置优先。尚未初始化时兼容原有 project.json 默认值。
    configured = explicit or os.environ.get('ISSUE_RESOLVER_CONFIG')
    if configured:
        return configured
    path = standalone_config()
    return str(path) if path.exists() else 'project.json'


def ask(label):
    print(label + ': ', end='', file=sys.stderr, flush=True)
    line = sys.stdin.readline()
    if not line:
        raise CliError('INPUT', 'Initialization ended before all answers were provided.')
    return line.strip()


def env_ref(label):
    name = ask(label)
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
        raise CliError('INPUT', 'Enter an environment variable name.')
    return name


def read_hidden(label):
    # 无法关闭回显时，在 getpass 回退到普通输入之前中止。
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            return getpass.getpass(label)
    except (getpass.GetPassWarning, EOFError, OSError):
        raise CliError('INPUT', 'Cannot read a hidden credential from this terminal.')


def auth_config(use_env):
    mode = ask('Authentication (bearer/basic)')
    if mode == 'bearer':
        if use_env:
            return {'type': mode, 'token_env': env_ref('Token environment variable')}, None
        return {'type': mode}, read_hidden('Gerrit token (hidden): ').strip()
    if mode == 'basic':
        if use_env:
            return {'type': mode, 'username_env': env_ref('Username environment variable'),
                    'password_env': env_ref('Password or API token environment variable')}, None
        return {'type': mode, 'username': ask('Gerrit username')}, read_hidden('Gerrit password (hidden): ')
    raise CliError('INPUT', 'Authentication must be bearer or basic.')


def save_credential(directory, credential):
    fd, filename = tempfile.mkstemp(prefix='gerrit-credential-', dir=str(directory))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(credential)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        Path(filename).unlink()
        raise
    return filename


def add_commands(commands):
    sub = commands.add_parser('config', help='Manage standalone connection configuration')
    init = sub.add_subparsers(dest='config_command', required=True).add_parser(
        'init', help='Verify a connection and save credentials by reference')
    init.add_argument('--auth-env', action='store_true', help='Use environment variable references')


def initialize(args):
    if not sys.stdin.isatty():
        raise CliError('INPUT', 'Run config init from an interactive terminal.')
    if args.config is not None:
        raise CliError('INPUT', 'config init saves to the standalone config and does not take --config.')
    path = standalone_config()
    if not path.is_absolute():
        raise CliError('INPUT', 'XDG_CONFIG_HOME must be an absolute path.')
    url = ask('Service base URL')
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise CliError('INPUT', 'Invalid service URL.')
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise CliError('INPUT', 'Enter an HTTP(S) REST base URL.')
    auth, credential = auth_config(args.auth_env)
    section = {'base_url': url.rstrip('/'), 'auth': auth}
    metadata, server = probe(args, section, parsed, credential)
    with file_lock(str(path) + '.lock'):
        config = load_config(str(path)) if path.exists() else {}
        target = target_section(config, server)
        if target[1] in target[0]:
            if ask('Update this existing connection? Type yes to confirm') != 'yes':
                raise CliError('CONFLICT', 'Existing configuration was left unchanged.')
            previous = target[0][target[1]]
            section = {**previous, **section}
            # 重新初始化时以这次验证的传输策略为准。
            if parsed.scheme != 'http':
                section.pop('transport', None)
        saved = None
        if credential is not None:
            field = 'token_file' if auth['type'] == 'bearer' else 'password_file'
            saved = save_credential(path.parent, credential)
            section['auth'][field] = saved
        target[0][target[1]] = section
        try:
            write_json_atomic(str(path), config)
        except BaseException:
            # 替换后若目录同步失败，仍保留已被配置引用的新凭据。
            if saved is not None:
                current = load_config(str(path)) if path.exists() else {}
                selected = current.get('gerrit', {}) if server is None else current.get('gerrits', {}).get(server, {})
                if selected.get('auth', {}).get(field) != saved:
                    Path(saved).unlink()
            raise
    return result('SUCCESS', {'config': str(path), 'server': server, **metadata})


def probe(args, section, parsed, credential):
    server = args.server or ask('Gerrit server name (blank for a single server)')
    if server and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', server):
        raise CliError('INPUT', 'Use letters, digits, dots, underscores or hyphens for the server name.')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        if ask('HTTP sends credentials unencrypted. Type yes to allow it for this origin') != 'yes':
            raise CliError('INPUT', 'Use HTTPS or explicitly confirm HTTP for this origin.')
        section['transport'] = {'allow_http': True, 'allow_http_credentials': True}
    return connection_info(section, args.timeout, credential), server or None


def connection_info(section, timeout, credential=None):
    client = Client(section['base_url'], section.get('auth', {'type': 'none'}), timeout,
                    transport=section.get('transport'), credential=credential)
    prefix = '/a' if client.authorization else ''
    identity = client.request('GET', prefix + '/accounts/self')
    if not isinstance(identity, dict) or type(identity.get('_account_id')) is not int:
        raise CliError('AUTH_REQUIRED', 'Gerrit did not return an identifiable current account.')
    version = client.request('GET', prefix + '/config/server/version')
    if not isinstance(version, str) or not version.strip():
        raise CliError('REMOTE_ERROR', 'Gerrit did not return a server version.')
    return {'version': version, 'account_id': identity['_account_id'], 'push_permissions': 'not_checked'}


def target_section(config, server):
    if server is None:
        if 'gerrits' in config:
            raise CliError('INPUT', 'Enter a server name for this multi-server configuration.')
        return config, 'gerrit'
    if 'gerrit' in config:
        if 'gerrits' in config:
            raise CliError('CONFLICT', 'Standalone configuration contains both single and named servers.')
        # 转为多服务配置时保留已验证的单服务及其配置。
        if server == 'default':
            raise CliError('INPUT', 'The existing single server will be named default. Choose another name.')
        config['gerrits'] = {'default': config.pop('gerrit')}
    return config.setdefault('gerrits', {}), server
