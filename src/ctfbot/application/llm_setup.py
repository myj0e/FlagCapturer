'''Temporary interactive ChatGPT/Codex model configuration flow.'''

from __future__ import annotations

import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from ctfbot.model_adapters.codex_app_server import CodexAppServer, CodexAppServerError, find_codex_cli


def llm_config_path() -> Path:
    return Path.cwd() / 'data' / 'llm.toml'


def read_llm_config() -> dict[str, Any] | None:
    path = llm_config_path()
    try:
        with path.open('rb') as stream:
            config = tomllib.load(stream)
    except FileNotFoundError:
        return None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f'Could not read {path}: {exc}') from exc
    provider = config.get('provider')
    model = config.get('model')
    effort = config.get('reasoning_effort')
    if (
        provider != 'codex-app-server'
        or not isinstance(model, str)
        or not model
        or (effort is not None and not isinstance(effort, str))
    ):
        raise ValueError(f'{path} does not contain a valid ctfbot Codex model configuration.')
    return config


def _save_llm_config(model: str, effort: str | None) -> Path:
    path = llm_config_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    rows = [
        'provider = "codex-app-server"',
        f'model = {json.dumps(model)}',
    ]
    if effort:
        rows.append(f'reasoning_effort = {json.dumps(effort)}')
    payload = '\n'.join(rows) + '\n'
    fd, temporary_name = tempfile.mkstemp(prefix='.llm-', suffix='.tmp', dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        if os.name == 'posix':
            os.chmod(path, 0o600)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
    return path


def _ask_yes_no(prompt: str, *, default: bool = False) -> bool:
    suffix = '[Y/n]' if default else '[y/N]'
    value = input(f'{prompt} {suffix} ').strip().lower()
    if not value:
        return default
    return value in {'y', 'yes'}


def _connect_account(server: CodexAppServer) -> dict[str, Any]:
    account = server.account()
    if account.get('type') == 'chatgpt':
        plan = account.get('planType')
        print(f'Codex ChatGPT login is active{f" ({plan})" if isinstance(plan, str) and plan else ""}.')
        return account

    current_type = account.get('type')
    if current_type:
        print(f'Current Codex authentication mode: {current_type}.')
        if not _ask_yes_no(
            'Switch the shared Codex CLI profile to ChatGPT sign-in? This can affect other Codex CLI tools.'
        ):
            raise CodexAppServerError('Setup cancelled; current Codex authentication was left unchanged.')
    else:
        print('No active ChatGPT login was found in the local Codex profile.')

    print('Choose sign-in method:')
    print('  1. Browser sign-in')
    print('  2. Device code (for headless/browser callback issues)')
    method = input('Method [1]: ').strip()
    if method not in {'', '1', '2'}:
        raise ValueError('Choose 1 for browser sign-in or 2 for device-code sign-in.')
    device_code = method == '2'
    return server.login_chatgpt(device_code=device_code)


def _select_model(models: list[dict[str, Any]]) -> tuple[str, str | None]:
    if not models:
        raise CodexAppServerError('Codex App Server returned no available models for this account.')
    default_index = next((i for i, model in enumerate(models) if model.get('isDefault')), 0)
    print('\nAvailable Codex models:')
    for index, model in enumerate(models, start=1):
        model_id = model.get('id') or model.get('model') or '(unknown model)'
        name = model.get('displayName')
        default = ' (default)' if model.get('isDefault') else ''
        label = f'{name} [{model_id}]' if name and name != model_id else str(model_id)
        print(f'  {index}. {label}{default}')

    raw_choice = input(f'Model [{default_index + 1}]: ').strip()
    if not raw_choice:
        selected_index = default_index
    else:
        try:
            selected_index = int(raw_choice) - 1
        except ValueError as exc:
            raise ValueError('Choose a model by its displayed number.') from exc
    if selected_index < 0 or selected_index >= len(models):
        raise ValueError('The selected model number is outside the list.')

    selected = models[selected_index]
    model_id = selected.get('id') or selected.get('model')
    if not isinstance(model_id, str) or not model_id:
        raise CodexAppServerError('Selected model has no model ID.')
    effort_options = selected.get('supportedReasoningEfforts', [])
    efforts = [
        option.get('reasoningEffort')
        for option in effort_options
        if isinstance(option, dict) and isinstance(option.get('reasoningEffort'), str)
    ] if isinstance(effort_options, list) else []
    if not efforts:
        return model_id, None
    default_effort = selected.get('defaultReasoningEffort')
    if default_effort not in efforts:
        default_effort = efforts[0]
    print(f'Reasoning effort: {", ".join(efforts)}')
    effort = input(f'Effort [{default_effort}]: ').strip() or str(default_effort)
    if effort not in efforts:
        raise ValueError(f'Unsupported reasoning effort for {model_id}; choose one of: {", ".join(efforts)}')
    return model_id, effort


def setup_llm() -> int:
    if not sys.stdin.isatty():
        print('`ctfbot llm setup` requires an interactive terminal.', file=sys.stderr)
        return 2
    path = llm_config_path()
    if path.exists() and not _ask_yes_no(f'Overwrite model selection in {path}?'):
        print('Existing configuration left unchanged.')
        return 0
    try:
        with CodexAppServer() as server:
            _connect_account(server)
            model, effort = _select_model(server.models())
        saved_path = _save_llm_config(model, effort)
    except (CodexAppServerError, EOFError, OSError, ValueError) as exc:
        print(f'LLM setup failed: {exc}', file=sys.stderr)
        return 1
    print(f'\nSaved model selection to {saved_path}.')
    print('Codex login credentials remain in Codex CLI storage; ctfbot stored no token.')
    print('Run `ctfbot llm test` to send one short connection check.')
    return 0


def show_llm_status() -> int:
    try:
        config = read_llm_config()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not config:
        print(f'No ctfbot model selected. Run `ctfbot llm setup` (Codex CLI: {find_codex_cli() or "not found"}).')
        return 0
    print(f'Provider: {config["provider"]}')
    print(f'Model: {config["model"]}')
    if config.get('reasoning_effort'):
        print(f'Reasoning effort: {config["reasoning_effort"]}')
    print(f'Config: {llm_config_path()}')
    if not find_codex_cli():
        print('Codex CLI: not found on PATH')
        return 1
    try:
        with CodexAppServer() as server:
            account = server.account()
    except CodexAppServerError as exc:
        print(f'Codex App Server: unavailable ({exc})')
        return 1
    auth_mode = account.get('type') or 'not signed in'
    print(f'Codex authentication: {auth_mode}')
    plan = account.get('planType')
    if isinstance(plan, str) and plan:
        print(f'ChatGPT plan: {plan}')
    return 0 if auth_mode == 'chatgpt' else 1


def test_llm_connection() -> int:
    try:
        config = read_llm_config()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not config:
        print('No model is configured. Run `ctfbot llm setup` first.', file=sys.stderr)
        return 2
    try:
        with CodexAppServer() as server:
            account = server.account()
            if account.get('type') != 'chatgpt':
                raise CodexAppServerError('The Codex CLI is not authenticated with a ChatGPT account.')
            response = server.test_model(
                str(config['model']),
                str(config['reasoning_effort']) if config.get('reasoning_effort') else None,
            )
    except CodexAppServerError as exc:
        print(f'LLM connection failed: {exc}', file=sys.stderr)
        return 1
    print('LLM connection succeeded.')
    print(f'Response: {response}')
    return 0


def test_llm_tool_call() -> int:
    """Make one experimental, constant-return dynamic-tool smoke request."""
    try:
        config = read_llm_config()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not config:
        print('No model is configured. Run `ctfbot llm setup` first.', file=sys.stderr)
        return 2
    try:
        with CodexAppServer(experimental_api=True) as server:
            account = server.account()
            if account.get('type') != 'chatgpt':
                raise CodexAppServerError('The Codex CLI is not authenticated with a ChatGPT account.')
            result = server.test_dynamic_tool(
                str(config['model']),
                str(config['reasoning_effort']) if config.get('reasoning_effort') else None,
            )
    except CodexAppServerError as exc:
        print(f'LLM tool-call smoke failed: {exc}', file=sys.stderr)
        return 1
    print('LLM dynamic-tool smoke succeeded.')
    print(f'Tool: {result["tool"]}')
    print(f'Arguments: {result["arguments"]}')
    print(f'Response: {result["response"]}')
    usage = result.get('usage')
    if isinstance(usage, dict):
        safe_usage = {key: value for key, value in usage.items() if isinstance(value, (str, int, float, bool))}
        print(f'Usage: {safe_usage}')
    else:
        print('Usage: unavailable in turn/completed')
    return 0
