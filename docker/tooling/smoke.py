"""Functional checks run inside the same restricted Docker configuration as solving."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

results = []


def command(label, argv, timeout=45, expected=0):
    started = time.monotonic()
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    output = (p.stdout + p.stderr).strip()
    if p.returncode != expected:
        raise RuntimeError(f'{label}: exit={p.returncode}\n{output[-3000:]}')
    results.append({'check': label, 'seconds': round(time.monotonic() - started, 2), 'output': output[-1000:]})
    return output


Path('/work/.cache').mkdir(parents=True, exist_ok=True)
command('Python dependency consistency', ['python3', '-m', 'pip', 'check'])
command('Inventory', ['python3', '/opt/ctfbot/inventory.py'])
command('OCR and OpenCV', ['python3', '-c', '''
import cv2,numpy as np,pytesseract
from PIL import Image,ImageDraw,ImageFont
image=Image.new('RGB',(600,100),'white')
ImageDraw.Draw(image).text((20,15),'CTF 12345',fill='black',font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',48))
image.save('/work/ocr.png')
gray=cv2.cvtColor(np.array(image),cv2.COLOR_RGB2GRAY)
_,mask=cv2.threshold(gray,127,255,cv2.THRESH_BINARY_INV)
assert cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]
text=pytesseract.image_to_string(image,config='--psm 7',lang='eng').strip()
assert ''.join(text.split())=='CTF12345',repr(text)
assert {'eng','chi_sim'} <= set(pytesseract.get_languages(config=''))
print(cv2.__version__,text,pytesseract.get_tesseract_version())
'''])
for label, argv in [
    ('gRPC', ['grpcurl', '-version']), ('JADX', ['jadx', '--version']),
    ('APK resources', ['apktool', '--version']), ('Android bridge', ['adb', 'version']),
    ('Frida', ['frida', '--version']), ('Volatility', ['vol', '--help']),
    ('Sleuth Kit', ['fls', '-V']), ('forge', ['forge', '--version']),
    ('cast', ['cast', '--version']), ('anvil', ['anvil', '--version']),
    ('solc', ['solc', '--version']), ('Signal decoders', ['sigrok-cli', '--version']),
    ('GDB with pwndbg', ['pwndbg', '--batch', '-ex', 'help context']),
    ('GNU Radio Debian Python', ['/usr/bin/python3', '-c', 'from gnuradio import gr; print(gr.version())']),
    ('Wireshark GUI binary', ['wireshark', '--version']),
    ('Firefox', ['firefox-esr', '--version']),
    ('Chromium offline renderer', ['chromium', '--headless', '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--user-data-dir=/work/.cache/chromium', '--dump-dom', 'data:text/html,<h1>offline-browser-ok</h1>']),
]:
    command(label, argv, timeout=60)
Path('/work/Smoke.sol').write_text('pragma solidity ^0.8.0; contract Smoke { function answer() public pure returns(uint) { return 42; } }')
command('Offline Solidity compilation', ['solc', '--bin', '/work/Smoke.sol'])
Path('/work/forge/src').mkdir(parents=True, exist_ok=True)
Path('/work/forge/src/Smoke.sol').write_text(Path('/work/Smoke.sol').read_text())
command('Foundry offline build', ['forge', 'build', '--root', '/work/forge', '--offline', '--use', '/usr/local/bin/solc'])
with open('/work/anvil.log', 'w') as log:
    chain = subprocess.Popen(['anvil', '--host', '127.0.0.1', '--port', '18545', '--silent'], stdout=log, stderr=log)
    try:
        for _ in range(50):
            try:
                request = urllib.request.Request('http://127.0.0.1:18545', data=b'{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}', headers={'Content-Type': 'application/json'})
                result = json.load(urllib.request.urlopen(request, timeout=1))
                assert result['result'] == '0x7a69', result
                from web3 import Web3
                assert Web3(Web3.HTTPProvider('http://127.0.0.1:18545')).eth.chain_id == 31337
                results.append({'check': 'Anvil local RPC', 'output': result})
                break
            except OSError:
                if chain.poll() is not None:
                    raise RuntimeError(Path('/work/anvil.log').read_text())
                time.sleep(0.1)
        else:
            raise RuntimeError('Anvil local RPC did not become ready')
    finally:
        chain.terminate()
        chain.wait(timeout=5)

Path('/work/smoke.c').write_text('int main(void) { return 42; }')
command('Build ELF fixture', ['gcc', '/work/smoke.c', '-o', '/work/smoke'])
command('Ghidra headless analysis', ['analyzeHeadless', '/work', 'ghidra-smoke', '-import', '/work/smoke', '-deleteProject'], timeout=120)
print(json.dumps({'ok': True, 'checks': results}, ensure_ascii=False, indent=2))
