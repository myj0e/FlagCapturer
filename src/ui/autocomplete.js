import { access, stat } from 'node:fs/promises';
import { constants } from 'node:fs';
import { delimiter, join } from 'node:path';
import { homedir } from 'node:os';
import { CombinedAutocompleteProvider, fuzzyFilter } from '@earendil-works/pi-tui';
import { loadSdk } from '../pi/sdk.js';
import { clean } from './workbench-view.js';
export function pathArgument(value) {
  let path = value.trim();
  if (path.startsWith('@')) path = path.slice(1);
  if (path.startsWith('"') && path.endsWith('"')) path = path.slice(1, -1);
  if (path === '~') return homedir();
  return path.startsWith('~/') ? join(homedir(), path.slice(2)) : path;
}

/** Discover installed fd only. Pi's automatic tool download is deliberately not invoked. */
export async function findFd() {
  const suffix = process.platform === 'win32' ? '.exe' : '';
  const directories = (process.env.PATH ?? '').split(delimiter).filter(Boolean);
  try {
    directories.unshift(join((await loadSdk()).getAgentDir(), 'bin'));
  } catch {}
  for (const directory of directories) for (const name of ['fd', 'fdfind']) {
    const candidate = join(directory, name + suffix);
    try {
      if (!(await stat(candidate)).isFile()) continue;
      await access(candidate, process.platform === 'win32' ? constants.R_OK : constants.X_OK);
      return candidate;
    } catch {}
  }
  return null;
}
const hints = {
  login: '[provider] [oauth|api_key]',
  model: '[provider/model]',
  thinking: '[level]',
  import: '<file|workspace>',
  network: '<grant.json|off>',
  memory: '<snapshot.json|off>'
};
const safeItem = item => ({
  ...item,
  label: clean(item.label).replaceAll('\n', ' '),
  description: clean(item.description ?? '').replaceAll('\n', ' ')
});
const matching = (items, prefix) => fuzzyFilter(items, prefix, item => `${item.value} ${item.label} ${item.description ?? ''}`).slice(0, 100);

/** Pi owns matching/menus/insertion; this adapter supplies workbench commands and live parameters. */
export function createWorkbenchAutocomplete(app, commands, {
  basePath = process.cwd(),
  fdPath = null
} = {}) {
  const files = new CombinedAutocompleteProvider([], basePath, fdPath);
  const providers = () => app.catalog.providers?.() ?? [];
  const argumentsFor = async (name, prefix, signal) => {
    if (name === 'login') {
      const parts = /^(\S+)\s+(.*)$/.exec(prefix);
      if (parts) {
        const provider = providers().find(item => item.id === parts[1]);
        return matching((provider?.methods ?? []).map(method => ({
          value: `${provider.id} ${method.id}`,
          label: method.id,
          description: method.label
        })), parts[2]);
      }
      return matching(providers().map(item => ({
        value: item.id,
        label: item.id,
        description: `${item.name} · ${item.configured ? '已配置' : '未配置'}`
      })), prefix);
    }
    if (name === 'model') {
      const items = providers().flatMap(provider => app.catalog.models(provider.id).map(model => ({
        value: `${provider.id}/${model.id}`,
        label: `${provider.id}/${model.id}`,
        description: model.name || model.id
      })));
      return matching(items, prefix);
    }
    if (name === 'thinking') return matching((app.model ? app.catalog.thinking(app.model) : []).map(level => ({
      value: level,
      label: level,
      description: level === app.options.thinkingLevel ? '当前选择' : ''
    })), prefix);
    if (['import', 'network', 'memory'].includes(name)) {
      const result = await files.getSuggestions([prefix], 0, prefix.length, {
        signal: signal ?? new AbortController().signal,
        force: true
      });
      const items = result?.items ?? [];
      // Pi replaces the entire argument; retain any portion before its path prefix.
      const leading = result ? prefix.slice(0, prefix.length - result.prefix.length) : '';
      return [...(name !== 'import' && 'off'.startsWith(prefix) ? [{
        value: 'off',
        label: 'off',
        description: '关闭'
      }] : []), ...items.map(item => ({
        ...item,
        value: leading + item.value
      }))];
    }
    return null;
  };
  const semantic = new Set(Object.keys(hints));
  const makeCombined = signal => new CombinedAutocompleteProvider(commands.map(([name, description]) => ({
    name,
    description,
    argumentHint: hints[name],
    ...(semantic.has(name) ? {
      getArgumentCompletions: prefix => argumentsFor(name, prefix, signal)
    } : {})
  })), basePath, fdPath);
  const combined = makeCombined();
  return {
    async getSuggestions(lines, line, col, options) {
      if (options.signal.aborted) return null;
      const before = (lines[line] ?? '').slice(0, col);
      const command = /^\s*\/(\S+)\s/.exec(before);
      // Tab must use semantic parameter completion instead of Pi's forced file mode.
      const requestProvider = command && semantic.has(command[1]) ? makeCombined(options.signal) : combined;
      let result = await requestProvider.getSuggestions(lines, line, col, {
        ...options,
        force: command && semantic.has(command[1]) ? false : options.force
      });
      if (!result && !fdPath) {
        // Native @ fuzzy search needs fd. Reuse Pi's directory completion as an offline fallback.
        const at = /(?:^|\s)(@(?:"[^"]*|[^\s"]*))$/.exec(before)?.[1];
        if (at) {
          const path = at.slice(1);
          const found = await files.getSuggestions([path], 0, path.length, {
            ...options,
            force: true
          });
          if (found) result = {
            prefix: at,
            items: found.items.map(item => ({
              ...item,
              value: '@' + item.value
            }))
          };
        }
      }
      if (!result || options.signal.aborted) return null;
      const items = result.items.filter(item => typeof item.value === 'string' && clean(item.value) === item.value && !/[\r\n]/.test(item.value)).slice(0, 100).map(safeItem);
      return items.length ? {
        ...result,
        items
      } : null;
    },
    applyCompletion: (...args) => combined.applyCompletion(...args),
    shouldTriggerFileCompletion: (...args) => combined.shouldTriggerFileCompletion(...args)
  };
}
