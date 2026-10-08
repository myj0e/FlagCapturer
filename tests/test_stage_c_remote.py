"""Connector unit tests use self-owned loopback servers; Docker is not mocked as acceptance."""
from __future__ import annotations

import socket
import socketserver
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from ctfbot.application.remote_profile import REQUIRED_CASES, _receipt
from ctfbot.challenge.remote import RemoteSpec
from ctfbot.runtime.remote import CandidateRemoteFactory, DockerRemoteRuntime
from ctfbot.runtime.service_recovery import write_private
from ctfbot.runtime.supervised import REMOTE_NETWORK_PROFILE

IMAGE = 'sha256:' + 'a' * 64


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        data = self.request.recv(16384)
        if data == b'wait':
            self.server.waiting.set()
            self.server.release.wait(3)
        else:
            self.request.sendall(b'HTTP/1.1 302 Found\r\nLocation: http://outside.invalid/\r\n\r\n' if data == b'redirect' else b'x' * 20000)


@pytest.fixture
def endpoint():
    with socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler) as server:
        server.daemon_threads = True
        server.waiting, server.release = threading.Event(), threading.Event()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.release.set()
            server.shutdown()
            thread.join(2)


def scope(port):
    now = datetime.now(timezone.utc)
    return RemoteSpec('fixture.invalid', port, 'tcp', now - timedelta(seconds=1),
                      now + timedelta(minutes=5), 'authored endpoint test')


def connector(tmp_path, port):
    events = []
    runtime = DockerRemoteRuntime(tmp_path, IMAGE, spec=scope(port), pinned_ip='127.0.0.1',
                                  grant_sha256='b' * 64, event_sink=events.append,
                                  recovery_root=tmp_path / 'state', expected_daemon={})
    # Unit-only admission substitute. No Docker isolation claim is made here.
    runtime._started = True
    return runtime, events


@pytest.mark.parametrize('changed', [{'host': 'other.invalid'}, {'port': 1}, {'protocol': 'udp'}])
def test_remote_rejects_changed_scope(tmp_path, changed):
    runtime, events = connector(tmp_path, 31337)
    args = dict(host='fixture.invalid', port=31337, protocol='tcp', data=b'hi', timeout=1, maximum_bytes=100)
    args.update(changed)
    with pytest.raises(PermissionError): runtime.remote_exchange(**args)
    assert not events and not runtime._remote_sockets


@pytest.mark.parametrize('changed', [{'data': b'a' * 16385}, {'timeout': 0}, {'timeout': 31}, {'maximum_bytes': 16385}])
def test_remote_rejects_unbounded_exchange(tmp_path, changed):
    runtime, _ = connector(tmp_path, 31337)
    args = dict(host='fixture.invalid', port=31337, protocol='tcp', data=b'hi', timeout=1, maximum_bytes=100)
    args.update(changed)
    with pytest.raises(ValueError): runtime.remote_exchange(**args)


def exchange(runtime, data=b'hello', **kwargs):
    return runtime.remote_exchange(runtime.remote_spec.host, runtime.remote_spec.port, 'tcp', data,
                                   timeout=kwargs.get('timeout', 1), maximum_bytes=kwargs.get('maximum_bytes', 100))


def test_pin_proxy_redirect_and_output_cap(tmp_path, endpoint, monkeypatch):
    runtime, events = connector(tmp_path, endpoint.server_address[1])
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    with patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS must not be used')):
        assert exchange(runtime)['status'] == 'output_limit'
        response = exchange(runtime, b'redirect', maximum_bytes=1024)
    assert response['status'] == 'eof' and b'outside.invalid' in response['data']
    assert len([e for e in events if e['state'] == 'connecting']) == 2
    assert not runtime._remote_sockets


@pytest.mark.parametrize('budget', ['tool', 'run', 'scope'])
def test_remote_deadlines_close_connection(tmp_path, endpoint, budget):
    runtime, events = connector(tmp_path, endpoint.server_address[1])
    if budget == 'run': runtime._run_deadline = time.monotonic() + .1
    if budget == 'scope': runtime._scope_deadline = time.monotonic() + .1
    result = exchange(runtime, b'wait', timeout=.15)
    assert result['status'] == 'timed_out'
    assert events[-1]['state'] == 'connection_closed' and not runtime._remote_sockets


def test_remote_cancel_and_single_active_exchange(tmp_path, endpoint):
    runtime, events = connector(tmp_path, endpoint.server_address[1])
    errors = []
    def worker():
        try: exchange(runtime, b'wait', timeout=2)
        except (RuntimeError, OSError) as exc: errors.append(type(exc).__name__)
    thread = threading.Thread(target=worker)
    thread.start()
    assert endpoint.waiting.wait(1)
    with pytest.raises(RuntimeError, match='one remote'): exchange(runtime)
    runtime._stop_remote()
    thread.join(1)
    assert not thread.is_alive() and not runtime._remote_sockets
    assert events[-1]['state'] == 'connection_closed'
    with pytest.raises(RuntimeError, match='stopped'): exchange(runtime)


def test_remote_refusal_and_expired_scope(tmp_path):
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        runtime, events = connector(tmp_path, reserved.getsockname()[1])
        with pytest.raises(OSError): exchange(runtime)
    assert events[-1]['state'] == 'connection_closed' and not runtime._remote_sockets
    old = runtime.remote_spec
    runtime.remote_spec = RemoteSpec(old.host, old.port, old.protocol, old.not_before,
                                     datetime.now(timezone.utc)-timedelta(seconds=1), old.authorization_basis)
    with pytest.raises(PermissionError): exchange(runtime)


def test_grant_missing_literal_mismatch_and_receipt_gate(tmp_path):
    grant = tmp_path / 'grant.json'
    with pytest.raises(FileNotFoundError): CandidateRemoteFactory(grant).validate(scope(31337), IMAGE)
    now = datetime.now(timezone.utc)
    raw = dict(schema_version=1, host='127.0.0.2', port=31337, protocol='tcp',
               not_before=(now-timedelta(seconds=1)).isoformat().replace('+00:00','Z'),
               not_after=(now+timedelta(minutes=5)).isoformat().replace('+00:00','Z'),
               runtime_authorized=True, authorization_basis='authored')
    write_private(grant, dict(schema_version=1, remote=raw, pinned_ip='127.0.0.1', solver_image=IMAGE,
                              authorization_basis='unit controller authorization'))
    with pytest.raises(PermissionError, match='literal host'):
        CandidateRemoteFactory(grant).validate(RemoteSpec.from_manifest(raw), IMAGE)
    receipt = tmp_path / 'receipt.json'
    write_private(receipt, dict(schema_version=1, result='PASS', network_profile=REMOTE_NETWORK_PROFILE,
                                daemon={}, solver_image=IMAGE, grant_sha256='b'*64, cases={}))
    with pytest.raises(ValueError, match='all required'):
        _receipt(receipt, identity={}, image=IMAGE, grant_hash='b'*64)
    assert len(REQUIRED_CASES) == 28
