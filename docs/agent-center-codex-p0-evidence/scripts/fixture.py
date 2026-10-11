"""Real App Server protocol tests with a loopback-only deterministic model fixture."""
import json, os, secrets, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from probe import RPC, ROOT, ALIASES, clean, write_result

secret=secrets.token_urlsafe(32)
os.environ['VIGIL_P0_FIXTURE_KEY']=secret
state={'mode':'fail','requests':0,'unauthorized':0,'toolAvailable':False}
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        if self.headers.get('Authorization')!='Bearer '+secret:
            state['unauthorized']+=1; self.send_response(401); self.end_headers(); return
        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))))
        state['requests']+=1
        if state['mode']=='fail' or state['requests']>4:
            self.send_response(400); self.send_header('Content-Type','application/json'); self.end_headers()
            self.wfile.write(b'{"error":{"message":"VIGIL_P0_DETERMINISTIC_FAILURE","type":"invalid_request_error","code":"p0_fixture"}}'); return
        names=[t.get('name') for t in body.get('tools',[])]
        state['toolAvailable']='request_user_input' in names
        args=json.dumps({'questions':[{'header':'P0 probe','id':'probe_choice','question':'Which isolated fixture choice?', 'options':[{'label':'A','description':'Fixture A'},{'label':'B','description':'Fixture B'}]}]})
        tool='request_user_input'
        if state['mode']=='approval':
            tool='exec_command'; args=json.dumps({'cmd':'Write-Output VIGIL_P0_FIXTURE','sandbox_permissions':'require_escalated','justification':'Local deterministic ownership probe; do not approve.'})
        item={'type':'function_call','id':'fc_p0','call_id':'call_p0','name':tool,'arguments':args,'status':'completed'}
        response={'id':'resp_p0','object':'response','created_at':int(time.time()),'status':'in_progress','output':[]}
        events=[{'type':'response.created','response':response},
                {'type':'response.output_item.added','output_index':0,'item':{**item,'arguments':'','status':'in_progress'}},
                {'type':'response.function_call_arguments.delta','item_id':'fc_p0','output_index':0,'delta':args},
                {'type':'response.function_call_arguments.done','item_id':'fc_p0','output_index':0,'arguments':args},
                {'type':'response.output_item.done','output_index':0,'item':item},
                {'type':'response.completed','response':{**response,'status':'completed','output':[item],'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}}]
        self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.end_headers()
        for event in events: self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode()); self.wfile.flush()

server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
home=ROOT/'fixture-home'; home.mkdir(exist_ok=True)
workspace=ROOT/'fixture-workspace'; workspace.mkdir(exist_ok=True)
extra=['-c','model_provider="p0_fixture"','-c','model_providers.p0_fixture.name="P0 fixture"','-c',f'model_providers.p0_fixture.base_url="http://127.0.0.1:{server.server_port}/v1"','-c','model_providers.p0_fixture.wire_api="responses"','-c','model_providers.p0_fixture.env_key="VIGIL_P0_FIXTURE_KEY"','-c','model_providers.p0_fixture.requires_openai_auth=false','-c','model_providers.p0_fixture.request_max_retries=0','-c','model_providers.p0_fixture.stream_max_retries=0']
data={'hostedModelCalls':0,'fixture':'authenticated loopback Responses fixture','home':'<PROBE>/fixture-home','approvalResponsesSent':0}
client=None
def wait_turn(tid,turnid,start,observe_input=False):
    deadline=time.monotonic()+25
    observed=None
    while time.monotonic()<deadline:
        events=client.events[start:]
        requests=[e for e in events if e.get('method')=='item/tool/requestUserInput' and 'id' in e]
        completed=[e for e in events if e.get('method')=='turn/completed' and e.get('params',{}).get('turn',{}).get('id')==turnid]
        if observe_input and requests:
            request=requests[0]; params=request['params']
            observed={'method':request['method'],'bindingValid':params.get('threadId')==tid and params.get('turnId')==turnid,'isBlocking':params.get('isBlocking'),'questionCount':len(params.get('questions',[]))}
            client.request('turn/interrupt',{'threadId':tid,'turnId':turnid}); observe_input=False
        if completed:
            result={'status':completed[-1]['params']['turn']['status'],'userInput':observed,'methods':sorted(set(e.get('method') for e in events))}
            return result
        time.sleep(.1)
    client.request('turn/interrupt',{'threadId':tid,'turnId':turnid})
    return {'timeout':True,'userInput':observed}
def run_fixture():
    global client
    client=RPC('fixture',home=home,extra=extra)
    _run_fixture()
def _run_fixture():
  global client
  try:
    client.initialize()
    response=client.request('thread/start',{'model':'gpt-6.1-sol','modelProvider':'p0_fixture','cwd':str(workspace),'runtimeWorkspaceRoots':[str(workspace)],'sandbox':'read-only','approvalPolicy':'on-request','approvalsReviewer':'user','ephemeral':False,'config':{'web_search':'disabled','features.apps':False}})
    tid=response['thread']['id']; ALIASES[tid]='fixture_thread'
    start=len(client.events)
    turn=client.request('turn/start',{'threadId':tid,'input':[{'type':'text','text':'Deterministic protocol fixture.'}]})['turn']; ALIASES[turn['id']]='fixture_failure'
    data['failedTurn']=wait_turn(tid,turn['id'],start)
    client.close()
    client=RPC('fixture_resume',home=home,extra=extra); client.initialize()
    resumed=client.request('thread/resume',{'threadId':tid,'cwd':str(workspace),'modelProvider':'p0_fixture','sandbox':'read-only','approvalPolicy':'on-request','approvalsReviewer':'user'})
    loaded=client.request('thread/loaded/list',{})
    data['resume']={'sameThread':resumed.get('thread',{}).get('id')==tid,'persistedTurns':len(resumed.get('thread',{}).get('turns',[])),'loaded':tid in loaded.get('data',[])}
    state['mode']='user_input'; start=len(client.events)
    turn=client.request('turn/start',{'threadId':tid,'input':[{'type':'text','text':'Local fixture question.'}],'collaborationMode':{'mode':'plan','settings':{'model':'gpt-6.1-sol','reasoning_effort':'low'}}})['turn']; ALIASES[turn['id']]='fixture_user_input'
    data['userInputTurn']=wait_turn(tid,turn['id'],start,True)
  except Exception as exc: data['failure']=clean(exc)
  finally:
    client.close(); server.shutdown(); server.server_close()
    data['fixtureRequestCount']=state['requests']; data['unauthorizedRequests']=state['unauthorized']; data['requestUserInputToolOffered']=state['toolAvailable']
    write_result('fixture',data)
if __name__=='__main__': run_fixture()
