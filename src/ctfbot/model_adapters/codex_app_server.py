'''Minimal stdio JSON-RPC client for the installed Codex App Server.'''

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from typing import Any

from ctfbot.model_adapters.mailbox import MessageBuffer, MessageQueue, PublicTextBuffer
from ctfbot.model_adapters.events import normalize_event
from ctfbot import __version__
from ctfbot.evidence.redaction import redact_sensitive_text


class CodexAppServerError(RuntimeError):
    '''A safe, user-facing Codex App Server error.'''


def find_codex_cli() -> str | None:
    return shutil.which('codex')


def _redact(value: str) -> str:
    return redact_sensitive_text(value, limit=400)


class CodexAppServer:
    '''Own one local `codex app-server` process and its JSONL transport.'''

    def __init__(self, executable: str | None = None, *, experimental_api: bool = False) -> None:
        self.executable = executable or find_codex_cli()
        self.experimental_api = experimental_api
        self._process: subprocess.Popen[str] | None = None
        self._messages = MessageQueue()
        self._notifications = MessageBuffer()
        self._unmatched = MessageBuffer()
        self._stderr_tail: deque[str] = deque(maxlen=8)
        self._request_id = 0
        self._write_lock = threading.Lock()

    def __enter__(self) -> CodexAppServer:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start(self) -> None:
        if not self.executable:
            raise CodexAppServerError(
                'Codex CLI was not found. Install the official Codex CLI and ensure `codex` is on PATH.'
            )
        if self._process is not None:
            return

        # This integration is specifically for Codex managed ChatGPT sign-in.
        # Avoid accidentally routing it through a generic OpenAI-compatible URL.
        child_env = os.environ.copy()
        child_env.pop('OPENAI_API_KEY', None)
        child_env.pop('OPENAI_BASE_URL', None)
        try:
            self._process = subprocess.Popen(
                [
                    self.executable,
                    'app-server',
                    '--listen',
                    'stdio://',
                    '--config',
                    'mcp_servers={}',
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                env=child_env,
            )
        except OSError as exc:
            raise CodexAppServerError(f'Could not start Codex App Server: {_redact(str(exc))}') from exc

        threading.Thread(target=self._read_stdout, name='ctfbot-codex-stdout', daemon=True).start()
        threading.Thread(target=self._read_stderr, name='ctfbot-codex-stderr', daemon=True).start()
        try:
            self.request(
                'initialize',
                {
                    'clientInfo': {
                        'name': 'ctfbot',
                        'title': 'ctfbot',
                        'version': __version__,
                    },
                    'capabilities': {'experimentalApi': True} if self.experimental_api else {},
                },
                timeout=15,
            )
            self.notify('initialized', {})
        except CodexAppServerError:
            self.close()
            raise

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._messages.put(None)
            return
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._messages.put({'_ctfbot_parse_error': True})
                    continue
                if isinstance(message, dict):
                    self._messages.put(message)
        finally:
            self._messages.put(None)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr_tail.append(_redact(line.strip()))

    def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise CodexAppServerError(self._process_error('Codex App Server is not running.'))
        try:
            payload = json.dumps(message, ensure_ascii=False, separators=(',', ':'))
            with self._write_lock:
                process.stdin.write(payload + '\n')
                process.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            raise CodexAppServerError(
                self._process_error(f'Could not write to Codex App Server: {_redact(str(exc))}')
            ) from exc

    def _next_message(self, timeout: float) -> dict[str, Any]:
        try:
            message = self._messages.get(timeout=timeout)
        except OverflowError as exc:
            raise CodexAppServerError(str(exc)) from exc
        except queue.Empty as exc:
            raise CodexAppServerError(self._process_error('Timed out waiting for Codex App Server.')) from exc
        if message is None:
            raise CodexAppServerError(self._process_error('Codex App Server closed its output stream.'))
        if message.get('_ctfbot_parse_error'):
            raise CodexAppServerError('Codex App Server returned a malformed JSONL message.')
        return message

    def _process_error(self, message: str) -> str:
        details = '; '.join(part for part in self._stderr_tail if part)
        return f'{message} {details}' if details else message

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30,
    ) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        payload: dict[str, Any] = {'method': method, 'id': request_id}
        if params is not None:
            payload['params'] = params
        self._send(payload)

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexAppServerError(self._process_error(f'Timed out during `{method}`.'))
            message = self._next_message(remaining)
            if message.get('id') == request_id and 'method' not in message:
                error = message.get('error')
                if isinstance(error, dict):
                    code = error.get('code', 'unknown')
                    description = _redact(str(error.get('message', 'request failed')))
                    raise CodexAppServerError(f'Codex App Server `{method}` failed ({code}): {description}')
                result = message.get('result', {})
                if not isinstance(result, dict):
                    raise CodexAppServerError(f'Codex App Server `{method}` returned an unexpected result.')
                return result
            if 'method' in message and 'id' in message and message.get('method') != 'item/tool/call':
                self._send({'id': message['id'], 'error': {'code': -32601, 'message': 'Unsupported server request.'}})
                continue
            if 'method' in message and 'id' not in message:
                self._notifications.append(message)
            else:
                self._unmatched.append(message)

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({'method': method, 'params': params})

    def wait_for_notification(
        self,
        method: str,
        *,
        timeout: float,
        predicate: Any = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            for index, message in enumerate(self._notifications):
                if message.get('method') != method:
                    continue
                if predicate is not None and not predicate(message.get('params', {})):
                    continue
                del self._notifications[index]
                return message.get('params', {})
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexAppServerError(f'Timed out waiting for `{method}`.')
            message = self._next_message(remaining)
            if 'method' in message and 'id' not in message:
                self._notifications.append(message)
            else:
                self._unmatched.append(message)

    def account(self) -> dict[str, Any]:
        result = self.request('account/read', {'refreshToken': False})
        account = result.get('account')
        return account if isinstance(account, dict) else {}

    def login_chatgpt(self, *, device_code: bool = False, timeout: float = 300) -> dict[str, Any]:
        if device_code:
            result = self.request('account/login/start', {'type': 'chatgptDeviceCode'})
            print(f"\nOpen {result.get('verificationUrl', 'the shown sign-in URL')} and enter:")
            print(f"  {result.get('userCode', '(no device code returned)')}\n")
        else:
            result = self.request(
                'account/login/start',
                {
                    'type': 'chatgpt',
                    'useHostedLoginSuccessPage': True,
                    'appBrand': 'codex',
                },
            )
            auth_url = result.get('authUrl')
            if not isinstance(auth_url, str) or not auth_url.startswith('https://'):
                raise CodexAppServerError('Codex App Server did not return a valid ChatGPT sign-in URL.')
            print('\nOpen this ChatGPT sign-in URL in your browser:')
            print(f'  {auth_url}\n')
        login_id = result.get('loginId')
        completed = self.wait_for_notification(
            'account/login/completed',
            timeout=timeout,
            predicate=(lambda params: params.get('loginId') == login_id) if login_id else None,
        )
        if not completed.get('success'):
            error = _redact(str(completed.get('error') or 'sign-in was not completed'))
            raise CodexAppServerError(f'ChatGPT sign-in failed: {error}')
        return self.account()

    def models(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, Any] = {'limit': 100, 'includeHidden': False}
            if cursor:
                params['cursor'] = cursor
            result = self.request('model/list', params)
            data = result.get('data', [])
            if isinstance(data, list):
                models.extend(model for model in data if isinstance(model, dict))
            next_cursor = result.get('nextCursor')
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        return models

    def start_dynamic_thread(
        self,
        model: str,
        cwd: str,
        tools: list[dict[str, Any]],
        *,
        timeout: float = 30,
    ) -> str:
        """Start a thread with ctfbot-owned dynamic tools (experimental API)."""
        result = self.request(
            'thread/start',
            {
                'model': model,
                'cwd': cwd,
                'approvalPolicy': 'never',
                'sandbox': 'read-only',
                'serviceName': 'ctfbot',
                'dynamicTools': tools,
            },
            timeout=min(30, timeout),
        )
        thread = result.get('thread')
        thread_id = thread.get('id') if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise CodexAppServerError('Codex App Server did not return a thread id.')
        return thread_id

    def run_dynamic_turn(
        self,
        thread_id: str,
        model: str,
        prompt: str,
        tools: list[dict[str, Any]],
        on_tool_call: Any,
        *,
        cwd: str | None = None,
        effort: str | None = None,
        timeout: float = 120,
        on_message: Any = None,
        on_event: Any = None,
    ) -> dict[str, Any]:
        """Run one provider turn and answer ctfbot dynamic tool calls locally."""
        del tools  # Tool declarations are fixed when the thread starts.
        deadline = time.monotonic() + timeout
        params: dict[str, Any] = {
            'threadId': thread_id,
            'input': [{'type': 'text', 'text': prompt}],
            'model': model,
            'approvalPolicy': 'never',
            'sandboxPolicy': {'type': 'readOnly', 'networkAccess': False},
        }
        if cwd is not None:
            params['cwd'] = cwd
        if effort:
            params['effort'] = effort
        start_remaining = deadline - time.monotonic()
        if start_remaining <= 0:
            raise CodexAppServerError('Codex App Server model turn timed out before start.')
        started = self.request('turn/start', params, timeout=min(30, start_remaining))
        turn = started.get('turn')
        turn_id = turn.get('id') if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise CodexAppServerError('Codex App Server did not return a turn id.')

        completed_turn: dict[str, Any] | None = None
        messages = PublicTextBuffer()
        deltas = PublicTextBuffer()
        tool_count = 0
        sequence = 0
        while completed_turn is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexAppServerError('Codex App Server model turn timed out.')
            message = self._take_dynamic_turn_message(thread_id, turn_id, remaining)
            method = message.get('method')
            message_id = message.get('id')
            event_params = message.get('params', {})
            if not isinstance(event_params, dict):
                event_params = {}

            sequence += 1
            event = normalize_event(message, thread_id, turn_id, sequence)
            if event is not None and on_event is not None:
                on_event(event)

            if method == 'item/tool/call' and message_id is not None:
                if event_params.get('threadId') != thread_id or event_params.get('turnId') != turn_id:
                    self._send({
                        'id': message_id,
                        'error': {'code': -32602, 'message': 'Tool call did not match the active ctfbot turn.'},
                    })
                    continue
                tool_name = event_params.get('tool')
                arguments = event_params.get('arguments', {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = None
                call = {
                    'call_id': str(message_id),
                    'name': tool_name if isinstance(tool_name, str) else '',
                    'arguments': arguments if isinstance(arguments, dict) else {},
                }
                tool_count += 1
                try:
                    reply = on_tool_call(call)
                    content = str(reply.get('content', '')) if isinstance(reply, dict) else str(reply)
                    success = bool(reply.get('success', True)) if isinstance(reply, dict) else True
                except Exception as exc:
                    content = f'ctfbot tool handler error: {type(exc).__name__}'
                    success = False
                self._send({
                    'id': message_id,
                    'result': {
                        'contentItems': [{'type': 'inputText', 'text': content}],
                        'success': success,
                    },
                })
            elif method == 'turn/completed':
                candidate = event_params.get('turn')
                if isinstance(candidate, dict) and candidate.get('id') == turn_id:
                    completed_turn = candidate
            elif method == 'item/completed':
                item = event_params.get('item', {})
                if (
                    event_params.get('turnId') == turn_id
                    and isinstance(item, dict)
                    and item.get('type') == 'agentMessage'
                    and isinstance(item.get('text'), str)
                ):
                    messages.append(item['text'])
                    if on_message is not None:
                        on_message(item['text'])
            elif method == 'item/agentMessage/delta':
                delta = event_params.get('delta')
                if event_params.get('turnId') == turn_id and isinstance(delta, str):
                    deltas.append(delta)
            elif method and message_id is not None:
                self._send({'id': message_id, 'error': {'code': -32601, 'message': 'Unsupported server request.'}})

        status = completed_turn.get('status')
        if status not in {'completed', None}:
            details = completed_turn.get('error', {})
            message = details.get('message') if isinstance(details, dict) else None
            reason = _redact(str(message)) if message else str(status)
            raise CodexAppServerError(f'Model request finished with status {status}: {reason}')
        response = (messages.result() if messages.text else deltas.result()).strip()
        usage = completed_turn.get('usage')
        return {
            'text': response,
            'usage': usage if isinstance(usage, dict) else None,
            'response_id': turn_id,
            'finish_reason': str(status or 'completed'),
            'tool_calls': tool_count,
        }

    def _take_dynamic_turn_message(self, thread_id: str, turn_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout

        def matches(message: dict[str, Any]) -> bool:
            method = message.get('method')
            params = message.get('params', {})
            if not isinstance(params, dict):
                return False
            if method == 'turn/completed':
                turn = params.get('turn', {})
                return isinstance(turn, dict) and turn.get('id') == turn_id and params.get('threadId') in {None, thread_id}
            if method == 'item/tool/call':
                return params.get('threadId') == thread_id and params.get('turnId') == turn_id
            if method in {'item/started', 'item/completed', 'item/agentMessage/delta',
                          'thread/tokenUsage/updated', 'thread/compacted', 'error'}:
                return params.get('turnId') == turn_id and params.get('threadId') in {None, thread_id}
            return False

        while True:
            while self._notifications:
                queued = self._notifications.popleft()
                if matches(queued):
                    return queued
            for queued in list(self._unmatched):
                if matches(queued):
                    self._unmatched.remove(queued)
                    return queued
                if 'method' in queued and 'id' in queued:
                    self._unmatched.remove(queued)
                    self._send({'id': queued['id'], 'error': {'code': -32601,
                                'message': 'Request does not belong to the active ctfbot turn.'}})
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexAppServerError('Codex App Server model turn timed out.')
            message = self._next_message(remaining)
            if matches(message):
                return message
            if 'method' in message and 'id' in message:
                self._send({'id': message['id'], 'error': {'code': -32601,
                            'message': 'Request does not belong to the active ctfbot turn.'}})
            elif 'method' not in message:
                self._unmatched.append(message)
            # Unrelated/stale notifications are consumed, not retained forever.

    def delete_thread(self, thread_id: str) -> None:
        self.request('thread/delete', {'threadId': thread_id}, timeout=10)

    def test_model(self, model: str, effort: str | None = None, *, timeout: float = 90) -> str:
        with tempfile.TemporaryDirectory(prefix='ctfbot-llm-check-') as temp_dir:
            thread_id: str | None = None
            try:
                thread = self.request(
                    'thread/start',
                    {
                        'model': model,
                        'cwd': temp_dir,
                        'approvalPolicy': 'never',
                        'sandbox': 'read-only',
                        'serviceName': 'ctfbot',
                    },
                )
                thread_obj = thread.get('thread')
                thread_id = thread_obj.get('id') if isinstance(thread_obj, dict) else None
                if not isinstance(thread_id, str):
                    raise CodexAppServerError('Codex App Server did not return a thread id.')

                params: dict[str, Any] = {
                    'threadId': thread_id,
                    'input': [
                        {
                            'type': 'text',
                            'text': 'Reply with exactly CTFBOT_CONNECTION_OK. Do not use tools or inspect files.',
                        }
                    ],
                    'model': model,
                    'cwd': temp_dir,
                    'approvalPolicy': 'never',
                    'sandboxPolicy': {
                        'type': 'readOnly',
                        'networkAccess': False,
                    },
                }
                if effort:
                    params['effort'] = effort
                turn_result = self.request('turn/start', params, timeout=30)
                turn = turn_result.get('turn')
                turn_id = turn.get('id') if isinstance(turn, dict) else None
                if not isinstance(turn_id, str):
                    raise CodexAppServerError('Codex App Server did not return a turn id.')

                completed_messages: list[str] = []
                deltas: list[str] = []
                deadline = time.monotonic() + timeout
                while True:
                    event = self._next_turn_event(thread_id, turn_id, deadline)
                    event_method = event.get('method')
                    event_params = event.get('params', {})
                    if not isinstance(event_params, dict):
                        continue
                    if event_method == 'item/completed':
                        item = event_params.get('item', {})
                        if isinstance(item, dict) and item.get('type') == 'agentMessage':
                            content = item.get('text')
                            if isinstance(content, str):
                                completed_messages.append(content)
                    elif event_method == 'item/agentMessage/delta':
                        delta = event_params.get('delta')
                        if isinstance(delta, str):
                            deltas.append(delta)
                    elif event_method == 'turn/completed':
                        completed_turn = event_params.get('turn', {})
                        status = completed_turn.get('status') if isinstance(completed_turn, dict) else None
                        if status not in {'completed', None}:
                            details = completed_turn.get('error', {}) if isinstance(completed_turn, dict) else {}
                            message = details.get('message') if isinstance(details, dict) else None
                            reason = _redact(str(message)) if message else str(status)
                            raise CodexAppServerError(f'Model request finished with status {status}: {reason}')
                        break
                response = ''.join(completed_messages or deltas).strip()
                if not response:
                    raise CodexAppServerError('Model returned no text response.')
                return response
            finally:
                if thread_id:
                    try:
                        self.request('thread/delete', {'threadId': thread_id}, timeout=10)
                    except CodexAppServerError:
                        pass

    def test_dynamic_tool(self, model: str, effort: str | None = None, *, timeout: float = 90) -> dict[str, Any]:
        """Run one isolated, constant-return dynamic-tool round trip."""
        with tempfile.TemporaryDirectory(prefix='ctfbot-tool-smoke-') as temp_dir:
            thread_id: str | None = None
            try:
                thread = self.request(
                    'thread/start',
                    {
                        'model': model,
                        'cwd': temp_dir,
                        'approvalPolicy': 'never',
                        'sandbox': 'read-only',
                        'serviceName': 'ctfbot',
                        'dynamicTools': [
                            {
                                'type': 'function',
                                'name': 'ctfbot_probe',
                                'description': 'Return a fixed string to verify ctfbot tool-call transport.',
                                'inputSchema': {
                                    'type': 'object',
                                    'properties': {},
                                    'additionalProperties': False,
                                },
                            }
                        ],
                    },
                )
                thread_obj = thread.get('thread')
                thread_id = thread_obj.get('id') if isinstance(thread_obj, dict) else None
                if not isinstance(thread_id, str):
                    raise CodexAppServerError('Codex App Server did not return a thread id.')

                params: dict[str, Any] = {
                    'threadId': thread_id,
                    'input': [
                        {
                            'type': 'text',
                            'text': (
                                'Call ctfbot_probe exactly once using empty arguments. '
                                'After it returns, reply with exactly CTFBOT_TOOL_ROUNDTRIP_OK. '
                                'Do not use any other tools or inspect files.'
                            ),
                        }
                    ],
                    'model': model,
                    'cwd': temp_dir,
                    'approvalPolicy': 'never',
                    'sandboxPolicy': {
                        'type': 'readOnly',
                        'networkAccess': False,
                    },
                }
                if effort:
                    params['effort'] = effort
                turn_result = self.request('turn/start', params, timeout=30)
                turn = turn_result.get('turn')
                turn_id = turn.get('id') if isinstance(turn, dict) else None
                if not isinstance(turn_id, str):
                    raise CodexAppServerError('Codex App Server did not return a turn id.')

                tool_calls = 0
                tool_arguments: Any = None
                messages = PublicTextBuffer()
                deltas = PublicTextBuffer()
                completed_turn: dict[str, Any] | None = None
                deadline = time.monotonic() + timeout
                while completed_turn is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise CodexAppServerError('Dynamic tool smoke timed out.')
                    message: dict[str, Any] | None = None
                    for index, queued in enumerate(self._notifications):
                        queued_params = queued.get('params', {})
                        queued_turn = (
                            queued_params.get('turn', {})
                            if isinstance(queued_params, dict)
                            else {}
                        )
                        relevant_completion = (
                            queued.get('method') == 'turn/completed'
                            and isinstance(queued_turn, dict)
                            and queued_turn.get('id') == turn_id
                        )
                        relevant_item = (
                            queued.get('method') in {'item/completed', 'item/agentMessage/delta'}
                            and isinstance(queued_params, dict)
                            and queued_params.get('turnId') == turn_id
                        )
                        if relevant_completion or relevant_item:
                            message = queued
                            del self._notifications[index]
                            break
                    if message is None:
                        for index, queued in enumerate(self._unmatched):
                            queued_params = queued.get('params', {})
                            if (
                                queued.get('method') == 'item/tool/call'
                                and isinstance(queued_params, dict)
                                and queued_params.get('threadId') == thread_id
                                and queued_params.get('turnId') == turn_id
                            ):
                                message = queued
                                del self._unmatched[index]
                                break
                    if message is None:
                        message = self._next_message(remaining)
                    if message.get('method') == 'item/tool/call' and 'id' in message:
                        call_params = message.get('params', {})
                        if not isinstance(call_params, dict):
                            call_params = {}
                        is_expected_call = (
                            call_params.get('tool') == 'ctfbot_probe'
                            and call_params.get('threadId') == thread_id
                            and call_params.get('turnId') == turn_id
                            and call_params.get('arguments') == {}
                        )
                        tool_calls += 1
                        if is_expected_call and tool_calls == 1:
                            tool_arguments = call_params.get('arguments')
                            self._send(
                                {
                                    'id': message['id'],
                                    'result': {
                                        'contentItems': [
                                            {'type': 'inputText', 'text': 'CTFBOT_TOOL_OK'}
                                        ],
                                        'success': True,
                                    },
                                }
                            )
                        else:
                            self._send(
                                {
                                    'id': message['id'],
                                    'result': {
                                        'contentItems': [
                                            {'type': 'inputText', 'text': 'Unexpected or duplicate tool call.'}
                                        ],
                                        'success': False,
                                    },
                                }
                            )
                    elif 'method' in message and 'id' not in message:
                        event_params = message.get('params', {})
                        if not isinstance(event_params, dict):
                            self._notifications.append(message)
                            continue
                        if message.get('method') == 'turn/completed':
                            event_turn = event_params.get('turn', {})
                            if isinstance(event_turn, dict) and event_turn.get('id') == turn_id:
                                completed_turn = event_turn
                                continue
                        if message.get('method') == 'item/completed':
                            item = event_params.get('item', {})
                            if (
                                isinstance(item, dict)
                                and item.get('type') == 'agentMessage'
                                and event_params.get('turnId') == turn_id
                                and isinstance(item.get('text'), str)
                            ):
                                messages.append(item['text'])
                        elif message.get('method') == 'item/agentMessage/delta':
                            delta = event_params.get('delta')
                            if isinstance(delta, str) and event_params.get('turnId') == turn_id:
                                deltas.append(delta)
                        else:
                            self._notifications.append(message)
                    elif 'method' in message and 'id' in message:
                        self._send(
                            {
                                'id': message['id'],
                                'error': {'code': -32601, 'message': 'Unsupported server request.'},
                            }
                        )
                    else:
                        self._unmatched.append(message)

                status = completed_turn.get('status')
                if status not in {'completed', None}:
                    error = completed_turn.get('error', {})
                    detail = error.get('message') if isinstance(error, dict) else None
                    raise CodexAppServerError(
                        f'Dynamic tool smoke turn finished with status {status}: '
                        f'{_redact(str(detail)) if detail else status}'
                    )
                if tool_calls != 1:
                    raise CodexAppServerError(
                        f'Dynamic tool smoke expected one ctfbot_probe call; received {tool_calls}.'
                    )
                response = (messages.result() if messages.text else deltas.result()).strip()
                if response != 'CTFBOT_TOOL_ROUNDTRIP_OK':
                    raise CodexAppServerError(
                        f'Dynamic tool transport succeeded, but the final response was unexpected: '
                        f'{_redact(response)!r}'
                    )
                usage = completed_turn.get('usage')
                return {
                    'tool': 'ctfbot_probe',
                    'arguments': tool_arguments,
                    'response': response,
                    'usage': usage if isinstance(usage, dict) else None,
                }
            finally:
                if thread_id:
                    try:
                        self.request('thread/delete', {'threadId': thread_id}, timeout=10)
                    except CodexAppServerError:
                        pass

    def _next_turn_event(self, thread_id: str, turn_id: str, deadline: float) -> dict[str, Any]:
        relevant_methods = {
            'item/completed',
            'item/agentMessage/delta',
            'turn/completed',
        }
        while True:
            for index, message in enumerate(self._notifications):
                params = message.get('params', {})
                turn = params.get('turn', {}) if isinstance(params, dict) else {}
                is_turn_completion = (
                    message.get('method') == 'turn/completed'
                    and isinstance(turn, dict)
                    and turn.get('id') == turn_id
                )
                is_item_event = (
                    message.get('method') in relevant_methods - {'turn/completed'}
                    and isinstance(params, dict)
                    and params.get('threadId') in {None, thread_id}
                    and params.get('turnId') == turn_id
                )
                if is_turn_completion or is_item_event:
                    del self._notifications[index]
                    return message

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexAppServerError('Model request timed out.')
            message = self._next_message(remaining)
            if 'method' in message and 'id' not in message:
                params = message.get('params', {})
                turn = params.get('turn', {}) if isinstance(params, dict) else {}
                is_turn_completion = (
                    message.get('method') == 'turn/completed'
                    and isinstance(turn, dict)
                    and turn.get('id') == turn_id
                )
                is_item_event = (
                    message.get('method') in relevant_methods - {'turn/completed'}
                    and isinstance(params, dict)
                    and params.get('threadId') in {None, thread_id}
                    and params.get('turnId') == turn_id
                )
                if is_turn_completion or is_item_event:
                    return message
                self._notifications.append(message)
            else:
                self._unmatched.append(message)

    def interrupt(self) -> None:
        """Stop the owned process promptly and wake any active turn reader."""
        process = self._process
        if process is None:
            self._messages.put(None)
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        except OSError:
            pass
        finally:
            # Do not make a model turn wait out its original provider timeout if
            # the child exits without its stdout reader delivering EOF promptly.
            self._messages.put(None)

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
