"""Two authenticated connections to an isolated App Server, no hosted model."""
import json, secrets, socket, subprocess, threading, time
import websocket, jsonschema
import fixture
from probe import ROOT, CLI, ALIASES, TRACE, brief, CLIENT_SCHEMA, clean, write_result

fixture.state['mode']='approval'
token=secrets.token_urlsafe(32)
tokenfile=ROOT/'fixture-ws-token'; tokenfile.write_text(token,encoding='utf-8')
with socket.socket() as reserve:
    reserve.bind(('127.0.0.1',0)); port=reserve.getsockname()[1]
url=f'ws://127.0.0.1:{port}'
env=fixture.os.environ.copy(); env['CODEX_HOME']=str(fixture.home)
proc=subprocess.Popen([CLI,'app-server','--listen',url,'--ws-auth','capability-token','--ws-token-file',str(tokenfile),*fixture.extra],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
for stream in [proc.stdout,proc.stderr]:
    threading.Thread(target=lambda s: [None for _ in iter(s.readline,b'')],args=(stream,),daemon=True).start()
class Client:
    def __init__(self,label,credential):
        self.label=label; self.seq=0; self.events=[]; self.responses={}; self.cv=threading.Condition()
        self.ws=websocket.create_connection(url,header=['Authorization: Bearer '+credential],timeout=3,suppress_origin=True)
        self.ws.settimeout(1)
        threading.Thread(target=self.read,daemon=True).start()
    def read(self):
        while self.ws.connected:
            try: msg=json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException: continue
            except Exception: return
            with self.cv:
                if 'id' in msg and 'method' not in msg:self.responses[msg['id']]=msg
                else:self.events.append(msg); TRACE.append({'connection':self.label,**brief(msg)})
                self.cv.notify_all()
    def request(self,method,params):
        self.seq+=1; ident=self.seq; msg={'id':ident,'method':method,'params':params}; jsonschema.validate(msg,CLIENT_SCHEMA)
        self.ws.send(json.dumps(msg)); deadline=time.monotonic()+15
        with self.cv:
            while ident not in self.responses:
                if time.monotonic()>deadline:raise TimeoutError(method)
                self.cv.wait(.1)
            result=self.responses.pop(ident)
        if 'error' in result:raise RuntimeError(clean(result['error']))
        return result.get('result',{})
    def initialize(self):
        self.request('initialize',{'clientInfo':{'name':'vigil_p0_ws_fixture','version':'0.0.1'},'capabilities':{'experimentalApi':True}})
        self.ws.send(json.dumps({'method':'initialized','params':{}}))
    def close(self):self.ws.close()
data={'hostedModelCalls':0,'approvalResponsesSent':0,'transport':'loopback WebSocket with capability-token','connections':2}
owner=observer=None
try:
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        try:owner=Client('ws_owner',token); break
        except (ConnectionRefusedError,OSError):time.sleep(.1)
    if owner is None:raise TimeoutError('App Server websocket startup')
    try:
        wrong=Client('ws_invalid','wrong-fixture-token'); wrong.close(); data['wrongTokenRejected']=False
    except websocket.WebSocketBadStatusException as exc:data['wrongTokenRejected']=exc.status_code in (401,403); data['wrongTokenHttpStatus']=exc.status_code
    owner.initialize(); observer=Client('ws_observer',token); observer.initialize()
    started=owner.request('thread/start',{'model':'gpt-6.1-sol','modelProvider':'p0_fixture','cwd':str(fixture.workspace),'runtimeWorkspaceRoots':[str(fixture.workspace)],'sandbox':'read-only','approvalPolicy':'on-request','approvalsReviewer':'user','ephemeral':False,'config':{'web_search':'disabled','features.apps':False}})
    tid=started['thread']['id']; ALIASES[tid]='ws_fixture_thread'
    # New empty threads do not yet have a resumable rollout in this version.
    fixture.state['mode']='fail'
    warm=owner.request('turn/start',{'threadId':tid,'input':[{'type':'text','text':'Persist only this isolated protocol fixture.'}]})['turn']
    ALIASES[warm['id']]='ws_fixture_warmup'
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if any(e.get('method')=='turn/completed' and e.get('params',{}).get('turn',{}).get('id')==warm['id'] for e in owner.events):break
        time.sleep(.1)
    observer.request('thread/resume',{'threadId':tid,'modelProvider':'p0_fixture','sandbox':'read-only','approvalPolicy':'on-request','approvalsReviewer':'user'})
    data['observerSubscribed']=True
    fixture.state['mode']='approval'
    turn=owner.request('turn/start',{'threadId':tid,'input':[{'type':'text','text':'Local fixture approval routing probe.'}]})['turn']; turnid=turn['id']; ALIASES[turnid]='ws_approval_turn'
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        if any('requestApproval' in e.get('method','') for c in [owner,observer] for e in c.events):break
        time.sleep(.1)
    time.sleep(.5)
    for client in [owner,observer]:
        requests=[e for e in client.events if 'id' in e and 'requestApproval' in e.get('method','')]
        data[client.label]={'approvalRequests':len(requests),'bindingValid':all(e.get('params',{}).get('threadId')==tid and e.get('params',{}).get('turnId')==turnid for e in requests),'turnStartedObserved':any(e.get('method')=='turn/started' for e in client.events),'statuses':[e.get('params',{}).get('status') for e in client.events if e.get('method')=='thread/status/changed']}
    owner.request('turn/interrupt',{'threadId':tid,'turnId':turnid})
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        done=[e for e in owner.events if e.get('method')=='turn/completed' and e.get('params',{}).get('turn',{}).get('id')==turnid]
        if done:data['finalStatus']=done[-1]['params']['turn']['status'];break
        time.sleep(.1)
    data['commandExecuted']=any(e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('type')=='commandExecution' for e in owner.events)
except Exception as exc:data['failure']=clean(exc)
finally:
    for client in [owner,observer]:
        if client:client.close()
    proc.terminate()
    try:proc.wait(timeout=4)
    except subprocess.TimeoutExpired:proc.kill();proc.wait()
    fixture.server.shutdown();fixture.server.server_close()
    tokenfile.unlink(missing_ok=True)
    data['fixtureRequestCount']=fixture.state['requests']
    write_result('ws-fixture',data)
