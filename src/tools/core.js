import { readdir } from 'node:fs/promises';
import { s } from './schema.js';
import { confined, readRegular, invariant, sha256, base64 } from '../util.js';
import { installReliability } from './reliability.js';
import { installImages } from './images.js';
import { installAssets, confinedWorkPath } from './assets.js';
import { installWorkflows } from './workflows.js';
const argvSchema = s.array(s.text(4096), 64);
const bytesFormat = s.enum(['utf8', 'hex']);
export async function installCore(registry, {
  workspace,
  runtime,
  sessions,
  supportsImages = false
}) {
  const {
    state,
    evidence
  } = registry;
  const define = registry.define.bind(registry);
  async function inputBytes(path) {
    const file = workspace.manifest.files.find(x => x.path === path);
    invariant(file, 'Input path is outside inventory');
    const bytes = await readRegular(await confined(workspace.input, path));
    invariant(sha256(bytes) === file.sha256, 'Input integrity failure');
    return bytes;
  }
  async function focused(bytes, artifact, offset, length, encoding) {
    invariant(offset <= bytes.length, 'Offset exceeds source');
    const selected = bytes.subarray(offset, offset + length);
    const source = await state.add('sources', {
      artifact,
      offset,
      length: selected.length,
      sha256: sha256(selected),
      encoding
    });
    return {
      data: selected.toString(encoding === 'hex' ? 'hex' : 'utf8'),
      source_ref: source.id,
      artifact,
      coverage: {
        offset,
        length: selected.length,
        source_bytes: bytes.length,
        truncated: offset > 0 || offset + selected.length < bytes.length
      }
    };
  }
  async function command(argv, signal) {
    const result = await runtime.execute(argv, {
      signal
    });
    return {
      exit_code: result.code,
      timed_out: result.timedOut,
      truncated: result.truncated,
      cancelled: result.cancelled,
      stdout: result.stdout.subarray(0, 4096).toString('utf8'),
      stderr: result.stderr.subarray(0, 4096).toString('utf8'),
      ...(result.stdout.length > 4096 ? { stdout_tail: { text: result.stdout.subarray(-1024).toString('utf8'), offset: result.stdout.length - 1024, source_bytes: result.stdout.length } } : {}),
      ...(result.stderr.length > 4096 ? { stderr_tail: { text: result.stderr.subarray(-1024).toString('utf8'), offset: result.stderr.length - 1024, source_bytes: result.stderr.length } } : {}),
      stdout_artifact: await evidence.artifact(result.stdout),
      stderr_artifact: await evidence.artifact(result.stderr),
      coverage: {
        stdout_bytes: result.stdout.length,
        stderr_bytes: result.stderr.length,
        preview_bytes: 4096,
        truncated: result.truncated || result.stdout.length > 4096 || result.stderr.length > 4096
      }
    };
  }
  define('challenge_list', 'List authorized read-only attachment entries.', s.object({
    path: s.text(512)
  }, []), async ({
    path = '.'
  }) => ({
    entries: await readdir(await confined(workspace.input, path)),
    inventory: workspace.manifest.files
  }));
  define('challenge_read_text', 'Read a UTF-8 input of at most 64 KiB with evidence.', s.object({
    path: s.text(512)
  }), async ({
    path
  }) => {
    const bytes = await inputBytes(path);
    invariant(bytes.length <= 65536, 'Use challenge_read_bytes for larger files');
    const text = new TextDecoder('utf-8', {
      fatal: true
    }).decode(bytes);
    return {
      text,
      artifact: await evidence.artifact(bytes),
      coverage: {
        offset: 0,
        length: bytes.length,
        truncated: false
      }
    };
  }, {
    observe: true
  });
  define('challenge_read_bytes', 'Read an exact bounded byte range; offsets are file bytes.', s.object({
    path: s.text(512),
    offset: s.int(0, 32 * 1024 * 1024),
    length: s.int(1, 4096)
  }), async ({
    path,
    offset,
    length
  }) => {
    const bytes = await inputBytes(path);
    return focused(bytes, await evidence.artifact(bytes), offset, length, 'hex');
  }, {
    observe: true
  });
  define('artifact_read', 'Read hash-verified raw evidence by byte range.', s.object({
    artifact: s.text(100),
    offset: s.int(0, 32 * 1024 * 1024),
    length: s.int(1, 4096),
    encoding: bytesFormat
  }), async ({
    artifact,
    offset,
    length,
    encoding
  }) => focused(await evidence.read(artifact), {
    artifact
  }, offset, length, encoding));
  define('artifact_tail', 'Read the last bounded bytes of verified evidence.', s.object({
    artifact: s.text(100),
    length: s.int(1, 4096),
    encoding: bytesFormat
  }), async ({
    artifact,
    length,
    encoding
  }) => {
    const bytes = await evidence.read(artifact);
    return focused(bytes, {
      artifact
    }, Math.max(0, bytes.length - length), length, encoding);
  });
  define('artifact_find', 'Find literal UTF-8 bytes in a bounded scan. No regular expressions.', s.object({
    artifact: s.text(100),
    literal: s.text(256, {
      minLength: 1
    }),
    offset: s.int(0, 32 * 1024 * 1024),
    scan_bytes: s.int(1, 1024 * 1024)
  }), async ({
    artifact,
    literal,
    offset,
    scan_bytes
  }) => {
    const bytes = await evidence.read(artifact),
      part = bytes.subarray(offset, offset + scan_bytes),
      positions = [];
    const needle = Buffer.from(literal);
    let index = part.indexOf(needle),
      count = 0;
    while (index >= 0) {
      count++;
      if (positions.length < 20) positions.push(offset + index);
      index = part.indexOf(needle, index + 1);
    }
    return {
      positions,
      total_matches_in_scan: count,
      coverage: {
        offset,
        length: part.length,
        source_bytes: bytes.length,
        truncated: offset > 0 || offset + part.length < bytes.length
      }
    };
  });
  define('command_run', 'Run argv inside the offline sandbox; no implicit shell. Foreground deadline 120 s.', s.object({
    argv: argvSchema
  }), async ({
    argv
  }, signal) => command(argv, signal), {
    observe: true
  });
  define('session_start', 'Start a background or interactive argv process; pipes, 30-minute deadline, 16 MiB total output cap.', s.object({
    argv: argvSchema
  }), async ({
    argv
  }, signal) => sessions.start(argv, signal), {
    observe: true
  });
  define('session_send', 'Send up to 16 KiB exact base64 bytes to an interactive session.', s.object({
    session_id: s.text(100),
    data_base64: s.text(21848)
  }), async ({
    session_id,
    data_base64
  }) => sessions.send(session_id, base64(data_base64)), {
    observe: true
  });
  define('session_read', 'Collect progress for up to 60 s, drain up to 8192 bytes; full transcript remains evidence.', s.object({
    session_id: s.text(100),
    collect_seconds: s.int(0, 60)
  }, ['session_id']), async ({
    session_id,
    collect_seconds = 0
  }, signal) => sessions.read(session_id, {
    collectSeconds: collect_seconds,
    signal
  }), {
    observe: true
  });
  define('session_close', 'Terminate the container process group and retain output evidence.', s.object({
    session_id: s.text(100)
  }), async ({
    session_id
  }) => sessions.close(session_id), {
    observe: true
  });
  define('script_save', 'Save a reproducible UTF-8 script to a generated sandbox path. Language selects interpreter.', s.object({
    language: s.enum(['python', 'javascript', 'shell']),
    source: s.text(65536, {
      minLength: 1
    })
  }), async ({
    language,
    source
  }, signal) => {
    const id = `script-${state.sections.scripts.size + 1}`,
      extension = {
        python: 'py',
        javascript: 'mjs',
        shell: 'sh'
      }[language];
    const path = `/work/scripts/${id}.${extension}`;
    await runtime.write(path, Buffer.from(source), signal);
    return state.add('scripts', {
      path,
      language,
      source_artifact: await evidence.artifact(source, 'text/plain')
    });
  });
  const scriptArgv = ({
    script_id,
    args = []
  }) => {
    const script = state.select('scripts', [script_id])[0];
    return [{
      python: 'python3',
      javascript: 'node',
      shell: '/bin/sh'
    }[script.language], script.path, ...args];
  };
  const scriptSchema = s.object({
    script_id: s.text(100),
    args: s.array(s.text(4096), 32)
  }, ['script_id']);
  define('script_run', 'Run a saved script with the foreground deadline.', scriptSchema, async (args, signal) => command(scriptArgv(args), signal), {
    observe: true
  });
  define('script_start', 'Start a saved long-running script; read progress through session_read.', scriptSchema, async (args, signal) => sessions.start(scriptArgv(args), signal), {
    observe: true
  });
  const assets = await installAssets(registry, { workspace, runtime, inputBytes });
  define('artifact_export', 'Export a bounded /work file with declared input lineage.', s.object({
    path: s.text(512),
    parents: s.array(s.text(512), 16)
  }), async ({
    path,
    parents
  }, signal) => {
    confinedWorkPath(path);
    invariant(parents.length > 0, 'Input lineage is required');
    for (const parent of parents) await inputBytes(parent);
    const parentIds = parents.map(path => [...state.sections.assets.values()].find(a => a.origin === 'input' && a.path === path).id);
    const asset = await assets.capture(path, parentIds, { operation: 'artifact_export', controller_proven: false }, signal);
    return state.add('checkpoints', { path, parents, artifact: asset.artifact, asset_id: asset.id });
  }, {
    observe: true
  });
  installReliability(registry, {
    sessions,
    inputBytes,
    command,
    assets
  });
  installImages(registry, {
    supportsImages,
    runtime,
    inputBytes
  });
  await installWorkflows(registry, {
    command,
    workspace,
    runtime,
    assets
  });
}
