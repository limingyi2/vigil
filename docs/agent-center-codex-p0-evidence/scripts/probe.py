import argparse, collections, hashlib, json, os, queue, re, subprocess, threading, time, shutil
from pathlib import Path
import jsonschema

ROOT = Path(__file__).parent.resolve()
CLI = shutil.which('codex')
if not CLI: raise RuntimeError('Codex CLI is required')
CLIENT_SCHEMA = json.loads((ROOT/'schema/ClientRequest.json').read_text(encoding='utf-8'))
ALIASES = {}
CURRENT = os.environ.get('CODEX_THREAD_ID','')
if CURRENT: ALIASES[CURRENT]='desktop_current'
TRACE=[]
def alias(value):
    if not value: return value
    if value not in ALIASES: ALIASES[value]='id_'+str(len(ALIASES))
    return ALIASES[value]
def clean(value):
    text=str(value)
    for raw,label in [(str(ROOT),'<PROBE>'),('F:\\Vigil','<VIGIL>'),('F:/Vigil','<VIGIL>'),(os.environ.get('USERPROFILE',''),'<HOME>')]:
        if raw: text=text.replace(raw,label)
    text=re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',lambda m:alias(m[0]),text)
    text=re.sub(r'(?:sk-|ghp_)[A-Za-z0-9_-]{16,}','<REDACTED>',text)
    return text[:700]
def brief(msg):
    p=msg.get('params') or {}
    out={'at':round(time.monotonic(),3),'method':msg.get('method')}
    if 'id' in msg: out['requestId']=msg['id']
    for k in ['threadId','turnId','itemId','requestId']:
        if k in p: out[k]=alias(str(p[k]))
    for obj,k in [(p.get('thread'),'thread'),(p.get('turn'),'turn'),(p.get('item'),'item')]:
        if isinstance(obj,dict):
            out[k]={field:alias(str(obj[field])) if field=='id' else obj[field] for field in ['id','type','status','exitCode'] if field in obj}
    if 'id' in msg and 'method' in msg: out['serverRequest']=True
    return out
class RPC:
    def __init__(self,label='independent',home=None,extra=None):
        self.label=label; self.seq=0; self.responses={}; self.cv=threading.Condition(); self.events=[]; self.stderr=[]
        env=os.environ.copy()
        if home: env['CODEX_HOME']=str(home)
        self.process=subprocess.Popen([CLI,'app-server','--listen','stdio://','-c','analytics.enabled=false',*(extra or [])],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',env=env,cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
        threading.Thread(target=self.read,daemon=True).start()
        threading.Thread(target=self.readerr,daemon=True).start()
    def readerr(self):
        for line in self.process.stderr: self.stderr.append(line)
    def read(self):
        for line in self.process.stdout:
            try: msg=json.loads(line)
            except ValueError: continue
            with self.cv:
                if 'id' in msg and 'method' not in msg: self.responses[msg['id']]=msg
                else:
                    self.events.append(msg); TRACE.append({'connection':self.label,**brief(msg)})
                self.cv.notify_all()
    def send(self,msg):
        self.process.stdin.write(json.dumps(msg,ensure_ascii=False)+'\n'); self.process.stdin.flush()
    def request(self,method,params,timeout=20):
        self.seq+=1; ident=self.seq; msg={'id':ident,'method':method,'params':params}
        jsonschema.validate(msg,CLIENT_SCHEMA)
        TRACE.append({'connection':self.label,'direction':'request','method':method,'requestId':ident})
        self.send(msg)
        end=time.monotonic()+timeout
        with self.cv:
            while ident not in self.responses:
                if self.process.poll() is not None: raise RuntimeError('App Server exited '+str(self.process.returncode))
                remain=end-time.monotonic()
                if remain<=0: raise TimeoutError(method)
                self.cv.wait(min(remain,1))
            response=self.responses.pop(ident)
        if 'error' in response:
            TRACE.append({'connection':self.label,'direction':'error','method':method,'error':{'code':response['error'].get('code'),'message':clean(response['error'].get('message'))}})
            raise RuntimeError(clean(response['error']))
        TRACE.append({'connection':self.label,'direction':'response','method':method,'requestId':ident})
        return response.get('result',{})
    def initialize(self):
        result=self.request('initialize',{'clientInfo':{'name':'vigil_agent_center_p0','title':'Vigil Agent Center P0','version':'0.0.1'},'capabilities':{'experimentalApi':True}})
        self.send({'method':'initialized','params':{}})
        return result
    def close(self):
        try: self.process.stdin.close(); self.process.wait(timeout=4)
        except Exception:
            self.process.terminate()
            try:self.process.wait(timeout=3)
            except Exception:self.process.kill()

def write_result(name,data):
    (ROOT/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/(name+'-events.jsonl')).write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in TRACE),encoding='utf-8')
    print(json.dumps(data,ensure_ascii=False,indent=2),flush=True)

def discover():
    client=RPC(); data={}
    try:
        try:client.request('thread/loaded/list',{})
        except Exception as e:data['before_initialize_error']=str(e)
        data['initialize']={k:clean(v) for k,v in client.initialize().items()}
        account=client.request('account/read',{'refreshToken':False})
        data['authentication']={'requiresOpenaiAuth':account.get('requiresOpenaiAuth'),'accountPresent':bool(account.get('account')),'accountType':(account.get('account') or {}).get('type')}
        models=client.request('model/list',{'includeHidden':False,'limit':30})
        data['models']=[{'model':m.get('model'),'default':m.get('isDefault'),'efforts':[e.get('reasoningEffort') for e in m.get('supportedReasoningEfforts',[])]} for m in models.get('data',[])]
        listed=client.request('thread/list',{'limit':50,'cwd':'F:/Vigil','sourceKinds':['appServer','cli','vscode','unknown'],'useStateDbOnly':True})
        data['desktop_list']={'count':len(listed.get('data',[])),'currentThreadFound':any(t.get('id')==CURRENT for t in listed.get('data',[])),'statuses':dict(collections.Counter(str(t.get('status')) for t in listed.get('data',[]))),'sourceKinds':dict(collections.Counter(str(t.get('source')) for t in listed.get('data',[])))}
        if CURRENT:
            thread=client.request('thread/read',{'threadId':CURRENT,'includeTurns':False}).get('thread',{})
            data['desktop_current_read']={'id':alias(thread.get('id')),'status':thread.get('status'),'source':thread.get('source'),'historyMode':thread.get('historyMode'),'turnsCount':len(thread.get('turns',[]))}
            try:
                full=client.request('thread/read',{'threadId':CURRENT,'includeTurns':True}).get('thread',{})
                data['desktop_history']={'turnsCount':len(full.get('turns',[])),'lastStatuses':[t.get('status') for t in full.get('turns',[])[-3:]],'contentsExported':False}
            except Exception as e:data['desktop_history']={'error':str(e)}
        loaded=client.request('thread/loaded/list',{})
        data['loaded']={'count':len(loaded.get('data',[])),'currentThreadLoaded':CURRENT in loaded.get('data',[])}
        start=len(client.events); time.sleep(3)
        data['desktop_observation']={'seconds':3,'notificationMethods':dict(collections.Counter(e.get('method') for e in client.events[start:]))}
        data['stderrLineCount']=len(client.stderr)
    except Exception as e:data['failure']=clean(e)
    finally:client.close();write_result('discover',data)

def managed():
    client=RPC(label='managed'); data={'realTurnStarts':0,'approvalResponsesSent':0,'turnLimit':2,'secondsPerTurn':90,'toolItemsPerTurn':4}
    workspace=ROOT/'managed-workspace'; workspace.mkdir(exist_ok=True)
    (workspace/'probe.txt').write_text('VIGIL_P0_FIXTURE_ONLY\n',encoding='utf-8')
    try:
        client.initialize()
        # Read effective config in memory, retain no credential/config payload.
        cfg=client.request('config/read',{'includeLayers':False}).get('config',{})
        mcp=cfg.get('mcp_servers') or {}; plugins=cfg.get('plugins') or {}
        config={'analytics.enabled':False,'history.persistence':'none','web_search':'disabled','features.apps':False}
        for name in mcp: config['mcp_servers.'+name+'.enabled']=False
        for name in plugins: config['plugins.'+name+'.enabled']=False
        data['isolation']={'workspace':'<PROBE>/managed-workspace','ephemeral':True,'sandbox':'read-only','approvalPolicy':'on-request','approvalsReviewer':'user','mcpServersDisabled':len(mcp),'pluginsDisabled':len(plugins),'configurationFilesWritten':False}
        response=client.request('thread/start',{'model':'gpt-6.1-sol','cwd':str(workspace),'runtimeWorkspaceRoots':[str(workspace)],'sandbox':'read-only','approvalPolicy':'on-request','approvalsReviewer':'user','ephemeral':True,'config':config,'serviceName':'vigil_agent_center_p0','developerInstructions':'This is a bounded protocol probe. Work only in the temporary current workspace. Do not read unrelated paths, run network tools, load skills, or spawn agents. Do not modify any file. Use at most four tool calls; keep the final answer brief.'},timeout=30)
        tid=response['thread']['id']; ALIASES[tid]='managed_thread'
        data['thread']={k:response.get(k) for k in ['model','modelProvider','approvalPolicy','approvalsReviewer','sandbox']}
        data['thread']['id']='managed_thread'; data['thread']['ephemeral']=response['thread'].get('ephemeral')
        data['instructionSourcesCount']=len(response.get('instructionSources',[]))
        scenarios=[('tool_and_completion','Use a local shell tool once to read the file probe.txt in the current temporary directory and to run a harmless command that exits with code 7. Report the marker read and the exit code. A tool failure is expected; do not retry it. Do not edit files or access any other directory.'),('approval_observation','This is an approval-routing probe. Request approval for running a harmless command that prints VIGIL_APPROVAL_PROBE outside the read-only sandbox, using the tool execution-level escalation parameter. Do not run that command without approval. Do not edit or read unrelated files. Stop if approval is unavailable.')]
        data['turns']=[]
        for name,prompt in scenarios:
            start=len(client.events); result=client.request('turn/start',{'threadId':tid,'model':'gpt-6.1-sol','effort':'low','input':[{'type':'text','text':prompt}]})
            data['realTurnStarts']+=1; turnid=result['turn']['id']; ALIASES[turnid]=name
            end=time.monotonic()+90; completed=None; stopReason=None
            while time.monotonic()<end:
                time.sleep(.1); events=client.events[start:]
                done=[e for e in events if e.get('method')=='turn/completed' and e.get('params',{}).get('turn',{}).get('id')==turnid]
                approval=[e for e in events if 'id' in e and 'requestApproval' in e.get('method','')]
                tools=[e for e in events if e.get('method')=='item/started' and e.get('params',{}).get('item',{}).get('type') in ['commandExecution','fileChange','mcpToolCall','dynamicToolCall']]
                if done:completed=done[-1]['params']['turn'];break
                if approval:stopReason='observed_approval_no_response';break
                if len(tools)>4:stopReason='tool_item_circuit_breaker';break
            if not completed:
                stopReason=stopReason or 'time_circuit_breaker'
                client.request('turn/interrupt',{'threadId':tid,'turnId':turnid})
                deadline=time.monotonic()+8
                while time.monotonic()<deadline:
                    done=[e for e in client.events[start:] if e.get('method')=='turn/completed' and e.get('params',{}).get('turn',{}).get('id')==turnid]
                    if done:completed=done[-1]['params']['turn'];break
                    time.sleep(.1)
            events=client.events[start:]
            items=[e.get('params',{}).get('item',{}) for e in events if e.get('method')=='item/completed']
            approvals=[e for e in events if 'id' in e and 'requestApproval' in e.get('method','')]
            data['turns'].append({'name':name,'status':(completed or {}).get('status'),'error':clean((completed or {}).get('error')) if (completed or {}).get('error') else None,'stopReason':stopReason,'methods':dict(collections.Counter(e.get('method') for e in events)),'items':[{'type':i.get('type'),'status':i.get('status'),'exitCode':i.get('exitCode')} for i in items],'approvalMethods':[e.get('method') for e in approvals],'requestBindingValid':all(e.get('params',{}).get('threadId')==tid and e.get('params',{}).get('turnId')==turnid for e in approvals),'tokenUsage':[e.get('params',{}).get('tokenUsage') for e in events if e.get('method')=='thread/tokenUsage/updated']})
            print('SCENARIO',name,'status=',(completed or {}).get('status'),'stop=',stopReason,flush=True)
            if (completed or {}).get('status')=='failed':break
        data['fixtureFileUnchanged']=(workspace/'probe.txt').read_text()=='VIGIL_P0_FIXTURE_ONLY\n'
        data['workspaceFiles']=[p.name for p in workspace.iterdir()]
        data['stderrLineCount']=len(client.stderr)
    except Exception as e:data['failure']=clean(e)
    finally:client.close();write_result('managed',data)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--mode',choices=['discover','managed'],default='discover')
    parser.add_argument('--allow-real-model',action='store_true')
    args=parser.parse_args()
    if args.mode=='managed' and not args.allow_real_model:
        parser.error('Real model probe requires explicit authorization and --allow-real-model')
    {'discover':discover,'managed':managed}[args.mode]()
