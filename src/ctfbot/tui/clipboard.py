"""Best-effort system clipboard access using only Python's standard library.

Windows uses Win32; macOS uses its bundled pbcopy. Linux uses available desktop
helpers or the native X11 selection protocol. Remote sessions use terminal OSC 52
instead of copying to the remote host's desktop. A terminal request is never
reported as a confirmed system write.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import select
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class CopyResult:
    message: str
    confirmed: bool


def _command_copy(command: list[str], value: bytes) -> bool:
    try:
        subprocess.run(command, input=value, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=3, check=True,
                       env={**os.environ, "LC_CTYPE": "UTF-8"})
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _windows_copy(value: str) -> bool:
    # Explicit signatures are essential: default ctypes int truncates 64-bit handles.
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                    wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                    wintypes.HINSTANCE, ctypes.c_void_p]
    user.CreateWindowExW.restype = wintypes.HWND
    user.DestroyWindow.argtypes = [wintypes.HWND]
    user.DestroyWindow.restype = wintypes.BOOL
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.OpenClipboard.restype = wintypes.BOOL
    user.EmptyClipboard.restype = wintypes.BOOL
    user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user.SetClipboardData.restype = wintypes.HANDLE
    user.CloseClipboard.restype = wintypes.BOOL
    kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = wintypes.HANDLE
    kernel.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel.GlobalFree.argtypes = [wintypes.HANDLE]
    kernel.GlobalFree.restype = wintypes.HANDLE
    if "\0" in value:
        return False  # CF_UNICODETEXT cannot preserve embedded NULs.
    data = value.encode("utf-16-le") + b"\0\0"
    # EmptyClipboard requires a real owner window; NULL makes SetClipboardData fail.
    window = user.CreateWindowExW(0, "STATIC", "ctfbot-clipboard", 0, 0, 0, 0, 0,
                                  None, None, None, None)
    if not window:
        return False
    handle = kernel.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
    if not handle:
        user.DestroyWindow(window)
        return False
    opened = False
    try:
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            return False
        ctypes.memmove(pointer, data, len(data))
        kernel.GlobalUnlock(handle)
        opened = bool(user.OpenClipboard(window))
        if not opened or not user.EmptyClipboard():
            return False
        if not user.SetClipboardData(13, handle):  # CF_UNICODETEXT
            return False
        handle = None  # Windows now owns the allocation.
        return True
    finally:
        if opened:
            user.CloseClipboard()
        if handle:
            kernel.GlobalFree(handle)
        user.DestroyWindow(window)


# Xlib event layouts from X11/Xlib.h. Atom, Window and Time are unsigned long.
class _SelectionRequest(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("owner", ctypes.c_ulong), ("requestor", ctypes.c_ulong),
                ("selection", ctypes.c_ulong), ("target", ctypes.c_ulong),
                ("property", ctypes.c_ulong), ("time", ctypes.c_ulong)]


class _SelectionNotify(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("requestor", ctypes.c_ulong), ("selection", ctypes.c_ulong),
                ("target", ctypes.c_ulong), ("property", ctypes.c_ulong),
                ("time", ctypes.c_ulong)]


class _XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("request", _SelectionRequest),
                ("notify", _SelectionNotify), ("pad", ctypes.c_long * 24)]


_X_INIT_LOCK = threading.Lock()
_X_INITIALIZED = False
_X_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(lambda *_: 0)


def _load_x11():
    global _X_INITIALIZED
    library = ctypes.util.find_library("X11")
    if not library:
        raise OSError("X11 unavailable")
    x = ctypes.CDLL(library)
    with _X_INIT_LOCK:
        if not _X_INITIALIZED:
            x.XInitThreads.argtypes = []
            x.XInitThreads.restype = ctypes.c_int
            if not x.XInitThreads():
                raise OSError("X11 threading unavailable")
            # A requestor may close its window before receiving our reply.
            # The default Xlib error handler would terminate the entire TUI.
            x.XSetErrorHandler.argtypes = [type(_X_ERROR_HANDLER)]
            x.XSetErrorHandler.restype = ctypes.c_void_p
            x.XSetErrorHandler(_X_ERROR_HANDLER)
            _X_INITIALIZED = True
    pointer, atom, integer = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int
    signatures = {
        "XOpenDisplay": ([ctypes.c_char_p], pointer),
        "XDefaultRootWindow": ([pointer], atom),
        "XCreateSimpleWindow": ([pointer, atom, integer, integer, ctypes.c_uint,
                                 ctypes.c_uint, ctypes.c_uint, atom, atom], atom),
        "XInternAtom": ([pointer, ctypes.c_char_p, integer], atom),
        "XSetSelectionOwner": ([pointer, atom, atom, atom], integer),
        "XGetSelectionOwner": ([pointer, atom], atom),
        "XChangeProperty": ([pointer, atom, atom, atom, integer, integer, pointer, integer], integer),
        "XSendEvent": ([pointer, atom, integer, ctypes.c_long, ctypes.POINTER(_XEvent)], integer),
        "XNextEvent": ([pointer, ctypes.POINTER(_XEvent)], integer),
        "XPending": ([pointer], integer),
        "XConnectionNumber": ([pointer], integer),
        "XFlush": ([pointer], integer),
        "XDestroyWindow": ([pointer, atom], integer),
        "XCloseDisplay": ([pointer], integer),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(x, name)
        function.argtypes, function.restype = arguments, result
    return x


def _x11_copy(value: str) -> bool:
    # Keep ownership alive while the TUI runs; another copy ends the old owner.
    # Larger transfers require INCR; let helpers / OSC 52 handle those instead.
    raw = value.encode("utf-8")
    if len(raw) > 64 * 1024:
        return False
    ready = threading.Event()
    cancelled = threading.Event()
    success = []

    def serve() -> None:
        display = None
        window = 0
        try:
            x = _load_x11()
            display = x.XOpenDisplay(None)
            if not display:
                return
            window = x.XCreateSimpleWindow(display, x.XDefaultRootWindow(display), 0, 0, 1, 1, 0, 0, 0)
            atoms = {name: x.XInternAtom(display, name.encode(), 0)
                     for name in ("CLIPBOARD", "TARGETS", "UTF8_STRING", "TEXT", "STRING", "ATOM")}
            x.XSetSelectionOwner(display, atoms["CLIPBOARD"], window, 0)
            x.XFlush(display)
            if x.XGetSelectionOwner(display, atoms["CLIPBOARD"]) != window or cancelled.is_set():
                return
            success.append(True)
            ready.set()
            while not cancelled.is_set():
                if not x.XPending(display):
                    select.select([x.XConnectionNumber(display)], [], [], .2)
                    continue
                event = _XEvent()
                x.XNextEvent(display, ctypes.byref(event))
                if event.type == 29:  # SelectionClear: another app owns it now.
                    break
                if event.type != 30:  # SelectionRequest
                    continue
                request = event.request
                property_atom = request.property or request.target
                notify = _XEvent()
                notify.notify = _SelectionNotify(31, 0, 1, display, request.requestor,
                                                request.selection, request.target, 0, request.time)
                if request.target == atoms["TARGETS"]:
                    targets = (ctypes.c_ulong * 4)(atoms["TARGETS"], atoms["UTF8_STRING"], atoms["TEXT"], atoms["STRING"])
                    x.XChangeProperty(display, request.requestor, property_atom, atoms["ATOM"], 32, 0, targets, 4)
                    notify.notify.property = property_atom
                elif request.target in (atoms["UTF8_STRING"], atoms["TEXT"], atoms["STRING"]):
                    data = value.encode("latin-1", errors="replace") if request.target == atoms["STRING"] else raw
                    buffer = ctypes.create_string_buffer(data)
                    encoding = atoms["STRING"] if request.target == atoms["STRING"] else atoms["UTF8_STRING"]
                    x.XChangeProperty(display, request.requestor, property_atom, encoding, 8, 0, buffer, len(data))
                    notify.notify.property = property_atom
                x.XSendEvent(display, request.requestor, 0, 0, ctypes.byref(notify))
                x.XFlush(display)
        except (OSError, AttributeError, ValueError):
            pass
        finally:
            ready.set()
            if display:
                if window:
                    x.XDestroyWindow(display, window)
                x.XCloseDisplay(display)

    threading.Thread(target=serve, name="ctfbot-clipboard", daemon=True).start()
    if not ready.wait(2):
        cancelled.set()
        return False
    return bool(success)


def copy_text(value: str, terminal_copy: Callable[[str], None]) -> CopyResult:
    """Copy exact text; return confirmation only for a successful system backend."""
    remote = any(os.environ.get(key) for key in ("SSH_CONNECTION", "SSH_TTY", "SSH_CLIENT"))
    backend = None
    if not remote:
        try:
            if sys.platform == "win32" and _windows_copy(value):
                backend = "Windows"
            elif sys.platform == "darwin" and _command_copy(["/usr/bin/pbcopy"], value.encode("utf-8")):
                backend = "macOS"
            elif sys.platform.startswith("linux"):
                if os.environ.get("WSL_DISTRO_NAME") and shutil.which("clip.exe"):
                    if _command_copy(["clip.exe"], value.encode("utf-16-le")):
                        backend = "Windows / WSL"
                if not backend:
                    commands = []
                    if os.environ.get("WAYLAND_DISPLAY"):
                        commands.append(("wl-copy", ["--type", "text/plain;charset=utf-8"]))
                    if os.environ.get("DISPLAY"):
                        commands.extend((("xclip", ["-selection", "clipboard", "-in"]), ("xsel", ["--clipboard", "--input"])))
                    for program, arguments in commands:
                        executable = shutil.which(program)
                        if executable and _command_copy([executable, *arguments], value.encode("utf-8")):
                            backend = program
                            break
                    if not backend and os.environ.get("DISPLAY") and _x11_copy(value):
                        backend = "X11"
        except (OSError, AttributeError, ValueError):
            pass
    # Always update Textual's clipboard, and send OSC 52 to the terminal. This
    # covers SSH, tmux and native-backend failures without claiming verification.
    try:
        terminal_copy(value)
    except Exception:
        if not backend:
            return CopyResult("复制失败：系统与终端剪贴板均不可用，请在文本框选择原文复制。", False)
    if backend:
        suffix = "；退出前请粘贴，持久保存取决于桌面剪贴板管理器" if backend == "X11" else ""
        return CopyResult(f"已复制完整原文到系统剪贴板（{backend}）{suffix}。", True)
    return CopyResult("已发送终端复制请求（OSC 52），无法确认写入。终端需允许剪贴板访问；若未生效，请选择原文复制。", False)


_MAX_PASTE_BYTES = 8 * 1024 * 1024


def _command_read(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=3, check=True, env={**os.environ, "LC_CTYPE": "UTF-8"})
        if len(result.stdout) <= _MAX_PASTE_BYTES:
            return result.stdout.decode("utf-8")
    except (OSError, subprocess.SubprocessError, UnicodeError):
        pass
    return None


def _windows_read() -> str | None:
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.OpenClipboard.restype = wintypes.BOOL
    user.CloseClipboard.restype = wintypes.BOOL
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel.GlobalSize.argtypes = [wintypes.HANDLE]
    kernel.GlobalSize.restype = ctypes.c_size_t
    if not user.OpenClipboard(None):
        return None
    try:
        handle = user.GetClipboardData(13)  # CF_UNICODETEXT; memory belongs to Windows.
        if not handle:
            return None
        size = kernel.GlobalSize(handle)
        if not 0 < size <= _MAX_PASTE_BYTES:
            return None
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            return None
        try:
            raw = ctypes.string_at(pointer, size)
            end = next((offset for offset in range(0, len(raw)-1, 2)
                        if raw[offset:offset+2] == b"\0\0"), len(raw))
            return raw[:end].decode("utf-16-le")
        finally:
            kernel.GlobalUnlock(handle)
    finally:
        user.CloseClipboard()


class _PropertyNotify(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong), ("atom", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("state", ctypes.c_int)]


def _x11_read() -> str | None:
    """Read CLIPBOARD, including incremental transfers, without xclip/xsel."""
    import time
    display = None
    window = 0
    try:
        x = _load_x11()
        pointer, atom, integer = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int
        x.XConvertSelection.argtypes = [pointer, atom, atom, atom, atom, atom]
        x.XConvertSelection.restype = integer
        x.XSelectInput.argtypes = [pointer, atom, ctypes.c_long]
        x.XSelectInput.restype = integer
        x.XGetWindowProperty.argtypes = [pointer, atom, atom, ctypes.c_long, ctypes.c_long,
                                         integer, atom, ctypes.POINTER(atom), ctypes.POINTER(integer),
                                         ctypes.POINTER(atom), ctypes.POINTER(atom), ctypes.POINTER(pointer)]
        x.XGetWindowProperty.restype = integer
        x.XFree.argtypes = [pointer]
        x.XFree.restype = integer
        display = x.XOpenDisplay(None)
        if not display:
            return None
        window = x.XCreateSimpleWindow(display, x.XDefaultRootWindow(display), 0, 0, 1, 1, 0, 0, 0)
        atoms = {name: x.XInternAtom(display, name.encode(), 0)
                 for name in ("CLIPBOARD", "UTF8_STRING", "STRING", "INCR", "CTFBOT_CLIPBOARD_READ")}
        if not x.XGetSelectionOwner(display, atoms["CLIPBOARD"]):
            return None
        x.XSelectInput(display, window, 1 << 22)  # PropertyChangeMask, for INCR.
        deadline = time.monotonic() + 2

        def next_event():
            while time.monotonic() < deadline:
                if x.XPending(display):
                    event = _XEvent()
                    x.XNextEvent(display, ctypes.byref(event))
                    return event
                select.select([x.XConnectionNumber(display)], [], [], min(.05, max(0, deadline-time.monotonic())))
            return None

        def read_property():
            actual_type, count, after = atom(), atom(), atom()
            bits, data = integer(), pointer()
            result = x.XGetWindowProperty(display, window, atoms["CTFBOT_CLIPBOARD_READ"], 0,
                                          _MAX_PASTE_BYTES // 4, 1, 0, ctypes.byref(actual_type),
                                          ctypes.byref(bits), ctypes.byref(count), ctypes.byref(after), ctypes.byref(data))
            try:
                if result or after.value or count.value > _MAX_PASTE_BYTES:
                    return None
                raw = ctypes.string_at(data, count.value) if data.value and bits.value == 8 else b""
                return actual_type.value, bits.value, raw
            finally:
                if data.value:
                    x.XFree(data)
                x.XFlush(display)  # Deleting the property acknowledges an INCR chunk.

        for target_name in ("UTF8_STRING", "STRING"):
            target = atoms[target_name]
            x.XConvertSelection(display, atoms["CLIPBOARD"], target, atoms["CTFBOT_CLIPBOARD_READ"], window, 0)
            x.XFlush(display)
            while (event := next_event()) is not None:
                if event.type != 31 or event.notify.requestor != window or event.notify.target != target:
                    continue
                if not event.notify.property:
                    break  # Owner doesn't support this target; try STRING.
                value = read_property()
                if value is None:
                    return None
                actual_type, bits, raw = value
                if actual_type == atoms["INCR"]:
                    chunks = bytearray()
                    while (chunk_event := next_event()) is not None:
                        prop = ctypes.cast(ctypes.byref(chunk_event), ctypes.POINTER(_PropertyNotify)).contents
                        if chunk_event.type != 28 or prop.window != window or prop.atom != atoms["CTFBOT_CLIPBOARD_READ"] or prop.state != 0:
                            continue
                        value = read_property()
                        if value is None or value[1] != 8:
                            return None
                        raw = value[2]
                        if not raw:
                            return bytes(chunks).decode("latin-1" if target_name == "STRING" else "utf-8")
                        chunks.extend(raw)
                        if len(chunks) > _MAX_PASTE_BYTES:
                            return None
                    return None
                if bits != 8:
                    return None
                return raw.decode("latin-1" if target_name == "STRING" else "utf-8")
        return None
    except (OSError, AttributeError, ValueError, UnicodeError):
        return None
    finally:
        if display:
            if window:
                x.XDestroyWindow(display, window)
            x.XCloseDisplay(display)


def read_text() -> str | None:
    """Read the local system clipboard; None means unavailable, not empty text.

    Never read the server's desktop clipboard in SSH sessions. Terminal-generated
    Paste events and the application's own clipboard cover that case instead.
    """
    if any(os.environ.get(key) for key in ("SSH_CONNECTION", "SSH_TTY", "SSH_CLIENT")):
        return None
    try:
        if sys.platform == "win32":
            return _windows_read()
        if sys.platform == "darwin":
            return _command_read(["/usr/bin/pbpaste"])
        if sys.platform.startswith("linux"):
            commands = []
            if os.environ.get("WSL_DISTRO_NAME") and shutil.which("powershell.exe"):
                commands.append(("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
                    "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); $t=Get-Clipboard -Raw; if($null -ne $t){[Console]::Write($t)}"]))
            if os.environ.get("WAYLAND_DISPLAY"):
                commands.append(("wl-paste", ["--no-newline", "--type", "text"]))
            if os.environ.get("DISPLAY"):
                commands.extend((("xclip", ["-selection", "clipboard", "-out", "-target", "UTF8_STRING"]),
                                 ("xsel", ["--clipboard", "--output"])))
            for program, arguments in commands:
                executable = shutil.which(program)
                if executable:
                    value = _command_read([executable, *arguments])
                    if value is not None:
                        return value
            if os.environ.get("DISPLAY"):
                return _x11_read()
    except (OSError, AttributeError, ValueError, UnicodeError):
        pass
    return None
