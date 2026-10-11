import { access } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { resolve, join, dirname } from 'node:path';
export async function loadSdk() {
  if (process.env.FLAGCAPTURER_PI_SDK) return import(pathToFileURL(resolve(process.env.FLAGCAPTURER_PI_SDK)).href);
  // Prefer the project's pinned dependency over reference builds or global installations.
  const installed = new URL('../../node_modules/@earendil-works/pi-coding-agent/dist/index.js', import.meta.url);
  try {
    await access(installed);
    return await import(installed.href);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const local = new URL('../../reference/pi/packages/coding-agent/dist/index.js', import.meta.url);
  try {
    await access(local);
    return await import(local.href);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const global = pathToFileURL(join(dirname(process.execPath), '../lib/node_modules/@earendil-works/pi-coding-agent/dist/index.js'));
  try {
    await access(global);
    return await import(global.href);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  try {
    return await import('@earendil-works/pi-coding-agent');
  } catch (error) {
    if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
    throw new Error('Pi SDK unavailable. Build reference/pi (see README), or install @earendil-works/pi-coding-agent@1.1.0. No model request was sent.', {
      cause: error
    });
  }
}
