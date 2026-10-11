import { spawn } from 'node:child_process';

/** Pi's browser helper is private; mirror only its shell-free OS launcher. */
export async function openBrowser(target, {
  platform = process.platform,
  launch = spawn
} = {}) {
  const url = new URL(target);
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) {
    return Promise.reject(new Error('Expected an HTTP(S) login URL'));
  }
  const [command, args] = platform === 'darwin' ? ['open', [target]] : platform === 'win32' ? ['rundll32', ['url.dll,FileProtocolHandler', target]] : ['xdg-open', [target]];
  return new Promise((resolve, reject) => {
    const child = launch(command, args, {
      stdio: 'ignore',
      detached: true,
      shell: false
    });
    child.once('error', reject);
    child.once('exit', code => code === 0 ? resolve() : reject(new Error('Browser launcher failed')));
    child.unref();
  });
}
