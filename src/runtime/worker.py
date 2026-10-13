"""Fixed RPC worker. Authentication data and browser profiles never enter solver /work."""
import sys,json,socket,threading,time,base64,hashlib,io,os,signal,subprocess,tempfile,urllib.request,struct,secrets,select,shutil
import importlib.util
spec=importlib.util.spec_from_file_location('relay','/work/relay.py'); relay=importlib.util.module_from_spec(spec); spec.loader.exec_module(relay)
connections={}; channels={}; browsers={}
def uid(): return secrets.token_hex(16)
def b64(b): return base64.b64encode(b).decode()
def raw(b): return base64.b64decode(b,validate=True)
def fingerprint(key): return 'SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
def stop_browser(item):
 process=item['process']
 try: os.killpg(process.pid,signal.SIGKILL)
 except ProcessLookupError: pass
 try: process.wait(timeout=3)
 except subprocess.TimeoutExpired: process.kill(); process.wait()
 item['cdp'].sock.close(); item['proxy'].close()
 for _ in range(10):
  try: item['profile'].cleanup(); return
  except OSError: time.sleep(.05)
 item['profile'].cleanup()

def ssh_open(a):
 import paramiko
 sock=relay.upstream({'target_id':a['target_id'],'capability':'ssh'})
 transport=paramiko.Transport(sock)
 try:
  transport.start_client(timeout=15); key=fingerprint(transport.get_remote_server_key())
  if key!=a.get('host_key'): return {'needs_host_key':key}
  secret=a['credential']; username=secret['username']
  if 'private_key' in secret:
   errors=[]; pkey=None
   for name in ('Ed25519Key','RSAKey','ECDSAKey'):
    try: pkey=getattr(paramiko,name).from_private_key(io.StringIO(secret['private_key']),password=secret.get('passphrase')); break
    except Exception: pass
   if pkey is None: raise RuntimeError('Private key format is unsupported')
   transport.auth_publickey(username,pkey)
  else: transport.auth_password(username,secret['password'])
  assert transport.is_authenticated(), 'SSH authentication failed'
  id=uid(); connections[id]=transport; transport=None
  return {'connection_id':id,'generation':uid(),'host_key':key,'state':'authenticated'}
 finally:
  if transport: transport.close()

def ssh_channel(a):
 transport=connections[a['connection_id']]; channel=transport.open_session(timeout=15)
 if a.get('pty'): channel.get_pty(term='xterm',width=100,height=30)
 if a.get('command') is not None: channel.exec_command(a['command'])
 else: channel.invoke_shell()
 id=uid(); channels[id]=channel
 return {'channel_id':id,'state':'open','pty':bool(a.get('pty'))}
def ssh_read(a):
 channel=channels[a['channel_id']]; end=time.monotonic()+a.get('wait_ms',1000)/1000; limit=a.get('max_bytes',8192)
 while not channel.recv_ready() and not channel.recv_stderr_ready() and not channel.closed and not channel.exit_status_ready() and time.monotonic()<end: time.sleep(.02)
 out=channel.recv(limit) if channel.recv_ready() else b''
 err=channel.recv_stderr(limit) if channel.recv_stderr_ready() else b''
 return {'data_base64':b64(out),'stderr_base64':b64(err),'eof':channel.closed or channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready(),'timed_out':not(out or err) and not channel.closed and not channel.exit_status_ready(),'exit_code':channel.recv_exit_status() if channel.exit_status_ready() else None}
def ssh_transfer(a):
 import paramiko
 sftp=paramiko.SFTPClient.from_transport(connections[a['connection_id']])
 try:
  if a['direction']=='download':
   with sftp.open(a['path'],'rb') as f: data=f.read(16777217)
   assert len(data)<=16777216,'File exceeds 16 MiB'
   return {'data_base64':b64(data)}
  data=raw(a['data_base64']); assert len(data)<=16777216
  with sftp.open(a['path'],'wb') as f: f.write(data)
  return {'uploaded_bytes':len(data)}
 finally: sftp.close()

class WebSocket:
 def __init__(self,url):
  from urllib.parse import urlsplit
  u=urlsplit(url); self.sock=socket.create_connection(('127.0.0.1',u.port),timeout=15); key=b64(os.urandom(16))
  self.sock.sendall(('GET '+u.path+' HTTP/1.1\r\nHost: 127.0.0.1:'+str(u.port)+'\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: '+key+'\r\n\r\n').encode())
  header=b''
  while not header.endswith(b'\r\n\r\n'): header+=self.exact(1)
  assert b' 101 ' in header,'CDP websocket failed'
  self.sequence=0; self.events=[]
 def exact(self,n):
  out=b''
  while len(out)<n:
   p=self.sock.recv(n-len(out))
   if not p: raise RuntimeError('Browser closed')
   out+=p
  return out
 def frame(self,data,opcode=1):
  mask=os.urandom(4); n=len(data); header=bytes([128|opcode,128|(n if n<126 else 126 if n<65536 else 127)])
  if n>=126: header+=struct.pack('!H' if n<65536 else '!Q',n)
  self.sock.sendall(header+mask+bytes(c^mask[i%4] for i,c in enumerate(data)))
 def receive(self):
  fragments=[]; total=0; message_opcode=None
  while True:
   h=self.exact(2); final=bool(h[0]&128); opcode=h[0]&15; n=h[1]&127
   if n==126: n=struct.unpack('!H',self.exact(2))[0]
   if n==127: n=struct.unpack('!Q',self.exact(8))[0]
   assert n<=24*1024*1024,'CDP frame limit'
   mask=self.exact(4) if h[1]&128 else None; data=self.exact(n)
   if mask: data=bytes(c^mask[i%4] for i,c in enumerate(data))
   if opcode==9: self.frame(data,10); continue
   if opcode==8: raise RuntimeError('Browser closed')
   if opcode==10: continue
   if opcode in (1,2):
    assert message_opcode is None,'Unexpected new CDP message frame'
    message_opcode=opcode; fragments=[data]
   elif opcode==0:
    assert message_opcode is not None,'Unexpected CDP continuation frame'
    fragments.append(data)
   else: raise RuntimeError('Unsupported CDP frame')
   total+=len(data); assert total<=24*1024*1024,'CDP message limit'
   if final: return json.loads(b''.join(fragments))
 def call(self,method,params=None):
  self.sequence+=1; sequence=self.sequence; self.frame(json.dumps({'id':sequence,'method':method,'params':params or {}}).encode())
  while True:
   message=self.receive()
   if message.get('id')==sequence:
    if 'error' in message: raise RuntimeError('CDP command failed: '+str(message['error'].get('message','error')))
    return message.get('result',{})
   if 'method' in message:
    # Never retain request/response cookie or authorization headers in the event feed.
    params=message.get('params',{}); record={'method':message['method']}
    if 'request' in params: record['url']=params['request'].get('url'); record['request_method']=params['request'].get('method')
    if 'response' in params: record['status']=params['response'].get('status'); record['url']=params['response'].get('url')
    if 'errorText' in params: record['error']=params['errorText']
    if message['method']=='Runtime.consoleAPICalled': record['text']=' '.join(str(x.get('value',x.get('description',''))) for x in params.get('args',[]))[:2048]
    self.events.append(record); self.events=self.events[-128:]

def browser_open(a):
 binary=shutil.which('chromium') or shutil.which('chromium-browser'); assert binary,'Chromium capability unavailable'
 proxy=relay.listen(0)
 def accept():
  while True:
   try: c,_=proxy.accept(); threading.Thread(target=relay.serve,args=(c,None,'browser'),daemon=True).start()
   except OSError: break
 threading.Thread(target=accept,daemon=True).start()
 profile=tempfile.TemporaryDirectory(prefix='ctf-browser-')
 process=subprocess.Popen([binary,'--headless','--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--disable-background-networking','--disable-quic','--disable-extensions','--disable-sync','--no-first-run','--remote-debugging-port=0','--remote-allow-origins=*','--proxy-server=http://127.0.0.1:'+str(proxy.getsockname()[1]),'--proxy-bypass-list=<-loopback>','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1','--user-data-dir='+profile.name,'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
 try:
  path=os.path.join(profile.name,'DevToolsActivePort'); deadline=time.monotonic()+15
  while not os.path.exists(path):
   assert process.poll() is None and time.monotonic()<deadline,'Chromium startup failed'; time.sleep(.05)
  port=int(open(path).readline()); opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  pages=json.loads(opener.open('http://127.0.0.1:'+str(port)+'/json/list',timeout=5).read()); page=next(p for p in pages if p['type']=='page')
  cdp=WebSocket(page['webSocketDebuggerUrl']); cdp.call('Page.enable'); cdp.call('Runtime.enable'); cdp.call('Network.enable')
  cdp.call('Browser.setDownloadBehavior',{'behavior':'deny'})
  id=uid(); browsers[id]={'process':process,'profile':profile,'proxy':proxy,'cdp':cdp}
  return {'browser_id':id,'state':'ready','downloads':'Use HTTP/SSH download tools to capture explicit assets'}
 except Exception:
  try: os.killpg(process.pid,signal.SIGKILL)
  except ProcessLookupError: pass
  process.wait(); proxy.close(); profile.cleanup(); raise

def browser_action(a):
 item=browsers[a['browser_id']]; cdp=item['cdp']; action=a['action']
 if action=='navigate':
  result=cdp.call('Page.navigate',{'url':a['url']})
  if result.get('errorText'): return {'error':result['errorText']}
  deadline=time.monotonic()+min(a.get('wait_ms',2000),10000)/1000
  while time.monotonic()<deadline:
   result=cdp.call('Runtime.evaluate',{'expression':'document.readyState','returnByValue':True})
   if result.get('result',{}).get('value')=='complete': break
   time.sleep(.05)
 elif action in ('click','fill'):
  selector=json.dumps(a['selector']); value=json.dumps(a.get('value',''))
  expression='(()=>{const e=document.querySelector('+selector+');if(!e)throw Error("selector not found");'
  expression+='e.click();return true;})()' if action=='click' else 'e.value='+value+';e.dispatchEvent(new Event("input",{bubbles:true}));e.dispatchEvent(new Event("change",{bubbles:true}));return true;})()'
  result=cdp.call('Runtime.evaluate',{'expression':expression,'returnByValue':True})
  if result.get('exceptionDetails'): return {'error':'Page action failed'}
 elif action=='evaluate':
  result=cdp.call('Runtime.evaluate',{'expression':a['expression'],'returnByValue':True,'awaitPromise':True,'timeout':10000})
  return {'result':result,'events':item['cdp'].events[-32:]}
 elif action=='screenshot': return {'data_base64':cdp.call('Page.captureScreenshot',{'format':'png'})['data'],'mime_type':'image/png'}
 elif action=='cookies':
  if a.get('cookies') is not None: cdp.call('Network.setCookies',{'cookies':a['cookies']}); return {'state':'updated'}
  return {'cookies':cdp.call('Network.getCookies').get('cookies',[])}
 elif action=='events': return {'events':cdp.events[-64:]}
 elif action=='close':
  stop_browser(item); del browsers[a['browser_id']]; return {'state':'closed'}
 result=cdp.call('Runtime.evaluate',{'expression':'JSON.stringify({url:location.href,title:document.title,text:document.body?.innerText?.slice(0,32000),html:document.documentElement?.outerHTML?.slice(0,1000000)})','returnByValue':True})
 if 'exceptionDetails' in result: raise RuntimeError('Page inspection failed')
 return {**json.loads(result['result']['value']),'events':cdp.events[-32:]}

handlers={'ssh_open':ssh_open,'ssh_exec':ssh_channel,'ssh_shell':ssh_channel,'ssh_read':ssh_read,'ssh_transfer':ssh_transfer,'browser_open':browser_open,'browser_action':browser_action}
def dispatch(method,a):
 if method=='ssh_relay':
  channel=channels[a['channel_id']]; name='channel-'+uid()+'.sock'; server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); server.bind('/run/ctf-network/'+name); os.chmod('/run/ctf-network/'+name,0o600); server.listen(1)
  def bridge():
   try:
    client,_=server.accept(); relay.pump(client,channel); client.close()
   finally: server.close(); channel.close()
  threading.Thread(target=bridge,daemon=True).start(); return {'socket_name':name}
 if method=='ssh_send': channels[a['channel_id']].sendall(raw(a['data_base64'])); return {'state':'sent'}
 if method=='ssh_close':
  if a.get('channel_id'): channels.pop(a['channel_id']).close()
  else: connections.pop(a['connection_id']).close()
  return {'state':'closed'}
 return handlers[method](a)
for line in sys.stdin:
 request={}
 try:
  request=json.loads(line); result=dispatch(request['method'],request.get('args',{})); response={'id':request['id'],'result':result}
 except Exception as e:
  # Preserve useful diagnostics while excluding exact credential values and control characters.
  message=str(e)
  credential=request.get('args',{}).get('credential',{}) if isinstance(request.get('args',{}),dict) else {}
  if isinstance(credential,dict):
   for secret in credential.values():
    if isinstance(secret,str) and len(secret)>=3: message=message.replace(secret,'[redacted]')
  message=''.join(ch if ord(ch)>=32 and ord(ch)!=127 else ' ' for ch in message).strip()[:300] or 'operation failed'
  response={'id':request.get('id'),'error':type(e).__name__+': '+message}
 print(json.dumps(response),flush=True)
