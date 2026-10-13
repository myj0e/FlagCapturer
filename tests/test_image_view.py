"""Image delivery contracts with authored bytes and a deterministic provider."""
from __future__ import annotations

import base64
import hashlib
import json
import struct
import threading
import zlib
from types import SimpleNamespace

import pytest

from ctfbot.agent.loop import AgentLoop, RunLimits
from ctfbot.evidence.store import EvidenceStore
from ctfbot.model_adapters.codex_app_server import CodexAppServer
from ctfbot.model_adapters.codex_session import CodexModelSession, encode_tool_reply, model_supports_images
from ctfbot.model_adapters.protocol import ToolCall, ToolImage, ToolReply, TurnResult
from ctfbot.tools.image_view import MAX_IMAGE_BYTES, sandbox_image_path
from ctfbot.tools.registry import CommandResult, ToolRegistry


def authored_png() -> bytes:
    def chunk(kind, value):
        return struct.pack('!I', len(value)) + kind + value + struct.pack('!I', zlib.crc32(kind + value))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 2, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0\0\xff\0')) + chunk(b'IEND', b''))


class ImageRuntime:
    def __init__(self, *, data=None, failure=None):
        self.data = data or authored_png()
        self.calls = []
        self.failure = failure

    def render_image(self, path, crop, *, timeout):
        self.calls.append((path, crop, timeout))
        if self.failure is not None:
            return self.failure
        payload = {'status': 'ok', 'source_sha256': hashlib.sha256(authored_png()).hexdigest(),
                   'source_bytes': len(authored_png()), 'source_format': 'PNG', 'original_size': [2, 1],
                   'view_size': [2, 1], 'frame': 0, 'frame_count': 1, 'crop': crop,
                   'media_type': 'image/png', 'data_base64': base64.b64encode(self.data).decode()}
        return CommandResult(0, json.dumps(payload).encode(), b'')


def make_tools(tmp_path, runtime=None, *, images=True, output=16384):
    challenge, work = tmp_path / 'input', tmp_path / 'work'
    challenge.mkdir()
    work.mkdir()
    (challenge / 'image.png').write_bytes(authored_png())
    evidence = EvidenceStore(tmp_path / 'run')
    tools = ToolRegistry(challenge, work, evidence, runtime, max_model_output_bytes=output)
    tools.supports_images = images
    return tools


@pytest.mark.parametrize('entries, selected, expected', [
    ([{'id': 'vision', 'inputModalities': ['text', 'image']}], 'vision', True),
    ([{'model': 'text', 'inputModalities': ['text']}], 'text', False),
    ([{'id': 'legacy'}], 'legacy', True),  # documented Codex catalog default
    ([{'id': 'invalid', 'inputModalities': None}], 'invalid', False),
    ([{'id': 'vision', 'inputModalities': ['text', 'image']}], 'unknown', False),
])
def test_model_capability_comes_from_catalog(entries, selected, expected):
    assert model_supports_images(entries, selected) is expected


def test_session_queries_catalog_before_advertising_images(monkeypatch):
    class Server:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            pass
        def account(self):
            return {'type': 'chatgpt'}
        def models(self, *, include_hidden):
            assert include_hidden
            return [{'id': 'selected', 'inputModalities': ['text', 'image']}]
        def close(self):
            pass
    monkeypatch.setattr('ctfbot.model_adapters.codex_session.CodexAppServer', Server)
    session = CodexModelSession('selected', executable='codex')
    assert session.supports_images
    session.close()


@pytest.mark.parametrize('path', ['/etc/passwd', '../secret.png', '/work/../secret.png',
                                  '/challenge', 'a\\b.png', '/work'])
def test_image_paths_stay_inside_sandbox(path):
    with pytest.raises(PermissionError):
        sandbox_image_path(path)


def test_inline_image_and_provenance_survive_reliability_wrapper(tmp_path):
    runtime = ImageRuntime()
    tools = make_tools(tmp_path, runtime)
    outcome = tools.invoke(ToolCall('image_view', {'path': 'image.png'}))
    assert outcome.reply.success and outcome.reply.images[0].data == authored_png()
    assert runtime.calls == [('/challenge/image.png', None, 30)]
    payload = json.loads(outcome.reply.content)
    assert payload['observation']['id'] == 'observation-1'
    assert payload['source_sha256'] == hashlib.sha256(authored_png()).hexdigest()
    image_ref = outcome.result['image_evidence']
    persisted, _ = tools.evidence.read_artifact(image_ref['artifact'])
    assert persisted == outcome.reply.images[0].data
    assert 'data_base64' not in outcome.reply.content
    assert 'data:image' not in tools.evidence.events_path.read_text()
    assert tools.reliability.observations['observation-1']['coverage']['source_kind'] == 'image'


def test_crop_is_forwarded_and_text_only_models_do_not_execute(tmp_path):
    runtime = ImageRuntime()
    tools = make_tools(tmp_path, runtime, images=False)
    assert 'image_view' not in {spec.name for spec in tools.specs}
    denied = tools.invoke(ToolCall('image_view', {'path': 'image.png'}))
    assert denied.result['status'] == 'image_input_unsupported' and not denied.reply.images
    assert not runtime.calls
    tools.supports_images = True
    crop = {'x': 0, 'y': 0, 'width': 1, 'height': 1}
    outcome = tools.invoke(ToolCall('image_view', {'path': '/work/derived.png', 'crop': crop}))
    assert outcome.reply.success
    assert runtime.calls[-1][:2] == ('/work/derived.png', crop)
    invalid = tools.invoke(ToolCall('image_view', {'path': 'image.png', 'crop': {**crop, 'width': 0}}))
    assert invalid.result['status'] == 'invalid_arguments' and len(runtime.calls) == 1


@pytest.mark.parametrize('failure', [CommandResult(124, b'', b'', timed_out=True),
                                    CommandResult(0, b'{}', b'', truncated=True),
                                    CommandResult(1, b'{"status":"image_error","message":"bad image"}', b''),
                                    CommandResult(0, b'not JSON', b'')])
def test_failed_rendering_never_attaches_image(tmp_path, failure):
    tools = make_tools(tmp_path, ImageRuntime(failure=failure))
    outcome = tools.invoke(ToolCall('image_view', {'path': 'image.png'}))
    assert not outcome.reply.success and not outcome.reply.images


def test_oversized_image_and_input_hash_mismatch_are_rejected(tmp_path):
    runtime = ImageRuntime(data=b'\x89PNG\r\n\x1a\n' + b'x' * MAX_IMAGE_BYTES)
    tools = make_tools(tmp_path, runtime)
    assert not tools.invoke(ToolCall('image_view', {'path': 'image.png'})).reply.success
    runtime.data = authored_png()
    tools.reliability.expected_inputs = {'image.png': '0' * 64}
    outcome = tools.invoke(ToolCall('image_view', {'path': 'image.png'}))
    assert not outcome.reply.success and not outcome.reply.images


@pytest.mark.parametrize('output_limit', [256, 16384])
def test_loop_preserves_images_through_text_clipping_and_context_restore(tmp_path, output_limit):
    runtime = ImageRuntime()
    tools = make_tools(tmp_path, runtime, output=output_limit)
    received = []

    class VisionModel:
        supports_images = True
        def run_turn(self, prompt, specs, dispatch, *, timeout):
            assert 'image_view' in {spec.name for spec in specs}
            assert 'image_view' in prompt
            loop.context_events.pending = 'authored-compaction'
            received.append(dispatch(ToolCall('image_view', {'path': 'image.png'})))
            dispatch(ToolCall('run_complete', {"writeup": '# Authored solution\n\nReproduce the recorded fixture steps; outputs and submitted results are preserved in this run.', 'outcome': 'unsolved', 'summary': 'Authored image inspected', 'unresolved': []}))
            return TurnResult('Inspected authored pixels.', tool_calls=2)
        def close(self):
            pass

    loop = AgentLoop(VisionModel(), tools, tools.evidence, limits=RunLimits(per_tool_output_bytes=output_limit))
    result = loop.run('Inspect the authored image.')
    assert result.status == 'unsolved'
    assert received[0].images[0].data == authored_png()
    assert len(received[0].content.encode()) <= output_limit
    assert loop.transmission.image_count == 1 and loop.transmission.image_bytes == len(authored_png())
    assert loop.call_counts['observation']['admitted'] == 1
    if output_limit == 16384:
        assert 'context_restore' in json.loads(received[0].content)


def test_adapter_transmits_actual_image_content_items_over_jsonrpc():
    messages = [
        {'id': 8, 'method': 'item/tool/call', 'params': {'threadId': 'thread', 'turnId': 'turn',
         'tool': 'image_view', 'arguments': {'path': 'image.png'}}},
        {'method': 'turn/completed', 'params': {'turn': {'id': 'turn', 'status': 'completed'}}},
    ]
    class Server(CodexAppServer):
        def __init__(self):
            super().__init__('codex')
            self.sent = []
        def request(self, method, params=None, *, timeout=30):
            return {'turn': {'id': 'turn'}}
        def _take_dynamic_turn_message(self, thread, turn, timeout):
            return messages.pop(0)
        def _send(self, message):
            self.sent.append(message)

    session = CodexModelSession.__new__(CodexModelSession)
    session.model, session.effort, session.timeout = 'authored-vision', None, 5
    session.supports_images = True
    session._closed, session._cancelled = False, threading.Event()
    session._thread_id, session._temp = 'thread', SimpleNamespace(name='/unused')
    session._tool_schema_hash = hashlib.sha256(b'[]').hexdigest()
    session._message_handler, session.turns_started = None, 0
    session._server = Server()
    session.run_turn('Inspect image', [], lambda call: ToolReply('image metadata', images=(ToolImage(authored_png()),)), timeout=5)
    items = session._server.sent[0]['result']['contentItems']
    assert items[0] == {'type': 'inputText', 'text': 'image metadata'}
    assert items[1]['type'] == 'inputImage'
    assert base64.b64decode(items[1]['imageUrl'].split(',', 1)[1]) == authored_png()
    denied = encode_tool_reply(ToolReply('image', images=(ToolImage(authored_png()),)), supports_images=False)
    assert denied['success'] is False and 'contentItems' not in denied
