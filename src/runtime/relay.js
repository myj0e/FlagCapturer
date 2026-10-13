import { createServer } from 'node:net';
import { chmod, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { invariant } from '../util.js';

export class RelayGateway {
  constructor(authority) { this.authority = authority; this.clients = new Set(); }
  async prepare() {
    // Short path also avoids the Unix socket path length limit for long run roots.
    this.root = await mkdtemp(join(tmpdir(), 'ctf-relay-')); await chmod(this.root, 0o700);
    return this.root;
  }
  async start() {
    if (this.server) return this.root;
    if (!this.root) await this.prepare();
    this.server = createServer({ allowHalfOpen: true }, client => {
      this.clients.add(client); client.on('error', () => {}); client.once('close', () => this.clients.delete(client));
      let header = Buffer.alloc(0), accepted = false, peer;
      const timer = setTimeout(() => client.destroy(), 10000);
      const close = () => { clearTimeout(timer); peer?.destroy(); };
      client.once('close', close);
      const receive = async bytes => {
        if (accepted) return;
        header = Buffer.concat([header, bytes]); const at = header.indexOf(10);
        if (at < 0) { if (header.length > 8192) client.destroy(); return; }
        accepted = true; client.pause(); client.off('data', receive);
        try {
          invariant(at <= 8192, 'Relay handshake exceeds limit');
          const request = JSON.parse(header.subarray(0, at).toString());
          const target = request.target_id ? this.authority.check(request.target_id, request.capability) : this.authority.find(request.host, request.port, request.capability);
          const abort = new AbortController(); client.once('close', () => abort.abort());
          peer = await this.authority.dial(target.id, request.capability, { signal: abort.signal });
          if (client.destroyed) { peer.destroy(); return; }
          clearTimeout(timer); client.write('{"ok":true}\n');
          const rest = header.subarray(at + 1); let sent = rest.length, received = 0;
          if (sent > 32 * 1024 * 1024) { peer.destroy(); client.destroy(); return; }
          if (rest.length) peer.write(rest);
          client.on('data', chunk => { sent += chunk.length; if (sent > 32 * 1024 * 1024) { peer.destroy(); client.destroy(); } });
          peer.on('data', chunk => { received += chunk.length; if (received > 32 * 1024 * 1024) { peer.destroy(); client.destroy(); } });
          peer.once('error', () => client.destroy()); peer.once('close', () => client.destroy());
          client.pipe(peer); peer.pipe(client); client.resume();
        } catch { clearTimeout(timer); client.end('{"ok":false,"error":"scope_blocked_or_connection_failed"}\n'); }
      };
      client.on('data', receive);
    });
    await new Promise((resolve, reject) => { this.server.once('error', reject); this.server.listen(join(this.root, 'broker.sock'), resolve); });
    await chmod(join(this.root, 'broker.sock'), 0o600);
    return this.root;
  }
  async close() {
    for (const client of this.clients) client.destroy();
    if (this.server?.listening) await new Promise(resolve => this.server.close(resolve));
    if (this.root) await rm(this.root, { recursive: true, force: true });
  }
}

// Fixed helper, no host shell or host port. Only the Unix socket leaves the namespace.
export const RELAY_SOURCE = `import socket,threading,json,select,sys
path='/run/ctf-network/broker.sock'
def upstream(spec):
 s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.settimeout(12); s.connect(path)
 s.sendall(json.dumps(spec).encode()+b'\\n'); h=b''
 while not h.endswith(b'\\n'):
  c=s.recv(1)
  if not c: raise RuntimeError('broker closed')
  h+=c
  if len(h)>8192: raise RuntimeError('invalid broker response')
 if not json.loads(h)['ok']: s.close(); raise RuntimeError('scope blocked')
 s.settimeout(None); return s

def pump(a,b):
 sockets=[a,b]; total=0
 while sockets:
  ready,_,_=select.select(sockets,[],[],60)
  if not ready: continue
  for s in ready:
   data=s.recv(65536); dest=b if s is a else a
   if not data:
    sockets.remove(s)
    try: dest.shutdown(socket.SHUT_WR)
    except OSError: pass
   else:
    total+=len(data)
    if total>32*1024*1024: raise RuntimeError('relay byte limit')
    dest.sendall(data)

def serve(c,target,capability):
 peer=None
 try:
  if target:
   peer=upstream({'target_id':target,'capability':capability})
  else:
   c.settimeout(10); h=b''
   while b'\\r\\n\\r\\n' not in h:
    chunk=c.recv(1)
    if not chunk: raise RuntimeError('client closed')
    h+=chunk
    if len(h)>65536: raise RuntimeError('proxy headers too large')
   line=h.split(b'\\r\\n',1)[0].decode('latin1'); method,url,version=line.split(' ',2)
   from urllib.parse import urlsplit
   u=urlsplit('https://'+url if method=='CONNECT' else url)
   host=u.hostname; port=u.port or (443 if method=='CONNECT' else 80)
   peer=upstream({'host':host,'port':port,'capability':capability})
   if method=='CONNECT': c.sendall(b'HTTP/1.1 200 Connection Established\\r\\n\\r\\n')
   else:
    path=(u.path or '/')+('?' + u.query if u.query else '')
    lines=h.split(b'\\r\\n'); lines[0]=(method+' '+path+' '+version).encode('latin1')
    lines=[x for x in lines if not x.lower().startswith(b'proxy-')]
    peer.sendall(b'\\r\\n'.join(lines))
   c.settimeout(None)
  pump(c,peer)
 except Exception:
  try:
   if not target: c.sendall(b'HTTP/1.1 502 scope_blocked_or_connection_failed\\r\\nContent-Length: 0\\r\\n\\r\\n')
  except OSError: pass
 finally:
  c.close()
  if peer: peer.close()

def listen(port,target=None,capability='http'):
 s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('127.0.0.1',port)); s.listen(16)
 return s
if __name__=='__main__':
 port=int(sys.argv[1]); target=sys.argv[2] if len(sys.argv)>2 and sys.argv[2]!='-' else None; cap=sys.argv[3] if len(sys.argv)>3 else 'http'
 s=listen(port,target,cap); print(json.dumps({'port':s.getsockname()[1]}),flush=True)
 while True:
  c,_=s.accept(); threading.Thread(target=serve,args=(c,target,cap),daemon=True).start()
`;
