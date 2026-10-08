"""Python source executed inside tooling containers, never on the controller.

Subreaping lets cleanup include double-forked/setsid descendants as well as the
original process group. A host watchdog closes the sandbox if cleanup fails.
"""

SUPERVISOR = r'''
import ctypes, os, signal, subprocess, sys, time
budget, marker = float(sys.argv[1]), sys.argv[2]
def report(status, code):
    if marker == "session":
        if status != "ok":
            print("[script %s]" % status, file=sys.stderr, flush=True)
        sys.exit(code if code >= 0 else 128 - code)
    os.write(2, ("\n" + marker + ":" + status + ":" + str(code) + "\n").encode())
    sys.exit(0)
if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
    report("cleanup_failed", 125)
stopped = False
def stop(signum, frame):
    global stopped
    stopped = True
if marker == "session":
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
try:
    child = subprocess.Popen(sys.argv[3:], start_new_session=True)
except OSError as exc:
    code = 127 if isinstance(exc, FileNotFoundError) else 126
    print(str(exc), file=sys.stderr, flush=True)
    report("ok", code)
status = "ok"
try:
    deadline = time.monotonic() + budget
    while True:
        if stopped:
            raise InterruptedError()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(sys.argv[3:], budget)
        try:
            code = child.wait(timeout=min(.1, remaining))
            break
        except subprocess.TimeoutExpired:
            pass
except (subprocess.TimeoutExpired, InterruptedError) as exc:
    status, code = ("stopped", 130) if isinstance(exc, InterruptedError) else ("timeout", 124)
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.kill()
    child.wait(timeout=1)
# Clean up background descendants even when the main command exited normally.
# When their parents die, detached grandchildren are adopted by this subreaper.
deadline = time.monotonic() + 2
children_file = "/proc/self/task/%d/children" % os.getpid()
while True:
    try:
        while os.waitpid(-1, os.WNOHANG)[0]:
            pass
    except ChildProcessError:
        break
    with open(children_file) as stream:
        children = stream.read().split()
    for pid in children:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    if time.monotonic() >= deadline:
        status, code = "cleanup_failed", 125
        break
    time.sleep(.01)
report(status, code)
'''

# Old minimal images without Python retain the host watchdog / fail-closed path.
SUPERVISOR_SHELL = '''source=$1; budget=$2; marker=$3; shift 3
if command -v python3 >/dev/null 2>&1; then
exec python3 -I -u -c "$source" "$budget" "$marker" "$@"
else
"$@"
code=$?
printf '\\n%s:legacy:%s\\n' "$marker" "$code" >&2
exit 0
fi'''
