import { randomUUID } from 'node:crypto';
import { realpath } from 'node:fs/promises';
import { runProcess, checkArgv } from './process.js';
import { invariant, base64, integer } from '../util.js';
export function imageReference(image) {
  invariant(typeof image === 'string' && /^(?:sha256:[a-f0-9]{64}|[a-zA-Z0-9._/:+-]+@sha256:[a-f0-9]{64})$/.test(image), 'Use an immutable sha256 image ID or repository digest');
  return image;
}
export async function resolveImage(reference = 'ctfbot-tools:candidate', runner = runProcess) {
  invariant(typeof reference === 'string' && reference.length <= 512 && !reference.startsWith('-'), 'Invalid image reference');
  const result = await runner('docker', ['image', 'inspect', '--format', '{{.Id}}', reference]);
  invariant(result.code === 0, 'Local tool image is unavailable; build it before solving');
  return imageReference(result.stdout.toString().trim());
}

// The supervisor lives INSIDE the container. Killing docker exec alone does not kill its child.
const SUPERVISOR = `import os,sys,signal,subprocess,time,json,select
key,deadline,limit=sys.argv[1:4]
cmd=json.loads(sys.argv[4]); deadline=time.monotonic()+float(deadline); limit=int(limit)
cancel='/work/.control/'+key+'.cancel'
if os.path.exists(cancel): sys.exit(130)
p=subprocess.Popen(cmd,stdin=sys.stdin.buffer,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
try:
 open('/work/.control/'+key,'w').write(str(p.pid))
 if os.path.exists(cancel): sys.exit(130)
 streams={p.stdout:sys.stdout.buffer,p.stderr:sys.stderr.buffer}; count=0
 while streams:
  left=deadline-time.monotonic()
  if left<=0: os.killpg(p.pid,signal.SIGKILL); sys.exit(124)
  ready,_,_=select.select(list(streams),[],[],min(left,0.2))
  for stream in ready:
   chunk=os.read(stream.fileno(),65536)
   if not chunk: del streams[stream]; continue
   if count+len(chunk)>limit: os.killpg(p.pid,signal.SIGKILL); sys.exit(125)
   count+=len(chunk); streams[stream].write(chunk); streams[stream].flush()
 sys.exit(p.wait())
finally:
 try: os.killpg(p.pid,signal.SIGKILL)
 except ProcessLookupError: pass
 try: os.unlink('/work/.control/'+key)
 except FileNotFoundError: pass
`;
const KILL = `import os,signal,sys
# Persist intent before reading the PID: docker exec may still be starting its supervisor.
open('/work/.control/'+sys.argv[1]+'.cancel','a').close()
try:
 pid=int(open('/work/.control/'+sys.argv[1]).read()); os.killpg(pid,signal.SIGKILL)
except (FileNotFoundError,ProcessLookupError): pass
`;
export class DockerRuntime {
  constructor({
    input,
    image,
    runId,
    runner = runProcess,
    sessionsFactory,
    extension,
    networkSocket,
    networkSocketWritable = false,
    onLost = () => {}
  } = {}) {
    this.input = input;
    this.image = imageReference(image);
    this.runId = runId;
    this.onLost = onLost;
    this.runner = runner;
    this.sessionsFactory = sessionsFactory;
    this.extension = extension;
    this.networkSocket = networkSocket;
    this.networkSocketWritable = networkSocketWritable;
    this.name = `ctfbot-${randomUUID()}`;
    this.started = false;
    this.created = false;
    this.closed = false;
    this.broken = false;
  }
  async start(signal) {
    invariant(!this.created && !this.closed, 'Runtime already used');
    invariant(process.getuid?.() !== 0, 'Run the controller as a non-root user');
    const source = await realpath(this.input);
    invariant(!/[,\r\n]/.test(source), 'Unsupported Docker mount path');
    const uid = process.getuid?.() ?? 1000,
      gid = process.getgid?.() ?? 1000;
    const extra = [];
    if (this.extension) {
      const root = await realpath(this.extension.root);
      invariant(!/[,\r\n]/.test(root), 'Unsupported extension mount path');
      // Controller-only configuration; no model-provided mount arguments.
      extra.push('--mount', `type=bind,source=${root},target=/opt/ctf-extension${this.extension.writable ? '' : ',readonly'}`);
      if (!this.extension.writable) extra.push('--env', 'PYTHONPATH=/opt/ctf-extension/site');
    }
    if (this.networkSocket) {
      const root = await realpath(this.networkSocket);
      invariant(!/[,\r\n]/.test(root), 'Unsupported network socket mount path');
      extra.push('--mount', `type=bind,source=${root},target=/run/ctf-network${this.networkSocketWritable ? '' : ',readonly'}`);
    }
    const result = await this.runner('docker', ['create', '--name', this.name, '--pull=never', '--label', `flagcapturer.run=${this.runId}`, '--network=none', '--read-only', '--cpus=2', '--memory=2g', '--memory-swap=2g', '--pids-limit=256', '--user', `${uid}:${gid}`, '--cap-drop=ALL', '--security-opt=no-new-privileges:true', '--env', 'HOME=/tmp', '--env', 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', '--tmpfs', `/tmp:rw,nosuid,nodev,size=64m,uid=${uid},gid=${gid}`, '--tmpfs', `/work:rw,exec,nosuid,nodev,size=1g,uid=${uid},gid=${gid},mode=0700`, '--mount', `type=bind,source=${source},target=/challenge,readonly`, ...extra, '--workdir=/work', '--entrypoint=/bin/sh', this.image, '-c', 'mkdir -p /work/.control && exec sleep infinity'], {
      signal
    });
    // A timed-out create may have succeeded at the daemon. Always attempt owned-name cleanup.
    this.created = true;
    invariant(result.code === 0, 'Docker create failed');
    const started = await this.runner('docker', ['start', this.name], {
      signal
    });
    invariant(started.code === 0, 'Docker start failed');
    this.started = true;
    const probe = await this.execute(['python3', '-I', '-c', 'print("ready")'], {
      signal
    });
    invariant(probe.code === 0, 'Tool image must provide Python3 for process supervision');
  }
  execArgs(argv, id, timeout, limit) {
    checkArgv(argv);
    invariant(this.started && !this.closed && !this.broken, 'Sandbox unavailable');
    return ['exec', '-i', this.name, 'python3', '-I', '-u', '-c', SUPERVISOR, id, String(timeout / 1000), String(limit), JSON.stringify(argv)];
  }
  async execute(argv, {
    timeout = 120000,
    signal,
    input,
    limit = 1024 * 1024
  } = {}) {
    integer(timeout, 1, 30 * 60 * 1000, 'timeout');
    integer(limit, 1, 32 * 1024 * 1024, 'output limit');
    const id = randomUUID();
    const result = await this.runner('docker', this.execArgs(argv, id, timeout, limit), {
      timeout: timeout + 5000,
      signal,
      input,
      limit
    });
    if (result.timedOut || result.cancelled || result.truncated) {
      const killed = await this.runner('docker', ['exec', this.name, 'python3', '-I', '-c', KILL, id], {
        timeout: 10000
      });
      if (killed.code !== 0 || result.timedOut || result.truncated) {
        this.broken = true;
        const error = new Error('Sandbox supervision lost; run must stop');
        this.onLost(error);
        await this.close();
        throw error;
      }
    }
    if (result.code === 124) result.timedOut = true;
    if (result.code === 125) result.truncated = true;
    return result;
  }
  async killSession(id) {
    const result = await this.runner('docker', ['exec', this.name, 'python3', '-I', '-c', KILL, id], {
      timeout: 10000
    });
    invariant(result.code === 0, 'Could not terminate sandbox process');
  }
  async write(path, data, signal) {
    invariant(/^\/work\/[a-zA-Z0-9._/-]+$/.test(path) && !path.split('/').includes('..') && !path.startsWith('/work/.control'), 'Invalid work path');
    const result = await this.execute(['python3', '-I', '-c', 'import os,sys; p=sys.argv[1]; os.makedirs(os.path.dirname(p),exist_ok=True); f=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600); stream=os.fdopen(f,"wb"); stream.write(sys.stdin.buffer.read()); stream.close()', path], {
      input: data,
      signal
    });
    invariant(result.code === 0, 'Sandbox file write failed');
  }
  async export(path, signal, { maxBytes = 262144 } = {}) {
    invariant(Number.isSafeInteger(maxBytes) && maxBytes > 0 && maxBytes <= 16 * 1024 * 1024, 'Invalid export byte limit');
    invariant(path.startsWith('/work/') && !path.split('/').includes('..') && !path.includes('\0'), 'Only /work files can be exported');
    const result = await this.execute(['python3', '-I', '-c', 'import pathlib,sys,base64; p=pathlib.Path(sys.argv[1]); assert p.resolve().is_relative_to("/work") and p.is_file() and p.stat().st_size<=int(sys.argv[2]); print(base64.b64encode(p.read_bytes()).decode())', path, String(maxBytes)], {
      signal,
      limit: Math.ceil(maxBytes * 4 / 3) + 4096
    });
    invariant(result.code === 0 && !result.truncated, 'Artifact export failed or exceeds byte limit');
    return base64(result.stdout.toString().trim(), Math.ceil(maxBytes * 4 / 3) + 4);
  }
  async close() {
    if (this.closed || !this.created) return;
    const result = await this.runner('docker', ['rm', '-f', this.name], {
      timeout: 15000
    });
    if (result.code !== 0) {
      const inspect = await this.runner('docker', ['inspect', this.name], {
        timeout: 10000
      });
      invariant(inspect.code !== 0 && inspect.stderr.toString().includes('No such'), `Container cleanup unconfirmed: ${this.name}`);
    }
    this.closed = true;
  }
}
