const test=require('node:test'),assert=require('node:assert/strict'),{EventEmitter}=require('node:events');
const {register}=require('../electron/agent-center-notifications');
function fixture(supported=true){let handler,shown=0;const frame={url:'file:///C:/fixture/index.html'};const wc={mainFrame:frame,send(){}};const win={webContents:wc,isDestroyed:()=>false};
class N extends EventEmitter{static isSupported(){return supported;}show(){shown++;}}
register({ipcMain:{handle:(_,fn)=>handler=fn},Notification:N,getWindow:()=>win,indexPath:'C:/fixture/index.html'});
return{call:(p,e={sender:wc,senderFrame:frame})=>handler(e,p),shown:()=>shown,wc,frame};}
test('native notification once per key',()=>{const f=fixture();assert.equal(f.call({kind:'approval',key:'c:g:t:r:approval'}).ok,true);assert.equal(f.call({kind:'approval',key:'c:g:t:r:approval'}).duplicate,true);assert.equal(f.shown(),1);});
test('reject foreign sender, frame and notification payload',()=>{const f=fixture();assert.equal(f.call({kind:'approval',key:'x'},{sender:{},senderFrame:f.frame}).ok,false);assert.equal(f.call({kind:'approval',key:'x'},{sender:f.wc,senderFrame:{url:f.frame.url}}).ok,false);for(const p of [{kind:'approve',key:'x'},{kind:'toString',key:'x'},{kind:'failure',key:'private text!'}])assert.equal(f.call(p).ok,false);assert.equal(f.shown(),0);});
test('unsupported desktop reports failure for in-app fallback',()=>{const f=fixture(false);assert.equal(f.call({kind:'input',key:'x'}).err,'notifications_unavailable');});
