import test from 'node:test';
import assert from 'node:assert/strict';
import { basename } from 'node:path';
import { tools } from '../helpers.js';
import { DockerRuntime, resolveImage } from '../../src/runtime/docker.js';
import { SkillCatalog } from '../../src/skills/catalog.js';
import { installSkills } from '../../src/tools/skills.js';
import { loadSdk } from '../../src/pi/sdk.js';

test('captured periodic image helper separates synthetic phases in the current sandbox', async t => {
  const data = await tools(t);
  const runtime = new DockerRuntime({ input: data.workspace.input,
    image: await resolveImage(process.env.CTFBOT_DOCKER_TEST_IMAGE ?? 'ctfbot-tools:candidate'), runId: basename(data.evidence.root) });
  t.after(() => runtime.close());
  await runtime.start();
  const catalog = await SkillCatalog.create({ sdk: await loadSdk(), ...data, userDirectory: null });
  installSkills(data.registry, catalog);
  const helper = await data.call('skill_materialize', { path: 'builtin/ctf-forensics/scripts/periodic_image.py' });
  const resource = data.state.select('assets', [helper.asset_id])[0];
  await runtime.write('/work/periodic_image.py', await data.evidence.read(resource.artifact.artifact));
  const source = `import json,subprocess,pathlib,hashlib
from PIL import Image
im=Image.new('RGB',(9,6),'black')
for y in range(6):
 for x in range(9): im.putpixel((x,y),((x%3)*100,y*30,0))
im.save('/work/synthetic.png')
helper='/work/periodic_image.py'
def run(*args):
 p=subprocess.run(['python3',helper,'/work/synthetic.png',*args],capture_output=True,text=True)
 assert p.returncode==0,p.stderr
 return json.loads(p.stdout)
out=run('--output','/work/compact','--periods','3','--axes','cols','--save-candidates')
assert out['candidate_count']==3 and out['ranked']==False
manifest=json.loads(pathlib.Path('/work/compact/manifest.json').read_text())
assert manifest['source_sha256']==hashlib.sha256(pathlib.Path('/work/synthetic.png').read_bytes()).hexdigest()
for phase in range(3):
 result=Image.open('/work/compact/cols-p3-phase%d-compact.png'%phase)
 assert result.size==(3,6)
 assert all(result.getpixel((x,y))==(phase*100,y*30,0) for x in range(3) for y in range(6))
run('--output','/work/mask','--periods','3','--axes','rows','--phase','1','--mode','mask','--save-candidates')
masked=Image.open('/work/mask/rows-p3-phase1-mask.png')
assert masked.size==(9,6) and masked.getpixel((2,1))==im.getpixel((2,1)) and masked.getpixel((2,0))==(255,255,255)
run('--output','/work/default')
assert len(json.loads(pathlib.Path('/work/default/manifest.json').read_text())['candidates'])==70
for args in [('--periods','99'),('--periods','3','--phase','3'),('--save-candidates',)]:
 bad=subprocess.run(['python3',helper,'/work/synthetic.png','--output','/work/rejected',*args],capture_output=True)
 assert bad.returncode!=0
print('verified synthetic phase separation, masked coordinates, coverage and bounds')`;
  const result = await runtime.execute(['python3', '-I', '-c', source]);
  assert.equal(result.code, 0, result.stderr.toString());
  assert.match(result.stdout.toString(), /verified synthetic/);
});
