// Fixed, reviewed recipes. The model selects IDs, never repositories, versions or install commands.
export const recipes = {
  base58: { title: 'Base58 编解码', binaries: [], modules: ['base58'], wheels: ['base58'], verify: 'import base58; assert base58.b58decode(base58.b58encode(b"CTF\\x00"))==b"CTF\\x00"' },
  ocr: { title: 'OCR / OpenCV', aliases: ['tesseract', 'cv2', 'pytesseract', 'opencv'], binaries: ['tesseract'], modules: ['cv2', 'pytesseract', 'PIL'],
    verify: "import cv2,numpy as np,pytesseract; assert cv2.cvtColor(np.zeros((2,2,3),dtype=np.uint8),cv2.COLOR_BGR2GRAY).shape==(2,2); assert 'eng' in pytesseract.get_languages(config='')" },
  reverse: { title: 'Ghidra / Pwndbg / Android', aliases: ['ghidra', 'pwndbg', 'jadx', 'apktool', 'frida'], binaries: ['analyzeHeadless', 'pwndbg', 'jadx', 'apktool'], modules: ['frida'] },
  crypto: { title: '密码学 / Sage', aliases: ['z3', 'Crypto', 'sage', 'pycryptodome'], binaries: ['sage'], modules: ['z3', 'Crypto'], verify: 'import z3; s=z3.Solver(); s.add(z3.Int("x")==42); assert s.check()==z3.sat' },
  forensics: { title: '取证', aliases: ['volatility3', 'tshark', 'sleuthkit'], binaries: ['vol', 'tshark', 'fls'], modules: ['volatility3'] },
  blockchain: { title: 'Foundry / Web3', aliases: ['forge', 'cast', 'anvil', 'solc', 'web3'], binaries: ['forge', 'cast', 'anvil', 'solc'], modules: ['web3'], verify: 'from web3 import Web3; assert Web3.to_int(hexstr="0x2a")==42' },
  signal: { title: '音频 / 信号', aliases: ['audacity', 'pulseview', 'sigrok', 'gnuradio'], binaries: ['audacity', 'pulseview', 'sigrok-cli', 'gnuradio-companion'], modules: [] },
  web: { title: 'Web / gRPC / CyberChef', aliases: ['grpcurl', 'chromium', 'firefox', 'burp', 'cyberchef'], binaries: ['grpcurl', 'chromium', 'firefox-esr', 'burpsuite', 'cyberchef'], modules: [] },
  construct: { title: 'Construct 二进制结构', binaries: [], modules: ['construct'], wheels: ['construct'], verify: 'import construct; assert construct.Int16ul.parse(b"\\x2a\\x00")==42' },
  dpkt: { title: 'dpkt 网络包解析', binaries: [], modules: ['dpkt'], wheels: ['dpkt'], verify: 'import dpkt; assert dpkt.ip.IP(bytes(dpkt.ip.IP(src=b"\\x7f\\x00\\x00\\x01"))).src==b"\\x7f\\x00\\x00\\x01"' },
  pefile: { title: 'PE 文件解析', binaries: [], modules: ['pefile'], wheels: ['pefile'], verify: 'import pefile; assert pefile.MACHINE_TYPE[0x14c]=="IMAGE_FILE_MACHINE_I386"' }
};
export function selectRecipes(names) {
  return [...new Set(names.map(name => Object.hasOwn(recipes, name) ? name : Object.keys(recipes).find(id => recipes[id].aliases?.includes(name))))];
}
export function probeSource(selected) {
  return `import importlib,json,shutil\nmissing=[]\n` + selected.map(id => {
    const recipe = recipes[id];
    return `for name in ${JSON.stringify(recipe.binaries)}:\n if shutil.which(name) is None: missing.append(name)\nfor name in ${JSON.stringify(recipe.modules)}:\n try: importlib.import_module(name)\n except Exception: missing.append(name)\nif not missing:\n try:\n  exec(${JSON.stringify(recipe.verify ?? 'pass')})\n except Exception as e: missing.append(str(e)[:256])\n`;
  }).join('') + 'print(json.dumps({"missing":missing,"ready":not missing}))';
}
