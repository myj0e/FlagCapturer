"""Check that GUI entry points create windows on an internal Xvfb display."""
import json
import os
import signal
import re
import subprocess
import time

checks = []
for name, argv in [('Audacity', ['audacity']), ('PulseView', ['pulseview']),
                   ('NetworkMiner', ['networkminer']), ('Burp', ['burpsuite'])]:
    env = {**os.environ, 'QT_QPA_PLATFORM': 'xcb'}
    log = '/work/' + name.lower() + '-startup.log'
    with open(log, 'w') as output:
        process = subprocess.Popen(argv, stdout=output, stderr=output, env=env, start_new_session=True)
        try:
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                time.sleep(1)
                windows = subprocess.run(['xwininfo', '-root', '-tree'], capture_output=True, text=True, check=True).stdout
                errors = open(log).read()
                if any(marker in errors for marker in ['Unhandled Exception', 'FATAL UNHANDLED', 'Could not load file or assembly']):
                    raise RuntimeError(f'{name}: ' + errors[-2000:])
                # Ignore hidden 1x1 bootstrap and Qt selection-owner windows.
                matches = [line for line in windows.splitlines() if name.lower() in line.lower()
                           and any(int(w) >= 100 and int(h) >= 50 for w, h in re.findall(r' (\d+)x(\d+)[+-]', line))]
                if matches:
                    checks.append({'tool': name, 'window_created': True, 'windows': windows[-2000:]})
                    break
                if process.poll() is not None:
                    raise RuntimeError(f'{name} exited {process.returncode}: ' + open(log).read()[-2000:])
            else:
                raise RuntimeError(f'{name}: no window: ' + open(log).read()[-2000:])
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
print(json.dumps({'gui_startup_ok': True, 'checks': checks}, indent=2))
