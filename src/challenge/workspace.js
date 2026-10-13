import { lstat, readdir, writeFile, chmod } from 'node:fs/promises';
import { join, basename, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { confined, readRegular, privateDir, sha256, json, invariant, relativePath } from '../util.js';
import { parseConnectionDescription } from './connection.js';
export async function importFile(source, importsRoot, {
  authorized = false,
  basis = '',
  task
} = {}) {
  let selected = resolve(source);
  const stat = await lstat(selected);
  invariant(!stat.isSymbolicLink(), 'Attachment must not be a symlink');
  if (stat.isDirectory()) {
    const entries = await readdir(selected);
    invariant(entries.length === 1, 'Select a file or a directory containing exactly one attachment');
    selected = await confined(selected, entries[0]);
  }
  const data = await readRegular(selected);
  invariant(data.length > 0, 'Attachment is empty');
  invariant(!authorized || basis.trim(), 'Model transfer authorization needs a basis');
  const name = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(basename(selected)) ? basename(selected) : 'attachment.bin';
  const digest = sha256(data);
  const root = await privateDir(join(await privateDir(importsRoot), `${digest.slice(0, 24)}-${randomUUID()}`));
  const input = await privateDir(join(root, 'input'));
  const description = task ?? `Solve the offline CTF attachment ${name}. Identify the requested flag(s) and provide a reproducible writeup. No external network access is authorized.`;
  invariant(typeof description === 'string' && description.trim() && Buffer.byteLength(description) <= 64 * 1024, 'Task must contain 1..65536 bytes');
  await writeFile(join(input, name), data, {
    flag: 'wx',
    mode: data.subarray(0, 4).equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46])) ? 0o555 : 0o444
  });
  const manifest = {
    schema_version: 1,
    id: digest.slice(0, 24),
    mode: 'offline',
    admitted: true,
    model_transfer: {
      authorized,
      basis
    },
    task_sha256: sha256(description),
    files: [{
      path: name,
      sha256: digest,
      bytes: data.length
    }],
    source: selected
  };
  await writeFile(join(root, 'TASK.md'), description, {
    flag: 'wx',
    mode: 0o444
  });
  await writeFile(join(root, 'manifest.json'), json(manifest), {
    flag: 'wx',
    mode: 0o444
  });
  await chmod(input, 0o555);
  return preview(root);
}
export async function preview(workspace, {
  requireAuthorization = false
} = {}) {
  const root = resolve(workspace);
  invariant(!(await lstat(root)).isSymbolicLink(), 'Workspace cannot be a symlink');
  let manifest;
  try {
    manifest = JSON.parse((await readRegular(await confined(root, 'manifest.json'), 256 * 1024)).toString());
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    const old = JSON.parse((await readRegular(await confined(root, 'provenance.json'), 256 * 1024)).toString());
    invariant(old.formal_admission === 'admitted' && old.import_mode === 'static_files_only' && !old.remote_target && !old.service && !old.service_endpoint, 'Only admitted static legacy workspaces can migrate');
    manifest = {
      schema_version: 1,
      id: old.challenge_id,
      mode: 'offline',
      admitted: true,
      model_transfer: {
        authorized: old.model_data_authorized === true,
        basis: old.model_data_authorization_basis
      },
      task_sha256: old.task_sha256,
      files: old.input_files,
      legacy: true
    };
  }
  invariant(manifest.schema_version === 1 && ['offline', 'remote'].includes(manifest.mode) && manifest.admitted === true, 'Unsupported workspace admission or mode');
  invariant(Array.isArray(manifest.files) && (manifest.mode === 'remote' || manifest.files.length > 0) && manifest.files.length <= 256, 'Invalid input inventory');
  const task = (await readRegular(await confined(root, 'TASK.md'), 64 * 1024)).toString('utf8');
  invariant(task.trim() && sha256(task) === manifest.task_sha256, 'Task hash mismatch');
  const input = await confined(root, 'input');
  const paths = new Set();
  let total = 0;
  for (const file of manifest.files) {
    relativePath(file.path);
    invariant(!paths.has(file.path), 'Duplicate attachment path');
    paths.add(file.path);
    const bytes = await readRegular(await confined(input, file.path));
    total += bytes.length;
    invariant(total <= 128 * 1024 * 1024, 'Workspace exceeds 128 MiB');
    invariant(bytes.length === file.bytes && sha256(bytes) === file.sha256, `Attachment integrity failure: ${file.path}`);
  }
  // No undeclared files, symlinks, sockets or devices may enter the sandbox mount.
  async function walk(directory, prefix = '') {
    for (const entry of await readdir(directory, {
      withFileTypes: true
    })) {
      const path = prefix + entry.name;
      invariant(!entry.isSymbolicLink(), 'Input symlinks are forbidden');
      if (entry.isDirectory()) await walk(join(directory, entry.name), `${path}/`);else invariant(entry.isFile() && paths.has(path), `Undeclared input: ${path}`);
    }
  }
  await walk(input);
  const authorized = manifest.model_transfer?.authorized === true && typeof manifest.model_transfer.basis === 'string' && manifest.model_transfer.basis.trim().length > 0;
  if (requireAuthorization) invariant(authorized, 'This workspace does not authorize model transfer');
  return {
    root,
    input,
    manifest,
    task,
    authorized
  };
}
export async function snapshot(source, destination) {
  const checked = await preview(source, {
    requireAuthorization: true
  });
  const root = await privateDir(destination);
  const input = await privateDir(join(root, 'input'));
  for (const file of checked.manifest.files) {
    const data = await readRegular(await confined(checked.input, file.path));
    invariant(sha256(data) === file.sha256, 'Attachment changed before run');
    const parts = file.path.split('/');
    parts.pop();
    if (parts.length) await privateDir(join(input, ...parts));
    await writeFile(join(input, file.path), data, {
      flag: 'wx',
      mode: data.subarray(0, 4).equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46])) ? 0o555 : 0o444
    });
  }
  await writeFile(join(root, 'TASK.md'), checked.task, {
    flag: 'wx',
    mode: 0o444
  });
  await writeFile(join(root, 'manifest.json'), json(checked.manifest), {
    flag: 'wx',
    mode: 0o444
  });
  await chmod(input, 0o555);
  return preview(root, {
    requireAuthorization: true
  });
}


/** Remote-only tasks have a real task identity; targets are hints, not grants. */
export async function importRemote(target, importsRoot, { task, authorized = false, basis = '' } = {}) {
  const { targets, connection } = parseConnectionDescription(target);
  invariant(task === undefined || typeof task === 'string' && task.trim(), 'Task must contain text');
  const description = `${task ?? 'Solve the authorized CTF service. Find the requested flag and provide a reproducible writeup.'}\n\nConnection instructions (user-provided data; not network authorization):\n${connection.description}`;
  invariant(description.trim() && Buffer.byteLength(description) <= 65536 && (!authorized || basis.trim()), 'Task and model transfer basis are required');
  const id = randomUUID();
  const root = await privateDir(join(await privateDir(importsRoot), `remote-${id}`));
  const input = await privateDir(join(root, 'input'));
  const manifest = { schema_version: 1, id, mode: 'remote', admitted: true, model_transfer: { authorized, basis }, task_sha256: sha256(description), files: [], targets, connection };
  await writeFile(join(root, 'TASK.md'), description, { mode: 0o444 });
  await writeFile(join(root, 'manifest.json'), json(manifest), { mode: 0o444 });
  await chmod(input, 0o555); return preview(root);
}
