import { constants } from 'node:fs';
import { open, lstat, mkdir, realpath, rename, writeFile } from 'node:fs/promises';
import { resolve, relative, isAbsolute, join, dirname } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
export const sha256 = data => createHash('sha256').update(data).digest('hex');
export const json = value => JSON.stringify(value, null, 2);
export function invariant(condition, message) {
  if (!condition) throw new Error(message);
}
export function integer(value, min, max, name = 'value') {
  invariant(Number.isSafeInteger(value) && value >= min && value <= max, `${name} must be ${min}..${max}`);
  return value;
}
export function relativePath(value) {
  invariant(typeof value === 'string' && value.length > 0 && value.length <= 4096 && !/[\0\\\r\n]/.test(value) && !isAbsolute(value) && !value.split('/').some(p => p === '..' || p === ''), 'Expected confined relative POSIX path');
  return value;
}
export async function confined(root, value = '.') {
  relativePath(value);
  const base = await realpath(root);
  let cursor = base;
  for (const part of value.split('/')) {
    cursor = join(cursor, part);
    invariant(!(await lstat(cursor)).isSymbolicLink(), 'Symlinks are not allowed');
  }
  const path = await realpath(cursor);
  invariant(!relative(base, path).startsWith('..') && !isAbsolute(relative(base, path)), 'Path escapes root');
  return path;
}
export async function readRegular(path, maximum = 32 * 1024 * 1024) {
  const fd = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const before = await fd.stat();
    invariant(before.isFile() && before.size <= maximum, 'Expected bounded regular file');
    const data = Buffer.alloc(before.size);
    let offset = 0;
    while (offset < data.length) {
      const {
        bytesRead
      } = await fd.read(data, offset, data.length - offset, offset);
      if (!bytesRead) break;
      offset += bytesRead;
    }
    const after = await fd.stat();
    invariant(offset === data.length && before.size === after.size && before.mtimeMs === after.mtimeMs && before.ctimeMs === after.ctimeMs, 'File changed while reading');
    return data;
  } finally {
    await fd.close();
  }
}
export async function privateDir(path) {
  await mkdir(path, {
    recursive: true,
    mode: 0o700
  });
  const stat = await lstat(path);
  invariant(stat.isDirectory() && !stat.isSymbolicLink() && (stat.mode & 0o077) === 0, `Directory must be private (0700): ${path}`);
  return resolve(path);
}
export async function atomicWrite(path, data) {
  await privateDir(dirname(path));
  const temporary = `${path}.${randomUUID()}.tmp`;
  await writeFile(temporary, data, {
    mode: 0o600,
    flag: 'wx'
  });
  await rename(temporary, path);
}
export function base64(value, maxEncodedLength = 2 * 1024 * 1024) {
  invariant(typeof value === 'string' && value.length <= maxEncodedLength && value.length % 4 === 0 && !/[^A-Za-z0-9+/=]/.test(value), 'Invalid base64');
  const padding = value.indexOf('=');
  invariant(padding < 0 || padding >= value.length - 2 && /^={1,2}$/.test(value.slice(padding)), 'Invalid base64 padding');
  return Buffer.from(value, 'base64');
}
export function safeTerminal(value) {
  return String(value).replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f]/g, '');
}
