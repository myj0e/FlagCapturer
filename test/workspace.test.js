import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { chmod, writeFile, symlink, mkdir, readFile } from 'node:fs/promises';
import { fixture } from './helpers.js';
import { preview, importFile, snapshot } from '../src/challenge/workspace.js';
import { confined, readRegular } from '../src/util.js';
test('import preserves original bytes and requires per-workspace model consent', async t => {
  const {
    root,
    file,
    workspace
  } = await fixture(t, {
    authorized: false
  });
  assert.equal(workspace.authorized, false);
  assert.equal((await readFile(file)).toString(), 'flag{synthetic_fixture}');
  await assert.rejects(preview(workspace.root, {
    requireAuthorization: true
  }), /authorize/);
  await assert.rejects(snapshot(workspace.root, join(root, 'run-challenge')), /authorize/);
});
test('hash mismatch blocks a run', async t => {
  const {
    workspace
  } = await fixture(t);
  const file = join(workspace.input, 'attachment.txt');
  await chmod(file, 0o600);
  await writeFile(file, 'modified');
  await assert.rejects(preview(workspace.root), /integrity/);
});
test('undeclared files and symlinks are rejected', async t => {
  const {
    workspace
  } = await fixture(t);
  await chmod(workspace.input, 0o700);
  await symlink('/etc/passwd', join(workspace.input, 'escape'));
  await assert.rejects(preview(workspace.root), /symlinks/);
});
test('bounded regular-file reader rejects FIFO without blocking', async t => {
  const {
    root
  } = await fixture(t);
  await assert.rejects(readRegular(root), /regular file/);
});
test('confined paths reject traversal and symlink ancestors', async t => {
  const {
    root
  } = await fixture(t);
  await symlink('/tmp', join(root, 'link'));
  for (const path of ['../etc/passwd', '/etc/passwd', 'link/x', 'a\\b', 'x\0']) await assert.rejects(confined(root, path));
});
test('snapshot copies input and validates immutable task hash', async t => {
  const {
    root,
    workspace
  } = await fixture(t);
  const copy = await snapshot(workspace.root, join(root, 'snapshot'));
  assert.equal(copy.manifest.files[0].sha256, workspace.manifest.files[0].sha256);
  await chmod(join(workspace.root, 'TASK.md'), 0o600);
  await writeFile(join(workspace.root, 'TASK.md'), 'tampered');
  await assert.rejects(preview(workspace.root), /Task hash/);
  assert.equal((await preview(copy.root)).authorized, true);
});
test('import rejects empty files, missing authorization basis and multiple-file folders', async t => {
  const {
    root
  } = await fixture(t);
  const empty = join(root, 'empty');
  await writeFile(empty, '');
  await assert.rejects(importFile(empty, join(root, 'i')), /empty/);
  await assert.rejects(importFile(join(root, 'attachment.txt'), join(root, 'i'), {
    authorized: true
  }), /basis/);
  const folder = join(root, 'folder');
  await mkdir(folder);
  await writeFile(join(folder, 'a'), 'a');
  await writeFile(join(folder, 'b'), 'b');
  await assert.rejects(importFile(folder, join(root, 'i')), /exactly one/);
});
