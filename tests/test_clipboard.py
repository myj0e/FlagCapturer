from __future__ import annotations

import ctypes
import subprocess
from unittest.mock import Mock

import pytest

from ctfbot.tui import clipboard


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ('SSH_CONNECTION', 'SSH_TTY', 'SSH_CLIENT', 'DISPLAY', 'WAYLAND_DISPLAY', 'WSL_DISTRO_NAME'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(clipboard.shutil, 'which', lambda _: None)


def test_macos_copies_exact_utf8_without_shell_or_newline(monkeypatch):
    monkeypatch.setattr(clipboard.sys, 'platform', 'darwin')
    run = Mock()
    monkeypatch.setattr(clipboard.subprocess, 'run', run)
    value = 'flag{中文`$(command)`}\n\u202e'
    terminal = Mock()
    result = clipboard.copy_text(value, terminal)
    assert result.confirmed and 'macOS' in result.message
    assert run.call_args.args == (['/usr/bin/pbcopy'],)
    assert run.call_args.kwargs['input'] == value.encode('utf-8')
    assert 'shell' not in run.call_args.kwargs
    terminal.assert_called_once_with(value)


def test_remote_session_only_copies_to_the_client_terminal(monkeypatch):
    monkeypatch.setenv('SSH_CONNECTION', 'remote-session')
    native = Mock(side_effect=AssertionError('must not write the server clipboard'))
    monkeypatch.setattr(clipboard, '_windows_copy', native)
    monkeypatch.setattr(clipboard, '_command_copy', native)
    monkeypatch.setattr(clipboard, '_x11_copy', native)
    terminal = Mock()
    result = clipboard.copy_text('flag{remote}', terminal)
    assert not result.confirmed and '无法确认' in result.message
    terminal.assert_called_once_with('flag{remote}')


def test_linux_uses_wayland_then_x11_without_requiring_helpers(monkeypatch):
    monkeypatch.setattr(clipboard.sys, 'platform', 'linux')
    monkeypatch.setenv('WAYLAND_DISPLAY', 'example')
    monkeypatch.setenv('DISPLAY', ':example')
    monkeypatch.setattr(clipboard.shutil, 'which', lambda program: '/usr/bin/wl-copy' if program == 'wl-copy' else None)
    command = Mock(return_value=False)
    native = Mock(return_value=True)
    monkeypatch.setattr(clipboard, '_command_copy', command)
    monkeypatch.setattr(clipboard, '_x11_copy', native)
    result = clipboard.copy_text('flag{linux}', Mock())
    assert result.confirmed and 'X11' in result.message
    command.assert_called_once_with(['/usr/bin/wl-copy', '--type', 'text/plain;charset=utf-8'], b'flag{linux}')
    native.assert_called_once_with('flag{linux}')


def test_failed_native_and_terminal_routes_do_not_claim_success(monkeypatch):
    monkeypatch.setattr(clipboard.sys, 'platform', 'darwin')
    monkeypatch.setattr(clipboard.subprocess, 'run', Mock(side_effect=subprocess.TimeoutExpired('pbcopy', 3)))
    result = clipboard.copy_text('flag{test}', Mock(side_effect=OSError('unavailable')))
    assert not result.confirmed and '复制失败' in result.message


@pytest.mark.parametrize('accepted', [True, False])
def test_windows_keeps_full_unicode_and_transfers_or_frees_memory(monkeypatch, accepted):
    memory = ctypes.create_string_buffer(1000)
    kernel = Mock()
    kernel.GlobalAlloc.return_value = 0x123456789
    kernel.GlobalLock.return_value = ctypes.addressof(memory)
    user = Mock()
    user.CreateWindowExW.return_value = 0x12345678A
    user.OpenClipboard.return_value = True
    user.EmptyClipboard.return_value = True
    user.SetClipboardData.return_value = 0x123456789 if accepted else 0
    monkeypatch.setattr(clipboard.ctypes, 'WinDLL', lambda name, **_: user if name == 'user32' else kernel, raising=False)
    value = 'flag{中文}\n\u202e'
    assert clipboard._windows_copy(value) is accepted
    data = value.encode('utf-16-le') + b'\0\0'
    assert memory.raw[:len(data)] == data
    user.OpenClipboard.assert_called_once_with(user.CreateWindowExW.return_value)
    user.SetClipboardData.assert_called_once_with(13, kernel.GlobalAlloc.return_value)
    user.CloseClipboard.assert_called_once()
    user.DestroyWindow.assert_called_once()
    assert kernel.GlobalFree.call_count == (0 if accepted else 1)


def test_native_x11_serves_utf8_selection_and_releases_old_owner(monkeypatch):
    import threading
    from collections import deque
    atoms = dict(zip(('CLIPBOARD', 'TARGETS', 'UTF8_STRING', 'TEXT', 'STRING', 'ATOM'), range(1, 7)))
    closed = threading.Event()
    events = deque([30, 29])
    x = Mock()
    x.XOpenDisplay.return_value = 123
    x.XCreateSimpleWindow.return_value = 42
    x.XGetSelectionOwner.return_value = 42
    x.XInternAtom.side_effect = lambda display, name, only: atoms[name.decode()]
    x.XPending.return_value = 1
    x.XCloseDisplay.side_effect = lambda _: closed.set()
    def next_event(display, pointer):
        event = pointer._obj
        event.type = events.popleft()
        if event.type == 30:
            event.request.requestor = 99
            event.request.selection = atoms['CLIPBOARD']
            event.request.target = atoms['UTF8_STRING']
            event.request.property = 77
    x.XNextEvent.side_effect = next_event
    payloads = []
    def change_property(display, requestor, prop, target, bits, mode, data, count):
        payloads.append((requestor, prop, target, bits, ctypes.string_at(data, count)))
    x.XChangeProperty.side_effect = change_property
    notifications = []
    x.XSendEvent.side_effect = lambda display, window, propagate, mask, pointer: notifications.append(
        (pointer._obj.notify.type, pointer._obj.notify.property))
    monkeypatch.setattr(clipboard, '_load_x11', lambda: x)
    value = 'flag{中文}\n\u202e'
    assert clipboard._x11_copy(value)
    assert closed.wait(2)
    assert payloads == [(99, 77, atoms['UTF8_STRING'], 8, value.encode('utf-8'))]
    assert notifications == [(31, 77)]
    x.XDestroyWindow.assert_called_once_with(123, 42)


def test_wsl_passes_utf16_to_the_windows_clipboard(monkeypatch):
    monkeypatch.setattr(clipboard.sys, 'platform', 'linux')
    monkeypatch.setenv('WSL_DISTRO_NAME', 'test')
    monkeypatch.setattr(clipboard.shutil, 'which', lambda name: name if name == 'clip.exe' else None)
    command = Mock(return_value=True)
    monkeypatch.setattr(clipboard, '_command_copy', command)
    value = 'flag{中文}\n'
    result = clipboard.copy_text(value, Mock())
    assert result.confirmed
    command.assert_called_once_with(['clip.exe'], value.encode('utf-16-le'))


def test_read_macos_preserves_unicode_and_trailing_newline(monkeypatch):
    monkeypatch.setattr(clipboard.sys, 'platform', 'darwin')
    run = Mock(return_value=subprocess.CompletedProcess('pbpaste', 0, '中文\nsecond\n'.encode()))
    monkeypatch.setattr(clipboard.subprocess, 'run', run)
    assert clipboard.read_text() == '中文\nsecond\n'
    assert run.call_args.args == (['/usr/bin/pbpaste'],)
    assert 'shell' not in run.call_args.kwargs


def test_read_linux_tries_available_desktop_routes_and_preserves_empty_text(monkeypatch):
    monkeypatch.setattr(clipboard.sys, 'platform', 'linux')
    monkeypatch.setenv('WAYLAND_DISPLAY', 'test')
    monkeypatch.setenv('DISPLAY', ':test')
    monkeypatch.setattr(clipboard.shutil, 'which', lambda name: '/usr/bin/'+name if name in ('wl-paste','xclip') else None)
    command = Mock(side_effect=[None, ''])
    native = Mock(side_effect=AssertionError('empty clipboard is a successful read'))
    monkeypatch.setattr(clipboard, '_command_read', command)
    monkeypatch.setattr(clipboard, '_x11_read', native)
    assert clipboard.read_text() == ''
    assert command.call_args_list[0].args == (['/usr/bin/wl-paste','--no-newline','--type','text'],)
    assert command.call_args_list[1].args == (['/usr/bin/xclip','-selection','clipboard','-out','-target','UTF8_STRING'],)


def test_read_ssh_does_not_read_the_remote_desktop(monkeypatch):
    monkeypatch.setenv('SSH_TTY', 'remote')
    for function in ('_windows_read','_command_read','_x11_read'):
        monkeypatch.setattr(clipboard, function, Mock(side_effect=AssertionError('must not read server clipboard')))
    assert clipboard.read_text() is None


def test_read_windows_uses_full_64bit_handles_and_unlocks_system_memory(monkeypatch):
    raw = '中文\nsecond\n'.encode('utf-16-le')+b'\0\0\x00\xd8'
    memory = ctypes.create_string_buffer(raw)
    user, kernel = Mock(), Mock()
    user.OpenClipboard.return_value = True
    user.GetClipboardData.return_value = 0x123456789
    kernel.GlobalSize.return_value = len(raw)
    kernel.GlobalLock.return_value = ctypes.addressof(memory)
    monkeypatch.setattr(clipboard.ctypes, 'WinDLL', lambda name, **_: user if name == 'user32' else kernel, raising=False)
    assert clipboard._windows_read() == '中文\nsecond\n'
    kernel.GlobalLock.assert_called_once_with(0x123456789)
    kernel.GlobalUnlock.assert_called_once_with(0x123456789)
    user.CloseClipboard.assert_called_once()
    kernel.GlobalFree.assert_not_called()


@pytest.mark.parametrize('incremental', [False, True])
def test_native_x11_reads_unicode_and_incremental_clipboard(monkeypatch, incremental):
    from collections import deque
    atoms = dict(zip(('CLIPBOARD','UTF8_STRING','STRING','INCR','CTFBOT_CLIPBOARD_READ'), range(1,6)))
    x = Mock()
    x.XOpenDisplay.return_value = 123
    x.XCreateSimpleWindow.return_value = 42
    x.XGetSelectionOwner.return_value = 55
    x.XInternAtom.side_effect = lambda display, name, only: atoms[name.decode()]
    x.XPending.return_value = 1
    events = deque([31,28,28] if incremental else [31])
    def next_event(display, pointer):
        event = pointer._obj
        event.type = events.popleft()
        if event.type == 31:
            event.notify.requestor = 42
            event.notify.target = atoms['UTF8_STRING']
            event.notify.property = atoms['CTFBOT_CLIPBOARD_READ']
        else:
            prop = ctypes.cast(pointer, ctypes.POINTER(clipboard._PropertyNotify)).contents
            prop.window, prop.atom, prop.state = 42, atoms['CTFBOT_CLIPBOARD_READ'], 0
    x.XNextEvent.side_effect = next_event
    raw = '中文\nsecond\n'.encode()
    values = deque([(atoms['INCR'],32,b''),(atoms['UTF8_STRING'],8,raw),(atoms['UTF8_STRING'],8,b'')]
                   if incremental else [(atoms['UTF8_STRING'],8,raw)])
    buffers = []
    def get_property(display, window, prop, offset, length, delete, requested, actual, bits, count, after, data):
        actual._obj.value, bits._obj.value, payload = values.popleft()
        count._obj.value = len(payload)
        after._obj.value = 0
        buffer = ctypes.create_string_buffer(payload)
        buffers.append(buffer)
        data._obj.value = ctypes.addressof(buffer)
        return 0
    x.XGetWindowProperty.side_effect = get_property
    monkeypatch.setattr(clipboard, '_load_x11', lambda: x)
    assert clipboard._x11_read() == '中文\nsecond\n'
    assert x.XFree.call_count == (3 if incremental else 1)
    x.XDestroyWindow.assert_called_once_with(123,42)
    x.XCloseDisplay.assert_called_once_with(123)
