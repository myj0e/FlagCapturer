import { mkdtemp, rm, writeFile, readdir, chmod } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { importFile } from '../src/challenge/workspace.js';
import { EvidenceStore } from '../src/evidence/store.js';
import { RunState } from '../src/evidence/state.js';
import { ToolRegistry } from '../src/tools/registry.js';
import { installCore } from '../src/tools/core.js';
export const IMAGE = 'sha256:' + 'a'.repeat(64);
export async function fixture(t, {
  authorized = true,
  content = 'flag{synthetic_fixture}'
} = {}) {
  const root = await mkdtemp(join(tmpdir(), 'flagcapturer-test-'));
  t.after(async () => {
    async function writable(path) {
      await chmod(path, 0o700);
      for (const item of await readdir(path, {
        withFileTypes: true
      })) if (item.isDirectory() && !item.isSymbolicLink()) await writable(join(path, item.name));
    }
    await writable(root);
    await rm(root, {
      recursive: true,
      force: true
    });
  });
  const file = join(root, 'attachment.txt');
  await writeFile(file, content);
  const workspace = await importFile(file, join(root, 'imports'), {
    authorized,
    basis: authorized ? 'Synthetic test fixture consent' : ''
  });
  return {
    root,
    file,
    workspace
  };
}
export async function tools(t, options = {}) {
  const fixtureData = await fixture(t, options);
  const evidence = await EvidenceStore.create(join(fixtureData.root, 'runs'));
  const state = new RunState(evidence, {
    run_id: 'synthetic',
    image: IMAGE
  });
  const registry = new ToolRegistry(state, evidence);
  const runtime = {
    execute: async () => ({
      code: 0,
      stdout: Buffer.from('ok'),
      stderr: Buffer.alloc(0),
      timedOut: false,
      truncated: false
    }),
    write: async () => {},
    export: async () => Buffer.from('checkpoint')
  };
  const sessions = {
    list: () => [],
    start: async () => ({
      session_id: 'fake'
    }),
    read: async () => ({
      state: 'exited'
    }),
    close: async () => ({
      state: 'closed'
    })
  };
  await installCore(registry, {
    workspace: fixtureData.workspace,
    runtime,
    sessions,
    supportsImages: options.supportsImages
  });
  const call = async (name, args) => JSON.parse((await registry.invoke(name, args)).content[0].text);
  return {
    ...fixtureData,
    evidence,
    state,
    registry,
    runtime,
    sessions,
    call
  };
}
