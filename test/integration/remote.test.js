import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer as tcpServer } from 'node:net';
import { createServer as httpServer } from 'node:http';
import { spawn } from 'node:child_process';
import { randomUUID, createHash } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tools } from '../helpers.js';
import { RemoteRuntime } from '../../src/runtime/remote.js';
import { DockerRuntime, resolveImage } from '../../src/runtime/docker.js';
import { Sessions } from '../../src/runtime/sessions.js';
import { installRemote } from '../../src/tools/remote.js';
import { audit, readEvents } from '../../src/evidence/store.js';

const listen = server => new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', () => resolve(server.address().port)); });
const stop = server => server ? new Promise(resolve => { server.closeAllConnections?.(); server.close(resolve); }) : undefined;
const image = () => resolveImage(process.env.CTFBOT_DOCKER_TEST_IMAGE ?? 'ctfbot-tools:candidate');

test('remote streams preserve binary menu interaction and HTTP cookies/redirects become audited assets', async t => {
  let remote, tcp, web;
  t.after(async () => { await remote?.close(); await stop(tcp); await stop(web); });
  const { state, evidence, registry } = await tools(t);
  tcp = tcpServer(socket => { socket.write(Buffer.from([0, 255, 62])); socket.on('data', bytes => socket.write(bytes)); });
  const tcpPort = await listen(tcp);
  web = httpServer((req, res) => {
    if (req.url === '/login') { res.setHeader('Set-Cookie', 'sid=fixture; Path=/; HttpOnly'); res.end('logged in'); }
    else if (req.url === '/redirect') { res.writeHead(302, { Location: 'http://127.0.0.1:1/blocked' }); res.end(); }
    else { let body = ''; req.on('data', c => { body += c; }); req.on('end', () => res.end(JSON.stringify({ cookie: req.headers.cookie, body, method: req.method }))); }
  });
  const webPort = await listen(web);
  remote = new RemoteRuntime({ state, evidence, image: 'sha256:' + 'a'.repeat(64), runId: 'fixture' });
  await remote.prepare(); remote.bind({ assets: registry.assets }); installRemote(registry, remote);
  const target = await remote.grant({ host: '127.0.0.1', port: tcpPort, capabilities: ['tcp'] }, { authorized: true });
  const connection = await remote.streams.open(target.id);
  const menu = await remote.streams.read(connection.session_id, { until_base64: 'Pg==', wait_ms: 1000 });
  assert.deepEqual(Buffer.from(menu.data_base64, 'base64'), Buffer.from([0, 255, 62]));
  await remote.streams.send(connection.session_id, Buffer.from([255, 0, 1, 2]));
  const echo = await remote.streams.read(connection.session_id, { max_bytes: 2, wait_ms: 1000 });
  assert.deepEqual(Buffer.from(echo.data_base64, 'base64'), Buffer.from([255, 0]));
  const remainder = await remote.streams.read(connection.session_id, { wait_ms: 1000 }); assert.equal(remainder.offset, 5);
  assert.deepEqual(Buffer.from(remainder.data_base64, 'base64'), Buffer.from([1, 2]));
  await remote.revoke(target.id); await assert.rejects(remote.streams.send(connection.session_id, Buffer.from('x')), /revoked/);
  await remote.grant({ host: '127.0.0.1', port: webPort, capabilities: ['http'] }, { authorized: true });
  const { session_id } = remote.http.open();
  const login = await remote.http.request({ url: `http://127.0.0.1:${webPort}/login`, session_id });
  assert.equal(login.headers.find(([key]) => /^set-cookie$/i.test(key))?.[1], '[redacted]', JSON.stringify(login));
  const result = await remote.http.request({ url: `http://127.0.0.1:${webPort}/echo`, session_id, method: 'POST', body: Buffer.from('form=value') });
  assert.match(result.preview, /sid=fixture/); assert.match(result.preview, /form=value/);
  assert.equal(state.sections.assets.get(result.body_asset_id).origin, 'remote');
  const redirect = await remote.http.request({ url: `http://127.0.0.1:${webPort}/redirect`, follow_redirects: true });
  assert.match(redirect.scope_blocked, /127.0.0.1:1/);
  await remote.close(); assert.equal((await audit(evidence.root)).ok, true);
});

test('real Docker scripts and existing Chromium use authorized UDS relays without public container networking', async t => {
  let remote, runtime, sessions, web, tcp;
  t.after(async () => { await remote?.close(); await sessions?.closeAll(); await runtime?.close(); await stop(web); await stop(tcp); });
  const { state, evidence, registry, workspace } = await tools(t);
  web = httpServer((req, res) => { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><title>CTF fixture</title><input id="x"><button id="go" onclick="document.querySelector(\'#result\').textContent=document.querySelector(\'#x\').value">Submit</button><p id="result">ready</p>'); });
  const webPort = await listen(web);
  tcp = tcpServer(socket => socket.on('data', bytes => socket.write(bytes))); const tcpPort = await listen(tcp);
  const selected = await image();
  remote = new RemoteRuntime({ state, evidence, image: selected, runId: randomUUID() }); const networkSocket = await remote.prepare();
  runtime = new DockerRuntime({ input: workspace.input, image: selected, runId: remote.runId, networkSocket }); await runtime.start(); sessions = new Sessions(runtime, evidence);
  remote.bind({ runtime, sessions, assets: registry.assets });
  const target = await remote.grant({ host: '127.0.0.1', port: tcpPort, capabilities: ['tcp'] }, { authorized: true });
  const relay = await remote.relay(target.id);
  const script = await runtime.execute(['python3', '-I', '-c', 'import socket,sys; s=socket.create_connection(("127.0.0.1",int(sys.argv[1]))); s.sendall(b"\\x00\\xffpayload"); print(s.recv(100).hex()); s.close()', String(relay.port)]);
  assert.equal(script.code, 0); assert.match(script.stdout.toString(), /00ff7061796c6f6164/);
  await remote.grant({ host: '127.0.0.1', port: webPort, capabilities: ['http', 'browser'] }, { authorized: true });
  const proxy = await remote.relay(undefined);
  const curl = await runtime.execute(['curl', '--noproxy', '', '--proxy', proxy.proxy, '--max-time', '10', `http://127.0.0.1:${webPort}/`]);
  assert.equal(curl.code, 0); assert.match(curl.stdout.toString(), /CTF fixture/);
  installRemote(registry, remote, { supportsImages: true });
  const call = async (name, args) => JSON.parse((await registry.invoke(name, args)).content[0].text);
  const browser = await call('browser_open', {});
  const page = await call('browser_action', { browser_id: browser.browser_id, action: 'navigate', url: `http://127.0.0.1:${webPort}/`, wait_ms: 5000 });
  assert.equal(page.title, 'CTF fixture', JSON.stringify(page)); assert.ok(page.dom_asset_id);
  await call('browser_action', { browser_id: browser.browser_id, action: 'fill', selector: '#x', value: 'flag{browser_fixture}' });
  await call('browser_action', { browser_id: browser.browser_id, action: 'click', selector: '#go' });
  const inspected = await call('browser_action', { browser_id: browser.browser_id, action: 'inspect' }); assert.match(inspected.text, /flag\{browser_fixture\}/);
  const shot = await registry.invoke('browser_action', { browser_id: browser.browser_id, action: 'screenshot' }); assert.equal(shot.content[1].type, 'image');
  await assert.rejects(remote.browser({ browser_id: browser.browser_id, action: 'navigate', url: 'http://127.0.0.1:1/' }), /scope_blocked/);
  assert.ok(remote.authority.denials.length);
  await call('browser_action', { browser_id: browser.browser_id, action: 'close' });
  await remote.close(); assert.equal((await audit(evidence.root)).ok, true);
});

const SSH_SERVER = `import paramiko,socket,threading,time,hashlib,base64
key=paramiko.RSAKey.generate(2048)
print('SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('='),flush=True)
class Server(paramiko.ServerInterface):
 def check_auth_password(self,u,p): return paramiko.AUTH_SUCCESSFUL if u=='fixture' and hashlib.sha256(p.encode()).hexdigest()=='${createHash('sha256').update('fixture-private-password').digest('hex')}' else paramiko.AUTH_FAILED
 def get_allowed_auths(self,u): return 'password'
 def check_channel_request(self,kind,id): return paramiko.OPEN_SUCCEEDED if kind=='session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
 def check_channel_exec_request(self,channel,command):
  def reply():
   time.sleep(.1)
   if command==b'echo':
    try:
     while True:
      data=channel.recv(65536)
      if not data: break
      channel.sendall(data)
    finally: channel.close()
   else: channel.sendall(b'\\x00fixture\\xff'); channel.send_exit_status(0); channel.close()
  threading.Thread(target=reply,daemon=True).start(); return True
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('127.0.0.1',22222)); s.listen(16)
def serve(c):
 t=paramiko.Transport(c); t.add_server_key(key)
 try:
  t.start_server(server=Server())
  channels=[]
  while t.is_active():
   channel=t.accept(1)
   if channel: channels.append(channel)
   channels=[item for item in channels if not item.closed]
 finally: t.close()
while True:
 c,_=s.accept(); threading.Thread(target=serve,args=(c,),daemon=True).start()
`;
const SSH_FORWARD = `import socket,sys,select,os
s=socket.create_connection(('127.0.0.1',22222))
while True:
 ready,_,_=select.select([s,sys.stdin.buffer],[],[],60)
 for stream in ready:
  data=s.recv(65536) if stream is s else os.read(0,65536)
  if not data: sys.exit(0)
  if stream is s: sys.stdout.buffer.write(data); sys.stdout.buffer.flush()
  else: s.sendall(data)
`;

test('isolated SSH worker authenticates without credential logging and lends a binary channel to a solver script', async t => {
  let remote, runtime, sessions, server; const children = new Set(), sockets = new Set();
  t.after(async () => { await remote?.close(); for (const s of sockets) s.destroy(); for (const c of children) c.kill(); await stop(server); await sessions?.closeAll(); await runtime?.close(); });
  const { state, evidence, registry, workspace, root } = await tools(t); const selected = await image(), runId = randomUUID();
  remote = new RemoteRuntime({ state, evidence, image: selected, runId, credentialsPath: join(root, 'secrets.json') }); const networkSocket = await remote.prepare();
  runtime = new DockerRuntime({ input: workspace.input, image: selected, runId, networkSocket }); await runtime.start(); sessions = new Sessions(runtime, evidence); remote.bind({ runtime, sessions, assets: registry.assets });
  const fixture = await sessions.start(['python3', '-I', '-u', '-c', SSH_SERVER]);
  let output = ''; for (let i = 0; i < 10 && !output.includes('\n'); i++) output += (await sessions.read(fixture.session_id, { collectSeconds: 1 })).output;
  const hostKey = output.split('\n')[0]; assert.match(hostKey, /^SHA256:/);
  server = tcpServer(socket => {
    sockets.add(socket); socket.on('error', () => {});
    const child = spawn('docker', runtime.execArgs(['python3', '-I', '-u', '-c', SSH_FORWARD], randomUUID(), 60000, 4 * 1024 * 1024), { stdio: ['pipe', 'pipe', 'pipe'] });
    children.add(child); child.stderr.resume(); child.stdin.on('error', () => {}); socket.pipe(child.stdin); child.stdout.pipe(socket);
    socket.on('close', () => { sockets.delete(socket); child.kill(); }); child.on('close', () => { children.delete(child); socket.destroy(); });
  });
  const port = await listen(server);
  await writeFile(join(root, 'secrets.json'), JSON.stringify({ fixture: { username: 'fixture', password: 'fixture-private-password' } }), { mode: 0o600 });
  const target = await remote.grant({ host: '127.0.0.1', port, capabilities: ['ssh'], ssh_host_key: hostKey }, { authorized: true });
  installRemote(registry, remote);
  const call = async (name, args) => { const result = await registry.invoke(name, args); assert.ok(!result.isError, result.content[0].text); return JSON.parse(result.content[0].text); };
  const connection = await call('ssh_open', { target_id: target.id, credential_ref: 'fixture' });
  const channel = await call('ssh_exec', { connection_id: connection.connection_id, command: 'fixture' });
  const read = await call('ssh_read', { channel_id: channel.channel_id, wait_ms: 1000 }); assert.equal(read.data_base64, Buffer.from([0, ...Buffer.from('fixture'), 255]).toString('base64'));
  const echo = await call('ssh_exec', { connection_id: connection.connection_id, command: 'echo' });
  const relay = await call('ssh_relay', { channel_id: echo.channel_id });
  const executed = await runtime.execute(['python3', '-I', '-c', 'import socket,sys; s=socket.create_connection(("127.0.0.1",int(sys.argv[1]))); s.sendall(b"\\x00\\xffssh"); print(s.recv(100).hex()); s.close()', String(relay.port)]);
  assert.equal(executed.code, 0); assert.match(executed.stdout.toString(), /00ff737368/);
  await assert.rejects(remote.ssh('ssh_read', { channel_id: echo.channel_id }), /belongs/);
  for (const event of await readEvents(evidence.root)) if (event.type === 'tool_started') assert.ok(!JSON.stringify(event).includes('fixture-private-password'));
  const artifacts = await import('node:fs/promises').then(fs => fs.readdir(join(evidence.root, 'artifacts')));
  for (const name of artifacts) assert.ok(!(await evidence.read('artifacts/' + name)).includes(Buffer.from('fixture-private-password')), name);
  await remote.close(); await sessions.close(fixture.session_id); assert.equal((await audit(evidence.root)).ok, true);
});
