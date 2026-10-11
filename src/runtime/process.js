import { spawn } from 'node:child_process';
import { invariant, integer } from '../util.js';
export function checkArgv(argv) {
  invariant(Array.isArray(argv) && argv.length > 0 && argv.length <= 64 && argv.every(x => typeof x === 'string' && x.length <= 4096 && !x.includes('\0')) && Buffer.byteLength(JSON.stringify(argv)) <= 16384, 'Invalid argv');
}
/** Controller subprocesses use fixed executable + argv, never a host shell. */
export function runProcess(executable, argv, {
  timeout = 30000,
  signal,
  input,
  limit = 1024 * 1024
} = {}) {
  integer(timeout, 1, 30 * 60 * 1000, 'timeout');
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    const child = spawn(executable, argv, {
      stdio: ['pipe', 'pipe', 'pipe'],
      shell: false
    });
    const stdout = [],
      stderr = [];
    let retained = 0,
      timedOut = false,
      truncated = false,
      cancelled = false;
    const stop = () => child.kill('SIGKILL');
    const abort = () => {
      cancelled = true;
      stop();
    };
    const timer = setTimeout(() => {
      timedOut = true;
      stop();
    }, timeout);
    signal?.addEventListener('abort', abort, {
      once: true
    });
    const collect = chunks => chunk => {
      const available = Math.max(0, limit - retained);
      chunks.push(chunk.subarray(0, available));
      retained += Math.min(available, chunk.length);
      if (chunk.length > available) {
        truncated = true;
        stop();
      }
    };
    child.stdout.on('data', collect(stdout));
    child.stderr.on('data', collect(stderr));
    child.stdin.on('error', () => {});
    child.on('error', error => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      reject(error);
    });
    child.on('close', (code, exitSignal) => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      resolve({
        code,
        signal: exitSignal,
        stdout: Buffer.concat(stdout),
        stderr: Buffer.concat(stderr),
        timedOut,
        truncated,
        cancelled
      });
    });
    child.stdin.end(input);
  });
}
