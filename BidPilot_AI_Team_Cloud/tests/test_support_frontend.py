"""Run isolated AI-chat UI checks without network or browser dependencies."""
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
for(const id of ['supportForm','supportMessages','supportQuestion','supportProvider','supportMode','supportSend','supportClear','supportNotice','supportIntroTitle','supportIntroDescription','supportComposerHint'])node(id);
const nav=element('button'),chip=element('button');chip.dataset.supportQuestion='Why is auto-bidding paused?';
const sandbox={console,AbortController,setTimeout,clearTimeout,S:{me:null,epoch:0},window:{},document:{getElementById:node,createElement:element,
  querySelectorAll(selector){return selector==='[data-nav="support"]'?[nav]:selector==='[data-support-question]'?[chip]:[]}},
  api:async()=>({reply:'Unavailable',provider:'unavailable',model:'',ai_used:false,notice:''})};
vm.createContext(sandbox);vm.runInContext(source,sandbox);
const form=node('supportForm'),input=node('supportQuestion'),select=node('supportProvider'),send=node('supportSend'),clear=node('supportClear'),notice=node('supportNotice'),log=node('supportMessages');
const mode=node('supportMode');
const submit=()=>form.onsubmit({preventDefault(){}});
function mockPost(handler){sandbox.api=async(path,options)=>handler(path,JSON.parse(options.body),options)}
const allText=el=>[el.textContent,...el.children.map(allText)].join('\n');
const labels=()=>log.children.filter(el=>el.tagName==='article').map(el=>el.children[0].textContent);
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
(async()=>{
  assert.equal(send.disabled,true);
  assert.match(allText(log),/What would you like to work on/);
  sandbox.S.me={id:1};nav.listeners.click[0]();assert.equal(send.disabled,false);
  assert.equal(mode.value,'general');
  chip.listeners.click[0]();assert.equal(input.value,'Help me turn a rough idea into a clear plan.');assert.equal(calls.length,0);
  input.value='';await submit();assert.equal(calls.length,0);assert.match(notice.textContent,/between 1 and 3,000/);
  input.value='X'.repeat(3001);await submit();assert.equal(calls.length,0);

  // User content, provider text, models and notices are rendered as plain text.
  mockPost(async(path,body)=>{calls.push({path,body:JSON.parse(JSON.stringify(body))});return {
    reply:'<script>alert("unsafe")</script> Try Settings.',provider:'gemini',model:'model<img src=x>',ai_used:true,notice:'<img src=x onerror=alert(1)>'}});
  input.value='<img src=x onerror=alert(1)> Why does this fail?';select.value='gemini';
  await submit();
  assert.equal(calls[0].path,'/api/support/chat');assert.equal(calls[0].body.provider,'gemini');assert.equal(calls[0].body.mode,'general');assert.equal(calls[0].body.expected_user_id,1);assert.deepEqual(calls[0].body.history,[]);
  assert.match(allText(log),/<script>/);assert.match(allText(log),/<img src=x/);assert.equal(notice.textContent,'<img src=x onerror=alert(1)>');
  assert.match(labels()[1],/^Gemini · model<img/);

  // A local fallback must never claim the configured cloud provider produced it.
  mockPost(async(path,body)=>{calls.push({path,body:JSON.parse(JSON.stringify(body))});return {reply:'Local help for your API error.',provider:'openai',model:'configured-model',ai_used:false,notice:''}});
  mode.value='support';mode.onchange();
  input.value='Which key should I save?';select.value='openai';await submit();
  assert.equal(calls[1].body.provider,'openai');assert.equal(calls[1].body.mode,'support');assert.equal(calls[1].body.history.length,0);
  assert.equal(labels().at(-1),'Local help');assert.match(notice.textContent,/Live AI was not used/);
  mockPost(async()=>({reply:'Unexpected provider metadata.',provider:'unknown',model:'model',ai_used:true}));
  input.value='Explain the setup';await submit();assert.equal(labels().at(-1),'Local help');


  // Mode changes isolate conversations and invalidate a pending reply, while
  // retaining an unsent draft and the user's provider choice.
  const switched=deferred();let switchedSignal;
  mockPost(async(path,body,options)=>{switchedSignal=options.signal;return switched.promise});
  mode.value='general';mode.onchange();input.value='General conversation to switch';select.value='openai';
  const changing=submit();input.value='A newer draft';
  mode.value='support';mode.onchange();
  assert.equal(switchedSignal.aborted,true);assert.equal(input.value,'A newer draft');assert.equal(select.value,'openai');
  assert.ok(!allText(log).includes('General conversation to switch'));assert.match(allText(log),/How can I help with BidPilot/);
  switched.resolve({reply:'STALE GENERAL ANSWER',provider:'gemini',model:'old',ai_used:true});await changing;
  assert.ok(!allText(log).includes('STALE GENERAL ANSWER'));assert.equal(send.disabled,false);
  const modeCalls=[];
  mockPost(async(path,body)=>{modeCalls.push(body);return {reply:'App guidance',provider:'local_help',ai_used:false}});
  await submit();assert.equal(modeCalls[0].mode,'support');assert.equal(modeCalls[0].history.length,0);
  clear.onclick();assert.equal(mode.value,'support');assert.equal(select.value,'openai');

  // A failed general connection never masquerades as an AI answer or becomes
  // conversation history supplied to the next provider call.
  mode.value='general';mode.onchange();
  mockPost(async()=>({reply:'Live AI is unavailable. Check Settings.',provider:'unavailable',ai_used:false}));
  input.value='Translate a greeting';await submit();assert.equal(labels().at(-1),'AI unavailable');
  assert.match(notice.textContent,/No live AI answer/);
  let recoveredBody;
  mockPost(async(path,body)=>{recoveredBody=body;return {reply:'Bonjour',provider:'openai',model:'actual',ai_used:true}});
  input.value='Translate hello into French';await submit();assert.deepEqual(recoveredBody.history,[]);
  assert.equal(labels().at(-1),'OpenAI · actual');
  clear.onclick();

  // Pending sends are serialized, while text for the next question stays editable.
  const first=deferred();let pendingCalls=0;
  mockPost(async(path,body)=>{pendingCalls++;calls.push({path,body:JSON.parse(JSON.stringify(body))});return first.promise});
  input.value='Question currently sending';const pending=submit();assert.equal(send.disabled,true);assert.equal(input.value,'');
  await submit();assert.equal(pendingCalls,1);
  input.value='A newer question drafted while waiting';
  select.value='gemini';
  first.resolve({reply:'A complete answer.',provider:'openai',model:'actual-model',ai_used:true,notice:''});await pending;
  assert.equal(input.value,'A newer question drafted while waiting');assert.equal(send.disabled,false);
  assert.equal(labels().at(-1),'OpenAI · actual-model');

  // A failure restores an empty input but never overwrites a newer draft.
  const failed=deferred();mockPost(async()=>failed.promise);
  input.value='Retain this failed question';const failing=submit();
  input.value='Newer unsent draft';failed.reject(Error('Provider temporarily unavailable'));await failing;
  assert.equal(input.value,'Newer unsent draft');assert.match(notice.textContent,/temporarily unavailable/);
  mockPost(async()=>{throw Error('Network unavailable')});input.value='Restore me after failure';await submit();
  assert.equal(input.value,'Restore me after failure');

  // Clearing a pending request invalidates its response without unlocking a newer request.
  const old=deferred(),newer=deferred();let which=0;mockPost(async()=>++which===1?old.promise:newer.promise);
  input.value='Old question to clear';const oldRequest=submit();clear.onclick();assert.equal(send.disabled,false);
  input.value='New question after clearing';const newRequest=submit();assert.equal(send.disabled,true);
  old.resolve({reply:'OLD CLEARED SECRET RESULT',provider:'gemini',model:'old-model',ai_used:true});await oldRequest;
  assert.equal(send.disabled,true);assert.ok(!allText(log).includes('OLD CLEARED SECRET RESULT'));
  newer.resolve({reply:'Current answer only',provider:'openai',model:'new-model',ai_used:true});await newRequest;
  assert.match(allText(log),/Current answer only/);assert.equal(send.disabled,false);

  // A session change clears conversation state, drafts and delayed account responses.
  const previous=deferred();mockPost(async()=>previous.promise);
  input.value='Private old-account question';const switching=submit();sandbox.S.epoch++;sandbox.S.me=null;sandbox.window.clearSupportChat();
  assert.equal(input.value,'');assert.equal(send.disabled,true);assert.ok(!allText(log).includes('Private old-account question'));
  previous.resolve({reply:'Previous owner private answer',provider:'gemini',model:'old',ai_used:true});await switching;
  assert.ok(!allText(log).includes('Previous owner private answer'));
  sandbox.S.me={id:2};nav.listeners.click[0]();assert.equal(send.disabled,false);

  // Another tab may replace the cookie before the account refresh detects it.
  // The expected-account server guard must clear old history and reload.
  let reloads=0;sandbox.window.location={reload(){reloads++}};
  mockPost(async()=>{const error=Error('Account changed');error.status=409;throw error});
  input.value='Old account history must stay private';await submit();
  assert.equal(reloads,1);assert.match(notice.textContent,/account changed/);
  assert.ok(!allText(log).includes('Old account history must stay private'));

  // History is bounded, truncates context messages, and excludes the new question.
  let longestHistory=0;const historyCalls=[];
  mockPost(async(path,body)=>{historyCalls.push(JSON.parse(JSON.stringify(body)));longestHistory=Math.max(longestHistory,body.history.length);return {reply:'A'.repeat(9000),provider:'gemini',model:'actual',ai_used:true}});
  for(let index=0;index<8;index++){input.value='Question '+index+(index===0?'x'.repeat(2500):'');select.value='not-a-provider';await submit()}
  assert.ok(longestHistory<=10);assert.equal(historyCalls[0].provider,'auto');assert.equal(historyCalls[0].history.length,0);
  assert.ok(historyCalls.every(call=>call.history.every(entry=>entry.content.length<=2000)));
  assert.ok(historyCalls.every(call=>!call.history.some(entry=>entry.role==='user'&&entry.content===call.question)));
  assert.ok(log.children.length<=10);assert.ok(log.children.every(entry=>entry.children[1].textContent.length<=8000));
  sandbox.window.clearSupportChat();assert.match(allText(log),/What would you like to work on/);
  console.log('AI chat rendering, modes, provider truthfulness, privacy and lifecycle checks passed');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', harness], cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_chat_markup_connects_accessible_modes_and_default_general_assistant():
    from html.parser import HTMLParser

    class ChatMarkup(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = []
            self.select = None
            self.options = {}
            self.labels = set()
            self.chat_nav = False

        def handle_starttag(self, tag, attributes):
            attrs = dict(attributes)
            if 'id' in attrs:
                self.ids.append(attrs['id'])
            if tag == 'select':
                self.select = attrs.get('id')
            elif tag == 'option' and self.select:
                self.options.setdefault(self.select, []).append(attrs.get('value'))
            if tag == 'button' and attrs.get('data-nav') == 'support':
                self.chat_nav = attrs.get('aria-label') == 'AI Chat'

        def handle_endtag(self, tag):
            if tag == 'select':
                self.select = None

    markup = ChatMarkup()
    markup.feed((ROOT / 'app/static/index.html').read_text())
    assert len(markup.ids) == len(set(markup.ids))
    assert markup.chat_nav
    assert markup.options['supportMode'] == ['general', 'support']
    assert markup.options['supportProvider'] == ['auto', 'gemini', 'openai']
    assert {'supportForm', 'supportMessages', 'supportQuestion', 'supportNotice',
            'supportIntroTitle', 'supportIntroDescription', 'supportComposerHint'} <= set(markup.ids)
