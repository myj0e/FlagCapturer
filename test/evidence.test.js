import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { writeFile, unlink } from 'node:fs/promises';
import { fixture, IMAGE } from './helpers.js';
import { EvidenceStore, audit, readEvents } from '../src/evidence/store.js';
import { DockerRuntime } from '../src/runtime/docker.js';
import { runProcess } from '../src/runtime/process.js';
test('audit detects corrupt and missing nested evidence', async t => {
  const {
      root
    } = await fixture(t),
    store = await EvidenceStore.create(join(root, 'runs'));
  const raw = await store.artifact('raw bytes');
  const observation = await store.artifact({
    stdout: raw
  }, 'application/json');
  await store.append('observation', {
    result: observation
  });
  assert.equal((await audit(store.root)).ok, true);
  await unlink(join(store.root, raw.artifact));
  assert.equal((await audit(store.root)).ok, false);
  await writeFile(join(store.root, raw.artifact), 'corrupt');
  await assert.rejects(store.read(raw.artifact), /integrity/);
});
test('concurrent evidence append has contiguous ordered sequences', async t => {
  const {
      root
    } = await fixture(t),
    store = await EvidenceStore.create(join(root, 'runs'));
  await Promise.all(Array.from({
    length: 20
  }, (_, index) => store.append('fixture', {
    index
  })));
  assert.deepEqual((await readEvents(store.root)).map(x => x.sequence), Array.from({
    length: 20
  }, (_, i) => i + 1));
  const refs = await Promise.all(Array.from({
    length: 10
  }, () => store.artifact('identical')));
  assert.equal(new Set(refs.map(x => x.artifact)).size, 1);
  assert.equal((await store.read(refs[0].artifact)).toString(), 'identical');
});
test('actual container supervisor program retains partial output and kills expired process group', async t => {
  const {
    root
  } = await fixture(t);
  const runtime = new DockerRuntime({
    image: IMAGE
  });
  runtime.started = true;
  const args = runtime.execArgs(['/bin/sh', '-c', 'printf progress; sleep 5'], 'synthetic', 100, 4096);
  const supervisor = args[7].replaceAll('/work/.control/', root + '/');
  const result = await runProcess('python3', ['-I', '-u', '-c', supervisor, 'synthetic', '0.1', '4096', JSON.stringify(['/bin/sh', '-c', 'printf progress; sleep 5'])], {
    timeout: 3000
  });
  assert.equal(result.code, 124);
  assert.equal(result.stdout.toString(), 'progress');
  assert.equal(result.timedOut, false);
});
test('supervisor honors cancellation before launch and during PID publication', async t => {
  const {
    root
  } = await fixture(t);
  const runtime = new DockerRuntime({
    image: IMAGE,
    runner: async (_cmd, args) => runProcess('python3', ['-I', '-c', args[5].replaceAll('/work/.control/', root + '/'), args[6]])
  });
  runtime.started = true;
  const key = 'cancel-before';
  await runtime.killSession(key);
  const argv = ['python3', '-c', "import time; time.sleep(10)"];
  const supervisor = runtime.execArgs(argv, key, 1000, 4096)[7].replaceAll('/work/.control/', root + '/');
  const before = await runProcess('python3', ['-I', '-c', supervisor, key, '1', '4096', JSON.stringify(argv)]);
  assert.equal(before.code, 130);
  const {
    access
  } = await import('node:fs/promises');
  const {
    setTimeout: delay
  } = await import('node:timers/promises');
  const paused = supervisor.replace("try:\n open(", `open(${JSON.stringify(join(root, 'spawned'))},'w').close()\nimport time; time.sleep(0.3)\ntry:\n open(`);
  const running = runProcess('python3', ['-I', '-c', paused, 'cancel-during', '2', '4096', JSON.stringify(argv)], {
    timeout: 3000
  });
  let spawned = false;
  for (let n = 0; n < 100; n++) {
    try {
      await access(join(root, 'spawned'));
      spawned = true;
      break;
    } catch {}
    await delay(10);
  }
  assert.ok(spawned);
  await runtime.killSession('cancel-during');
  const during = await running;
  assert.equal(during.code, 130, during.stderr.toString());
  assert.equal(during.timedOut, false);
});
