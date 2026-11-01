"""Regression coverage for account, editor, navigation and polling races."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which('node'), reason='Node is required for browser checks')
def test_drafts_latest_results_and_account_connection_guards():
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(),events={};let handler=async()=>reply({}),reloads=0;
function node(id){
 if(!nodes.has(id)){
  const classes=new Set();let value='',html='',text='';
  const el={checked:false,disabled:false,dataset:{},listeners:{},
   get innerHTML(){return html},set innerHTML(next){html=String(next);text=html},
   get textContent(){return text},set textContent(next){text=String(next);html=text},
   get value(){return value},set value(next){value=String(next)},
   classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle:(x,on)=>on?classes.add(x):classes.delete(x)},
   addEventListener(type,fn){(this.listeners[type]??=[]).push(fn)},
   querySelector:q=>q==='button[type="submit"]'?node(id+':submit'):null,
   toggleAttribute(name,on){if(name==='disabled')this.disabled=on},removeAttribute(){},focus(){},reset(){},setAttribute(){},
  };
  el.elements=new Proxy({}, {get:(_,key)=>node(id+':'+String(key))});nodes.set(id,el);
 }return nodes.get(id);
}
const reply=(body,status=200,headers={})=>({ok:status<400,status,headers:{get:name=>headers[name]??null},json:async()=>body});
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve}}
const sandbox={URL,console,document:{getElementById:node,querySelectorAll:()=>[],addEventListener:(name,fn)=>events[name]=fn},
 window:{scrollTo(){},location:{reload(){reloads++}}},navigator:{},confirm:()=>true,
 setTimeout(){},clearTimeout(){},setInterval(){},fetch:(url,options)=>handler(url,options)};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync('app/static/app.js','utf8').replace('(async()=>{await initialize()})();',''),sandbox);
const run=code=>vm.runInContext(code,sandbox);
const user={id:1,role:'admin',name:'Saved Name',email:'owner@example.test',skills:'Python',subscription_active:true,connected:true,
 preferred_ai:'auto',gemini_model:'saved-gemini',openai_model:'saved-openai',auto_admin_allowed:true};
sandbox.user=user;
(async()=>{
 run('S.me={...user};A.settings={};renderSettings()');
 // Navigating away and back preserves unsaved profile, checkbox, provider and
 // model edits. Clean fields may still pick up changes from another tab.
 node('settingsForm:name').value='Unsaved Name';node('settingsForm:auto_requested').checked=true;
 node('aiKeysForm:gemini_model').value='unsaved-model';node('aiKeysForm:preferred_ai').value='openai';
 node('aiKeysForm:gemini_key').value='SYNTHETIC-NEWER-KEY';
 run("S.me.skills='Python, SQL';S.me.openai_model='updated-saved-openai';navigate('overview');navigate('settings')");
 assert.equal(node('settingsForm:name').value,'Unsaved Name');assert.equal(node('settingsForm:auto_requested').checked,true);
 assert.equal(node('settingsForm:skills').value,'Python, SQL');assert.equal(node('aiKeysForm:gemini_model').value,'unsaved-model');
 assert.equal(node('aiKeysForm:preferred_ai').value,'openai');assert.equal(node('aiKeysForm:openai_model').value,'updated-saved-openai');
 assert.equal(node('aiKeysForm:gemini_key').value,'SYNTHETIC-NEWER-KEY');
 // Automatic chat must not advertise readiness during administrator/API pauses.
 run('S.platform_auto=true;S.me.auto_chat_greeting=true;S.me.auto_admin_allowed=false;renderStatus()');
 assert.equal(node('chatStatus').textContent,'Off');
 run("S.me.auto_admin_allowed=true;S.me.api_pause_reason='auth_rejected';renderStatus()");assert.equal(node('chatStatus').textContent,'Off');
 run("S.me.api_pause_reason='';renderStatus()");assert.equal(node('chatStatus').textContent,'Enabled (worker required)');

 // Later live-bid and member-inspection requests win, even when an older
 // provider request finishes after them.
 const first=deferred(),second=deferred();let calls=0;
 handler=()=>++calls===1?first.promise:second.promise;
 const oldBids=run('loadLiveBids()'),newBids=run('loadLiveBids()');
 second.resolve(reply({connected:true,bids:[{project_title:'Latest project',project_id:2}]}));await newBids;
 first.resolve(reply({connected:true,bids:[{project_title:'Earlier project',project_id:1}]}));await oldBids;
 assert.match(node('liveBidsBody').innerHTML,/Latest project/);assert.doesNotMatch(node('liveBidsBody').innerHTML,/Earlier project/);
 const inspectA=deferred(),inspectB=deferred();calls=0;handler=()=>++calls===1?inspectA.promise:inspectB.promise;
 const oldMember=run('inspectMember(2)'),newMember=run('inspectMember(3)');
 const overview=name=>({member:{name,email:name+'@example.test'},counts:{jobs:0,bids:0,submitted:0},jobs:[],activity:[]});
 inspectB.resolve(reply(overview('Latest Member')));await newMember;
 inspectA.resolve(reply(overview('Earlier Member')));await oldMember;
 assert.match(node('teamOverview').innerHTML,/Latest Member/);assert.doesNotMatch(node('teamOverview').innerHTML,/Earlier Member/);
 // Logout invalidates both successful and failed live-bid responses without
 // putting an old account's data or failure text back into the cleared shell.
 const staleBids=deferred();handler=()=>staleBids.promise;const stale=run('loadLiveBids()');run('resetSession()');
 staleBids.resolve(reply({detail:'OLD-ACCOUNT-ERROR'},503));await stale;assert.equal(node('liveBidsBody').innerHTML,'');

 // Connection mutations are serialized and preserve edits typed while the
 // provider verifies the submitted token. Updated identities use applyAccount.
 run('S.me={...user};renderSettings()');node('connectToken').value='SYNTHETIC-OLD-TOKEN';
 const connect=deferred();calls=0;
 handler=async url=>{if(url==='/api/connect'){calls++;return connect.promise}assert.equal(url,'/api/me');return reply({user:{...user,skills:'Imported, Skills'}})};
 const connecting=node('connectForm').onsubmit({preventDefault(){}});
 await node('connectForm').onsubmit({preventDefault(){}});assert.equal(calls,1);assert.equal(node('disconnect').disabled,true);
 node('connectToken').value='SYNTHETIC-NEW-TOKEN';node('settingsForm:name').value='New Profile Draft';
 node('aiKeysForm:gemini_model').value='New Model Draft';
 connect.resolve(reply({freelancer_name:'verified',skills_imported:['Imported']}));await connecting;
 assert.equal(node('connectToken').value,'SYNTHETIC-NEW-TOKEN');assert.equal(node('settingsForm:name').value,'New Profile Draft');
 assert.equal(node('settingsForm:skills').value,'Imported, Skills');assert.equal(node('aiKeysForm:gemini_model').value,'New Model Draft');
 assert.equal(node('disconnect').disabled,false);
 handler=async url=>reply(url==='/api/me'?{user:{id:2,role:'member',name:'Other Account',email:'other@example.test'}}:{ok:true});
 await node('disconnect').onclick();assert.equal(reloads,1);assert.equal(run('S.me'),null);

 // A save must finish before preparing/submitting a bid; editing while the
 // prepare endpoint is running requires reviewing and preparing the new text.
 run("S.me={...user};S.selected={id:7,demo:false,currency:'USD',type:'fixed'};$('bidProposal').value='A complete reviewed proposal with enough text.';$('bidAmount').value='10';$('bidDays').value='3';$('bidMilestone').value='100'");
 const draft=deferred();calls=0;handler=()=>{calls++;return draft.promise};
 sandbox.originalRefresh=run('refresh');run('refresh=async()=>{}');
 const saving=run('saveDraft()');await run('saveDraft()');await run('instantBid()');assert.equal(calls,1);
 draft.resolve(reply({ok:true}));await saving;assert.equal(run('S.bidBusy'),false);
 const preparing=deferred();let prepareStarted;const started=new Promise(done=>prepareStarted=done);
 handler=async url=>{if(url.endsWith('/prepare')){prepareStarted();return preparing.promise}return reply({ok:true})};
 const preparation=run('prepareBid()');await started;node('bidProposal').value='A newer edited proposal still needs my review.';
 preparing.resolve(reply({intent:'stale-intent',preview:{}}));await preparation;
 assert.equal(run('S.intent'),null);assert.match(node('toast').textContent,/prepare again/);
 run("S.intent={id:7,token:'pending'};S.bidBusy=true;$('confirmation').value='PLACE BID'");node('confirmation').oninput();
 assert.equal(node('confirmSubmit').disabled,true);

 // HTML/text proxy errors still carry HTTP status and Retry-After, including
 // fractional delays; a non-JSON 401 clears private account state immediately.
 handler=async()=>({ok:false,status:429,headers:{get:name=>name==='Retry-After'?'1.5':null},json:async()=>{throw Error('HTML error')}});
 await assert.rejects(run("api('/api/rate-limit')"),error=>error.status===429&&error.retryAfter===2);
 handler=async()=>({ok:false,status:401,json:async()=>{throw Error('HTML error')}});
 await assert.rejects(run("api('/api/expired')"),error=>error.status===401);assert.equal(run('S.me'),null);
 console.log('Audit navigation, draft, response order, connection and HTTP checks passed');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not shutil.which('node'), reason='Node is required for browser checks')
def test_status_pollers_respect_server_retry_after():
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
async function check(file,refreshName){
 let now=100000,calls=0;const nodes=new Map();
 function node(id){if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',disabled:false,setAttribute(){}});return nodes.get(id)}
 class ClockDate extends Date{static now(){return now}}
 const sandbox={Date:ClockDate,performance:{now:()=>now},console,S:{epoch:0,nav:file==='live.js'?'overview':'settings',me:{id:1}},
  document:{hidden:false,getElementById:node,querySelectorAll:()=>[],addEventListener(){}},window:{location:{reload(){}}},
  setInterval(){},setTimeout(){},clearTimeout(){},
  api:async()=>{calls++;const error=Error('Slow down');error.status=429;error.retryAfter=120;throw error}};
 vm.createContext(sandbox);vm.runInContext(fs.readFileSync('app/static/'+file,'utf8'),sandbox);
 await sandbox.window[refreshName]();await Promise.resolve();
 const before=calls;assert.equal(before,1);now+=30000;await sandbox.window[refreshName]();assert.equal(calls,before);
 now+=90000;await sandbox.window[refreshName]();assert.equal(calls,before+1);
}
(async()=>{await check('live.js','refreshAutomationLive');await check('security.js','refreshAccountSecurity')})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
