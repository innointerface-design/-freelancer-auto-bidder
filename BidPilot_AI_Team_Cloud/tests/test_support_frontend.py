"""Run isolated help-chat UI checks without network or browser dependencies."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which('node'), reason='Node is required for browser JavaScript checks')
def test_help_chat_safe_rendering_and_request_lifecycle():
    harness = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('app/static/support.js','utf8');
const nodes=new Map(),calls=[];
function element(tag='div'){
  return {
    tagName:tag,value:'',disabled:false,textContent:'',className:'',dataset:{},children:[],attributes:{},listeners:{},
    set innerHTML(_){throw Error('Untrusted HTML rendering is forbidden')},
    get innerHTML(){return ''},
    append(...items){this.children.push(...items)},replaceChildren(...items){this.children=[...items];this.textContent=''},
    setAttribute(name,value){this.attributes[name]=value},focus(){this.focused=true},
    addEventListener(type,handler){(this.listeners[type]??=[]).push(handler)},
    get scrollHeight(){return this.children.length*100},
  };
}
function node(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)}
for(const id of ['supportForm','supportMessages','supportQuestion','supportProvider','supportSend','supportClear','supportNotice'])node(id);
const nav=element('button'),chip=element('button');chip.dataset.supportQuestion='Why is auto-bidding paused?';
const sandbox={console,S:{me:null,epoch:0},window:{},document:{getElementById:node,createElement:element,
  querySelectorAll(selector){return selector==='[data-nav="support"]'?[nav]:selector==='[data-support-question]'?[chip]:[]}},
  post:async(path,body)=>{calls.push({path,body:JSON.parse(JSON.stringify(body))});return {reply:'Local answer',provider:'local_help',model:'',ai_used:false,notice:''}}};
vm.createContext(sandbox);vm.runInContext(source,sandbox);
const form=node('supportForm'),input=node('supportQuestion'),select=node('supportProvider'),send=node('supportSend'),clear=node('supportClear'),notice=node('supportNotice'),log=node('supportMessages');
const submit=()=>form.onsubmit({preventDefault(){}});
const allText=el=>[el.textContent,...el.children.map(allText)].join('\n');
const labels=()=>log.children.filter(el=>el.tagName==='article').map(el=>el.children[0].textContent);
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
(async()=>{
  assert.equal(send.disabled,true);
  assert.match(allText(log),/How can I help/);
  sandbox.S.me={id:1};nav.listeners.click[0]();assert.equal(send.disabled,false);
  chip.listeners.click[0]();assert.equal(input.value,'Why is auto-bidding paused?');assert.equal(calls.length,0);
  input.value='';await submit();assert.equal(calls.length,0);assert.match(notice.textContent,/between 1 and 3,000/);
  input.value='X'.repeat(3001);await submit();assert.equal(calls.length,0);

  // User content, provider text, models and notices are rendered as plain text.
  sandbox.post=async(path,body)=>{calls.push({path,body:JSON.parse(JSON.stringify(body))});return {
    reply:'<script>alert("unsafe")</script> Try Settings.',provider:'gemini',model:'model<img src=x>',ai_used:true,notice:'<img src=x onerror=alert(1)>'}};
  input.value='<img src=x onerror=alert(1)> Why does this fail?';select.value='gemini';
  await submit();
  assert.equal(calls[0].path,'/api/support/chat');assert.equal(calls[0].body.provider,'gemini');assert.equal(calls[0].body.expected_user_id,1);assert.deepEqual(calls[0].body.history,[]);
  assert.match(allText(log),/<script>/);assert.match(allText(log),/<img src=x/);assert.equal(notice.textContent,'<img src=x onerror=alert(1)>');
  assert.match(labels()[1],/^Gemini · model<img/);

  // A local fallback must never claim the configured cloud provider produced it.
  sandbox.post=async(path,body)=>{calls.push({path,body:JSON.parse(JSON.stringify(body))});return {reply:'Local help for your API error.',provider:'openai',model:'configured-model',ai_used:false,notice:''}};
  input.value='Which key should I save?';select.value='openai';await submit();
  assert.equal(calls[1].body.provider,'openai');assert.equal(calls[1].body.history.length,2);
  assert.equal(labels().at(-1),'Local help');assert.match(notice.textContent,/Live AI was not used/);
  sandbox.post=async()=>({reply:'Unexpected provider metadata.',provider:'unknown',model:'model',ai_used:true});
  input.value='Explain the setup';await submit();assert.equal(labels().at(-1),'Local help');

  // Pending sends are serialized, while text for the next question stays editable.
  const first=deferred();let pendingCalls=0;
  sandbox.post=async(path,body)=>{pendingCalls++;calls.push({path,body:JSON.parse(JSON.stringify(body))});return first.promise};
  input.value='Question currently sending';const pending=submit();assert.equal(send.disabled,true);assert.equal(input.value,'');
  await submit();assert.equal(pendingCalls,1);
  input.value='A newer question drafted while waiting';
  first.resolve({reply:'A complete answer.',provider:'openai',model:'actual-model',ai_used:true,notice:''});await pending;
  assert.equal(input.value,'A newer question drafted while waiting');assert.equal(send.disabled,false);
  assert.equal(labels().at(-1),'OpenAI · actual-model');

  // A failure restores an empty input but never overwrites a newer draft.
  const failed=deferred();sandbox.post=async()=>failed.promise;
  input.value='Retain this failed question';const failing=submit();
  input.value='Newer unsent draft';failed.reject(Error('Provider temporarily unavailable'));await failing;
  assert.equal(input.value,'Newer unsent draft');assert.match(notice.textContent,/temporarily unavailable/);
  sandbox.post=async()=>{throw Error('Network unavailable')};input.value='Restore me after failure';await submit();
  assert.equal(input.value,'Restore me after failure');

  // Clearing a pending request invalidates its response without unlocking a newer request.
  const old=deferred(),newer=deferred();let which=0;sandbox.post=async()=>++which===1?old.promise:newer.promise;
  input.value='Old question to clear';const oldRequest=submit();clear.onclick();assert.equal(send.disabled,false);
  input.value='New question after clearing';const newRequest=submit();assert.equal(send.disabled,true);
  old.resolve({reply:'OLD CLEARED SECRET RESULT',provider:'gemini',model:'old-model',ai_used:true});await oldRequest;
  assert.equal(send.disabled,true);assert.ok(!allText(log).includes('OLD CLEARED SECRET RESULT'));
  newer.resolve({reply:'Current answer only',provider:'openai',model:'new-model',ai_used:true});await newRequest;
  assert.match(allText(log),/Current answer only/);assert.equal(send.disabled,false);

  // A session change clears conversation state, drafts and delayed account responses.
  const previous=deferred();sandbox.post=async()=>previous.promise;
  input.value='Private old-account question';const switching=submit();sandbox.S.epoch++;sandbox.S.me=null;sandbox.window.clearSupportChat();
  assert.equal(input.value,'');assert.equal(send.disabled,true);assert.ok(!allText(log).includes('Private old-account question'));
  previous.resolve({reply:'Previous owner private answer',provider:'gemini',model:'old',ai_used:true});await switching;
  assert.ok(!allText(log).includes('Previous owner private answer'));
  sandbox.S.me={id:2};nav.listeners.click[0]();assert.equal(send.disabled,false);

  // Another tab may replace the cookie before the account refresh detects it.
  // The expected-account server guard must clear old history and reload.
  let reloads=0;sandbox.window.location={reload(){reloads++}};
  sandbox.post=async()=>{const error=Error('Account changed');error.status=409;throw error};
  input.value='Old account history must stay private';await submit();
  assert.equal(reloads,1);assert.match(notice.textContent,/account changed/);
  assert.ok(!allText(log).includes('Old account history must stay private'));

  // History is bounded, truncates context messages, and excludes the new question.
  let longestHistory=0;const historyCalls=[];
  sandbox.post=async(path,body)=>{historyCalls.push(JSON.parse(JSON.stringify(body)));longestHistory=Math.max(longestHistory,body.history.length);return {reply:'A'.repeat(4500),provider:'local_help',model:'',ai_used:false}};
  for(let index=0;index<8;index++){input.value='Question '+index+(index===0?'x'.repeat(2500):'');select.value='not-a-provider';await submit()}
  assert.ok(longestHistory<=10);assert.equal(historyCalls[0].provider,'auto');assert.equal(historyCalls[0].history.length,0);
  assert.ok(historyCalls.every(call=>call.history.every(entry=>entry.content.length<=2000)));
  assert.ok(historyCalls.every(call=>!call.history.some(entry=>entry.role==='user'&&entry.content===call.question)));
  assert.ok(log.children.length<=10);assert.ok(log.children.every(entry=>entry.children[1].textContent.length<=4000));
  sandbox.window.clearSupportChat();assert.match(allText(log),/How can I help/);
  console.log('Support chat rendering, provider truthfulness, privacy and lifecycle checks passed');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', harness], cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
