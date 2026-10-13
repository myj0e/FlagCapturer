#!/usr/bin/env node
import { parseArgs } from 'node:util';
import { resolve } from 'node:path';
import { importFile, preview } from './challenge/workspace.js';
import { solve } from './application/solve.js';
import { report, diagnose, evaluate, replay } from './application/report.js';
import { recover } from './application/recovery.js';
import { audit, readEvents } from './evidence/store.js';
import { loadSdk } from './pi/session.js';
import { DockerRuntime, resolveImage } from './runtime/docker.js';
import { runProcess } from './runtime/process.js';
import { json, invariant, safeTerminal } from './util.js';
import { attachTerminal, terminalObserver } from './ui/terminal.js';
import { startWorkbench } from './ui/workbench.js';
const HELP = `FlagCapturer 0.2 — Pi-based CTF workbench (Node >=22.19)

ctfbot import <file|folder> [--authorize-model-data --basis <reason>] [--task <text>]
ctfbot preview <workspace>
ctfbot solve <workspace> --provider <provider> --model <id> --confirm-model-usage [--interactive]
ctfbot resume <run-directory> --confirm-model-usage [--provider <provider> --model <id>]
ctfbot evaluate <dataset.json> --provider <provider> --model <id> --confirm-model-usage
ctfbot report|diagnose-run|audit|events|recover <run-directory>
ctfbot replay <run-directory> --confirm-execution [--image <local image>]
ctfbot ui [file|workspace]
ctfbot doctor | --help | --version

Options: --image (default ctfbot-tools:candidate), --runs-root, --imports-root,
         --skill <name>, --skill-path <directory> (repeatable),
         --additional-prompt, --thinking, --show-thinking (CLI reasoning text), --json, --output (evaluation results)
         --network-grant (scoped TCP/service grant), --memory (reviewed snapshot)
No arguments opens the interactive workbench. /login, /model, /thinking configure Pi.
`;
async function doctor() {
  const checks = {
    node: process.version
  };
  try {
    await loadSdk();
    checks.pi = 'available';
  } catch (error) {
    checks.pi = error.message;
  }
  try {
    const result = await runProcess('docker', ['version', '--format', '{{.Server.Version}}'], {
      timeout: 10000
    });
    checks.docker = result.code === 0 ? result.stdout.toString().trim() : 'daemon unavailable';
  } catch (error) {
    checks.docker = error.message;
  }
  try {
    checks.image = await resolveImage();
  } catch (error) {
    checks.image = error.message;
  }
  return checks;
}
async function setup(options = {}) {
  return startWorkbench(options);
}
async function runSolve(options) {
  const controller = new AbortController();
  const interrupt = () => controller.abort();
  process.once('SIGINT', interrupt);
  process.once('SIGTERM', interrupt);
  const ui = options.interactive ? attachTerminal(controller) : {
    ready: () => {},
    close: () => {}
  };
  try {
    const result = await solve({
      ...options,
      signal: controller.signal,
      onReady: ui.ready,
      onEvent: options.json ? event => {
        // Only public events are printed; raw Pi thinking events never leave this adapter.
        if (['run_started', 'run_finished', 'tool_execution_start', 'tool_execution_end'].includes(event.type)) console.log(JSON.stringify(event));
      } : terminalObserver(process.stdout, { showThinking: options.showThinking })
    });
    process.exitCode = result.ok ? 0 : result.status === 'cancelled' ? 130 : 1;
    return result;
  } finally {
    ui.close();
    process.removeListener('SIGINT', interrupt);
    process.removeListener('SIGTERM', interrupt);
  }
}
async function withCancellation(action) {
  const controller = new AbortController();
  const interrupt = () => controller.abort();
  process.once('SIGINT', interrupt);
  process.once('SIGTERM', interrupt);
  try {
    const result = await action(controller.signal);
    if (controller.signal.aborted) process.exitCode = 130;
    return result;
  } catch (error) {
    if (controller.signal.aborted) process.exitCode = 130;
    throw error;
  } finally {
    process.removeListener('SIGINT', interrupt);
    process.removeListener('SIGTERM', interrupt);
  }
}
async function main() {
  const {
    values,
    positionals
  } = parseArgs({
    allowPositionals: true,
    options: {
      help: {
        type: 'boolean'
      },
      version: {
        type: 'boolean'
      },
      'authorize-model-data': {
        type: 'boolean'
      },
      basis: {
        type: 'string'
      },
      task: {
        type: 'string'
      },
      provider: {
        type: 'string'
      },
      model: {
        type: 'string'
      },
      'skill-path': { type: 'string', multiple: true },
      skill: { type: 'string' },
      thinking: {
        type: 'string'
      },
      'confirm-model-usage': {
        type: 'boolean'
      },
      'confirm-execution': {
        type: 'boolean'
      },
      image: {
        type: 'string'
      },
      'runs-root': {
        type: 'string'
      },
      'imports-root': {
        type: 'string'
      },
      'additional-prompt': {
        type: 'string'
      },
      'show-thinking': { type: 'boolean' },
      interactive: {
        type: 'boolean'
      },
      json: {
        type: 'boolean'
      },
      output: {
        type: 'string'
      },
      'network-grant': {
        type: 'string'
      },
      memory: {
        type: 'string'
      }
    }
  });
  if (values.help) {
    console.log(HELP);
    return;
  }
  if (values.version) {
    console.log('0.2.0');
    return;
  }
  const [command, path] = positionals;
  const options = {
    workspace: path,
    provider: values.provider ?? process.env.CTFBOT_PROVIDER,
    model: values.model ?? process.env.CTFBOT_MODEL,
    thinkingLevel: values.thinking,
    confirmModelUsage: values['confirm-model-usage'] === true,
    image: values.image,
    runsRoot: values['runs-root'] ?? 'runs',
    initialSkill: values.skill,
    skillPaths: values['skill-path'] ?? [],
    additionalPrompt: values['additional-prompt'] ?? '',
    interactive: values.interactive,
    showThinking: values['show-thinking'],
    json: values.json,
    output: values.output,
    networkGrant: values['network-grant'],
    memoryPath: values.memory
  };
  let result;
  switch (command) {
    case undefined:
    case 'ui':
      result = await setup(Object.fromEntries(Object.entries({
        workspace: path,
        provider: values.provider ?? process.env.CTFBOT_PROVIDER,
        model: values.model ?? process.env.CTFBOT_MODEL,
        thinkingLevel: values.thinking,
        image: values.image,
        initialSkill: values.skill,
        skillPaths: values['skill-path'] ?? [],
        runsRoot: values['runs-root'],
        importsRoot: values['imports-root'],
        networkGrant: values['network-grant'],
        memoryPath: values.memory
      }).filter(([, value]) => value !== undefined)));
      break;
    case 'doctor':
      result = await doctor();
      break;
    case 'import':
      invariant(path, 'Specify a file');
      result = await importFile(path, values['imports-root'] ?? 'data/imports', {
        authorized: values['authorize-model-data'] === true,
        basis: values.basis ?? '',
        task: values.task
      });
      break;
    case 'preview':
      invariant(path, 'Specify a workspace');
      result = await preview(path);
      break;
    case 'solve':
      invariant(path, 'Specify a workspace');
      result = await runSolve(options);
      break;
    case 'resume':
      invariant(path, 'Specify a saved run directory');
      result = await runSolve({ ...options, resumeFrom: resolve(path), workspace: undefined });
      break;
    case 'report':
      result = await report(resolve(path));
      break;
    case 'diagnose-run':
      result = await diagnose(resolve(path));
      break;
    case 'audit':
      result = await audit(resolve(path));
      if (!result.ok) process.exitCode = 1;
      break;
    case 'events':
      result = await readEvents(resolve(path));
      break;
    case 'recover':
      result = await recover(resolve(path));
      break;
    case 'evaluate':
      result = await withCancellation(signal => evaluate(resolve(path), {
        ...options,
        signal
      }));
      if (!process.exitCode && result.results.some(x => !x.ok)) process.exitCode = 1;
      break;
    case 'replay':
      invariant(values['confirm-execution'], 'Pass --confirm-execution to execute recorded commands in a fresh sandbox');
      result = await withCancellation(signal => replay(resolve(path), {
        image: values.image,
        initialSkill: values.skill,
        skillPaths: values['skill-path'] ?? [],
        runsRoot: options.runsRoot,
        signal,
        runtimeFactory: config => new DockerRuntime(config)
      }));
      if (!result.ok && !process.exitCode) process.exitCode = 1;
      break;
    default:
      throw new Error(`Unknown command: ${command}\n${HELP}`);
  }
  console.log(safeTerminal(values.json ? JSON.stringify(result) : json(result)));
}
main().catch(error => {
  console.error(safeTerminal(error.message));
  process.exitCode = process.exitCode || 1;
});
