import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { openBrowser } from '../src/ui/browser.js';
test('browser launcher uses argv without a shell on Linux, macOS and Windows', async () => {
  const target = 'https://synthetic.invalid/login?a=1&code=%24%28noop%29';
  for (const [platform, command, args] of [['linux', 'xdg-open', [target]], ['darwin', 'open', [target]], ['win32', 'rundll32', ['url.dll,FileProtocolHandler', target]]]) {
    let unref = false;
    await openBrowser(target, {
      platform,
      launch: (cmd, argv, options) => {
        assert.equal(cmd, command);
        assert.deepEqual(argv, args);
        assert.equal(options.shell, false);
        const child = new EventEmitter();
        child.unref = () => {
          unref = true;
        };
        queueMicrotask(() => child.emit('exit', 0));
        return child;
      }
    });
    assert.equal(unref, true);
  }
});
test('browser launcher rejects invalid URLs and reports missing or failed launchers', async () => {
  for (const target of ['file:///tmp/fixture', 'javascript:alert(1)', 'https://key:secret@synthetic.invalid/', 'invalid']) {
    await assert.rejects(openBrowser(target, {
      launch: () => assert.fail('must not launch')
    }));
  }
  for (const event of ['error', 'exit']) await assert.rejects(openBrowser('https://synthetic.invalid', {
    launch: () => {
      const child = new EventEmitter();
      child.unref = () => {};
      queueMicrotask(() => child.emit(event, event === 'error' ? new Error('missing launcher') : 1));
      return child;
    }
  }));
});
