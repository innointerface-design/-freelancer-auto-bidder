"""Execute the browser code's security and billing behavior with Node, without npm dependencies."""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node is required for browser JavaScript checks')
class FrontendTests(unittest.TestCase):
    def test_browser_rendering_and_account_flows(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('app/static/index.html','utf8');
assert.ok(!html.includes('style='),'CSP-incompatible inline styles');
for(const method of ['jazzcash','easypaisa','bank','stripe'])assert.ok(html.includes('value="'+method+'"'),'Missing canonical payment method');
assert.ok(!html.includes('value="PKR"'),'Unsupported receipt currency exposed');
const nodes=new Map();
function node(id){
 if(!nodes.has(id)){
  const classes=new Set();
  const el={value:'',checked:false,disabled:false,innerHTML:'',textContent:'',dataset:{},listeners:{},
   classList:{add:v=>classes.add(v),remove:v=>classes.delete(v),contains:v=>classes.has(v),toggle:(v,on)=>{if(on)classes.add(v);else classes.delete(v)}},
   addEventListener(type,handler){(this.listeners[type]??=[]).push(handler)},querySelector(selector){return selector==='button[type="submit"]'?node(id+':submit'):null},toggleAttribute(name,on){if(name==='disabled')this.disabled=on},focus(){},reset(){},removeAttribute(){}};
  el.elements=new Proxy({}, {get:(_,key)=>node(id+':'+String(key))});nodes.set(id,el);
 }
 return nodes.get(id);
}
const requests=[];
const sandbox={URL,console,document:{getElementById:node,querySelectorAll:()=>[],addEventListener(){}},
 window:{scrollTo(){}},navigator:{},setInterval(){},setTimeout(){},clearTimeout(){},
 fetch:async(url,opts)=>{requests.push({url,opts});return {ok:true,status:200,json:async()=>url==='/api/billing'?{plan_tier:'paid_starter',monthly_fee:10,subscription_status:'pending',payments:[]}:url==='/api/jobs/7/proposal'?{proposal:'Local template proposal for this project.'}: {}}}};
vm.createContext(sandbox);
const source=fs.readFileSync('app/static/app.js','utf8').replace('(async()=>{await initialize()})();','');
vm.runInContext(source,sandbox);
(async()=>{
 await vm.runInContext(`(async()=>{
  if(safePaymentUrl('javascript:alert(1)')!=='')throw Error('Unsafe payment URL allowed');
  if(safePaymentUrl('https://user:pass@example.com')!=='')throw Error('Credential URL allowed');
  if(safeUrl('https://evil.example/projects/1')!=='')throw Error('Unsafe project host allowed');
  S.jobs=[{id:'1\" onclick=\"alert(1)',title:'<img src=x onerror=alert(1)>',score:90,country:'<svg onload=alert(1)>',currency:'USD',budget_min:10,budget_max:20}];
  renderTopJobs();
  const html=$('topJobs').innerHTML;
  if(html.includes('<img')||html.includes('<svg')||html.includes('data-job="1" onclick'))throw Error('Unescaped project markup');
  if(!html.includes('&lt;img'))throw Error('Expected escaped title');
  S.me={email:'test@example.com',role:'member'};
  S.payment_methods={stripe_url:'javascript:alert(1)'};
  await loadBilling();
  if($('paymentMethodsBox').innerHTML.includes('javascript:'))throw Error('Payment javascript URL rendered');
  if(!$('paymentMethodsBox').innerHTML.includes('not configured'))throw Error('Fake payment destination shown');
  if(!$('myPlanBanner').innerHTML.includes('$10/month')||!$('myPlanBanner').innerHTML.includes('$40/month'))throw Error('Incorrect plan prices');
  if(!$('whatsappApproveBtn').classList.contains('hidden'))throw Error('Fake WhatsApp contact shown');
  S.me={email:'test@example.com',role:'member',subscription_active:false,plan_tier:'paid_starter',subscription_status:'pending'};
  S.selected={id:7};await makeProposal(true);
  renderStatus();
  if(!$('planBadgeDesc').textContent.includes('inactive'))throw Error('Unpaid account claims bidding active');
  S.me={...S.me,subscription_active:true,subscription_status:'trial',auto_requested:true,auto_admin_allowed:true,connected:true};S.platform_auto=true;
  renderStatus();
  if(!$('autoStatus').textContent.includes('Enabled'))throw Error('Active trial falsely paused');
  initialize=async()=>{};
  $('authToggle').onclick();
  if(!$('registerName').required||$('loginPassword').minLength!==12)throw Error('Registration constraints missing');
  $('registerName').value='Test Customer';$('loginEmail').value='customer@example.com';$('loginPassword').value='a long password 123';
  await $('loginForm').onsubmit({preventDefault(){}});
  const f=$('paymentForm');
  f.elements.plan_tier.value='paid_pro';f.elements.method.value='bank';f.elements.amount.value='100000';f.elements.currency.value='PKR';f.elements.transaction_id.value='REF-123';
  await f.onsubmit({preventDefault(){},currentTarget:f});
 })()`,sandbox);
 const register=requests.find(r=>r.url==='/api/register');assert.ok(register);
 assert.equal(JSON.parse(register.opts.body).name,'Test Customer');
 assert.ok(requests.find(r=>r.url==='/api/login'));
 const payment=requests.find(r=>r.url==='/api/billing/submit');assert.ok(payment);
 assert.equal(JSON.parse(payment.opts.body).plan_tier,'paid_pro');
 assert.equal(JSON.parse(payment.opts.body).amount,40);
 assert.equal(JSON.parse(payment.opts.body).method,'bank');
 assert.equal(JSON.parse(payment.opts.body).currency,'USD');
 assert.ok(requests.find(r=>r.url==='/api/jobs/7/proposal'));
 assert.ok(!requests.find(r=>r.url==='/api/jobs/7/ai-proposal'));

 // A delayed response from a previous account must not reveal old data after logout.
 let release;
 sandbox.fetch=async()=>new Promise(resolve=>{release=resolve});
 const pending=vm.runInContext("api('/api/private')",sandbox);
 vm.runInContext("S.nav='team';S.team=[{email:'owner-private@example.test'}];$('teamList').textContent='private team';resetSession();S.me={role:'member'};navigate('team');",sandbox);
 assert.equal(vm.runInContext('S.nav',sandbox),'overview');
 assert.equal(vm.runInContext('S.team.length',sandbox),0);
 assert.equal(node('teamList').textContent,'');
 release({ok:true,status:200,json:async()=>({private:'previous account'})});
 await assert.rejects(pending,/Session changed/);
 // A slow generated proposal must preserve text and price typed while it was running.
 vm.runInContext("S.me={subscription_active:false};S.selected={id:7};$('bidProposal').value='';$('bidAmount').value='10';",sandbox);
 const generated=vm.runInContext('makeProposal(false)',sandbox);
 node('bidProposal').value='My carefully edited proposal';node('bidAmount').value='25';
 release({ok:true,status:200,json:async()=>({proposal:'Server template',suggested_amount:15})});
 await generated;
 assert.equal(node('bidProposal').value,'My carefully edited proposal');assert.equal(node('bidAmount').value,'25');
 // Switching projects during draft saving must never open authorization for the old project.
 vm.runInContext("S.selected={id:7,demo:false};S.intent=null;",sandbox);
 const prepared=vm.runInContext('prepareBid()',sandbox);
 vm.runInContext('S.selected={id:8};S.editorRevision++',sandbox);
 release({ok:true,status:200,json:async()=>({ok:true})});
 await prepared;
 assert.equal(vm.runInContext('S.intent',sandbox),null);
 assert.equal(vm.runInContext('S.bidBusy',sandbox),false);
 vm.runInContext("S.me={connected:true,subscription_active:true};S.selected={id:8,type:'fixed',currency:''};syncBidActions();",sandbox);
 assert.equal(node('instantBidBtn').disabled,true);assert.match(node('modalNotice').textContent,/Scan again/);
 assert.match(vm.runInContext("usd({currency:'',type:'',budget_min:10})",sandbox),/Currency unknown/);
 vm.runInContext("S.selected.currency='USD';syncBidActions();",sandbox);assert.equal(node('instantBidBtn').disabled,false);
 vm.runInContext("S.selected.type='';syncBidActions();",sandbox);assert.equal(node('instantBidBtn').disabled,true);
 // A cookie replaced by another tab must invalidate both retained admin UI and old requests.
 let reloads=0;sandbox.window.location={reload(){reloads++}};
 vm.runInContext("S.me={id:1,role:'admin'};S.nav='team';S.team=[{email:'private-owner@example.test'}];$('teamList').textContent='owner-only receipts';S.jobs=[{id:99,title:'private owner job'}];",sandbox);
 sandbox.fetch=async()=>new Promise(resolve=>{release=resolve});
 const priorAccountRequest=vm.runInContext("api('/api/old-owner-data')",sandbox);
 const finishPrior=release;
 sandbox.fetch=async url=>({ok:true,status:200,json:async()=>url==='/api/me'?{user:{id:2,role:'member',name:'Current Member',email:'member@example.test'}}:url==='/api/jobs'?{jobs:[{id:100,title:'Do not process this mixed batch'}]}:{jobs:1}});
 await vm.runInContext('refresh()',sandbox);
 assert.equal(reloads,1);assert.equal(vm.runInContext('S.me',sandbox),null);
 assert.equal(vm.runInContext('S.nav',sandbox),'overview');assert.equal(vm.runInContext('S.jobs.length',sandbox),0);
 assert.equal(vm.runInContext('S.team.length',sandbox),0);assert.equal(node('teamList').textContent,'');
 assert.ok(node('shell').classList.contains('hidden'));
 finishPrior({ok:true,status:200,json:async()=>({private:'prior owner response'})});
 await assert.rejects(priorAccountRequest,/Session changed/);
 // Losing the admin role on the same account also clears privileged UI.
 vm.runInContext("S.me={id:1,role:'admin'};$('adminPaymentsList').textContent='private receipts';",sandbox);
 assert.throws(()=>vm.runInContext("applyAccount({user:{id:1,role:'member'}})",sandbox),/Session changed/);
 assert.equal(reloads,2);assert.equal(node('adminPaymentsList').textContent,'');
 // Saved-key tests must not silently ignore unsaved credentials or model/provider edits.
 vm.runInContext("S.me={id:2,role:'member',name:'Test Member',email:'member@example.test',subscription_active:true,preferred_ai:'auto',gemini_model:'saved-gemini-model',openai_model:'saved-openai-model'};S.ai_provider='Gemini configured';renderSettings();",sandbox);
 assert.match(node('aiKeysBanner').innerHTML,/Configured AI providers/);
 assert.ok(!node('aiKeysBanner').innerHTML.includes('Active AI Provider'));
 let testCalls=0;
 sandbox.fetch=async(url,opts)=>{testCalls++;assert.equal(url,'/api/ai-keys/test');assert.deepEqual(JSON.parse(opts.body),{provider:'both'});return {ok:true,status:200,json:async()=>({results:{gemini:{ok:false,status_code:404,model:'saved-gemini-model',message:'Model not found',available_models:['gemini-available-model','<img src=x onerror=alert(1)>'],model_discovery_complete:false,model_discovery_message:'<script>unsafe model discovery text</script>'},openai:{ok:false,status_code:401,model:'saved-openai-model',message:'Authentication rejected'}}})}};
 node('aiKeysForm:gemini_key').value='UNSAVED-SYNTHETIC-KEY';
 await vm.runInContext("$('testAiKeysBtn').onclick()",sandbox);
 assert.equal(testCalls,0);assert.match(node('aiTestResultBox').textContent,/Save Gemini & ChatGPT Keys first/);
 assert.equal(node('aiKeysForm:gemini_key').value,'UNSAVED-SYNTHETIC-KEY');
 node('aiKeysForm:gemini_key').value='';node('aiKeysForm:gemini_model').value='unsaved-model';
 await vm.runInContext("$('testAiKeysBtn').onclick()",sandbox);assert.equal(testCalls,0);
 node('aiKeysForm:gemini_model').value='saved-gemini-model';node('aiKeysForm:preferred_ai').value='openai';
 await vm.runInContext("$('testAiKeysBtn').onclick()",sandbox);assert.equal(testCalls,0);
 node('aiKeysForm:preferred_ai').value='auto';node('aiKeysForm:clear_gemini').checked=true;
 await vm.runInContext("$('testAiKeysBtn').onclick()",sandbox);assert.equal(testCalls,0);
 node('aiKeysForm:clear_gemini').checked=false;
 await vm.runInContext("$('testAiKeysBtn').onclick()",sandbox);assert.equal(testCalls,1);
 const diagnostic=node('aiTestResultBox').innerHTML;
 assert.ok(diagnostic.includes('&lt;img')&&!diagnostic.includes('<img'));
 assert.ok(diagnostic.includes('&lt;script&gt;')&&!diagnostic.includes('<script>'));
 assert.match(diagnostic,/Copy a listed model ID/);assert.match(diagnostic,/No model is selected or changed automatically/);
 assert.match(diagnostic,/Create a new secret API key in your provider dashboard/);
 assert.match(diagnostic,/list may be incomplete/);
 assert.equal(node('aiKeysForm:gemini_model').value,'saved-gemini-model');
 assert.equal(node('aiKeysForm:openai_model').value,'saved-openai-model');
 assert.equal(node('aiKeysForm:preferred_ai').value,'auto');
 // A catalog-listed model can still fail generation; preserve the selection and show safe reason codes.
 sandbox.fetch=async()=>({ok:true,status:200,json:async()=>({results:{gemini:{ok:false,status_code:404,model:'saved-gemini-model',message:'Generation request failed',available_models:['saved-gemini-model','<img src=x onerror=alert(1)>',...Array.from({length:12},(_,i)=>'extra-model-'+i)],selected_model_listed:true,provider_status:'NOT_FOUND<img src=x>',provider_reason:'MODEL_UNAVAILABLE<script>alert(1)</script>',failure_kind:'generation_access<svg onload=alert(1)>',response_format:'non_json<img src=x onerror=alert(1)>',model_discovery_complete:true},openai:{ok:false,status_code:401,model:'saved-openai-model',provider_status:'UNAUTHENTICATED',provider_reason:'INVALID_API_KEY',failure_kind:'authentication'}}})});
 await vm.runInContext("$('testAiKeysBtn').onclick()",sandbox);
 const listedDiagnostic=node('aiTestResultBox').innerHTML;
 assert.match(listedDiagnostic,/Provider code:/);assert.match(listedDiagnostic,/HTTP 404/);assert.match(listedDiagnostic,/HTTP 401/);assert.ok(listedDiagnostic.includes('body: non_json&lt;img'));
 assert.match(listedDiagnostic,/Catalog availability does not verify generation access/);
 assert.ok(!listedDiagnostic.includes('Copy a listed model ID'));
 assert.ok(listedDiagnostic.includes('&lt;img')&&!listedDiagnostic.includes('<img'));
 assert.ok(listedDiagnostic.includes('&lt;script&gt;')&&!listedDiagnostic.includes('<script>'));
 assert.ok(listedDiagnostic.includes('&lt;svg')&&!listedDiagnostic.includes('<svg'));
 assert.ok(listedDiagnostic.includes('<details')&&!listedDiagnostic.includes('<details open'));
 assert.ok(listedDiagnostic.includes('Showing the first 10 of 14 reported models'));
 assert.ok(!listedDiagnostic.includes('<li><code>extra-model-11'));
 assert.match(listedDiagnostic,/Create a new secret API key in your provider dashboard/);
 assert.equal(node('aiKeysForm:gemini_model').value,'saved-gemini-model');
 assert.equal(node('aiKeysForm:openai_model').value,'saved-openai-model');
 // Only the server's specific official-OpenAI authentication hint explains key IDs.
 assert.ok(!listedDiagnostic.includes('resembles an OpenAI key ID'));
 const hintedDiagnostic=vm.runInContext("aiDiagnosticsHTML({}, {ok:false,status_code:401,credential_hint:'possible_key_identifier'})",sandbox);
 assert.match(hintedDiagnostic,/This saved value resembles an OpenAI key ID/);
 assert.match(hintedDiagnostic,/An active key record is different from its secret value/);
 assert.match(hintedDiagnostic,/a key ID or masked value cannot authenticate/);
 assert.match(hintedDiagnostic,/https:\/\/platform.openai.com\/api-keys/);
 const unknownHint=vm.runInContext("aiDiagnosticsHTML({}, {ok:false,status_code:401,credential_hint:'<img src=x onerror=alert(1)>'})",sandbox);
 assert.ok(!unknownHint.includes('resembles an OpenAI key ID')&&!unknownHint.includes('<img'));
 const nonAuthenticationHint=vm.runInContext("aiDiagnosticsHTML({}, {ok:false,status_code:404,credential_hint:'possible_key_identifier'})",sandbox);
 assert.ok(!nonAuthenticationHint.includes('resembles an OpenAI key ID'));

 // A save must serialize mutations and preserve newer credentials/model edits.
 const aiForm=node('aiKeysForm');
 vm.runInContext("S.me={id:2,role:'member',name:'Test Member',email:'member@example.test',subscription_active:true,preferred_ai:'auto',gemini_model:'saved-gemini-model',openai_model:'saved-openai-model'};renderSettings();",sandbox);
 node('aiKeysForm:gemini_key').value='SYNTHETIC-KEY-A';
 let finishSave,aiSaveCalls=0;
 sandbox.fetch=async(url,opts)=>{aiSaveCalls++;assert.equal(url,'/api/ai-keys');assert.equal(JSON.parse(opts.body).gemini_key,'SYNTHETIC-KEY-A');return new Promise(resolve=>{finishSave=resolve})};
 const saving=vm.runInContext("$('aiKeysForm').onsubmit({preventDefault(){}})",sandbox);
 assert.equal(node('testAiKeysBtn').disabled,true);assert.equal(node('aiKeysForm:submit').disabled,true);
 vm.runInContext('renderStatus()',sandbox);
 assert.equal(node('testAiKeysBtn').disabled,true);assert.equal(node('aiKeysForm:submit').disabled,true);
 await vm.runInContext("$('testAiKeysBtn').onclick()",sandbox);
 await vm.runInContext("$('aiKeysForm').onsubmit({preventDefault(){}})",sandbox);
 assert.equal(aiSaveCalls,1);
 node('aiKeysForm:gemini_key').value='SYNTHETIC-KEY-B';node('aiKeysForm:openai_key').value='SYNTHETIC-NEW-OPENAI';
 node('aiKeysForm:gemini_model').value='newer-unsaved-model';node('aiKeysForm:preferred_ai').value='openai';
 for(const listener of aiForm.listeners.input)listener();
 finishSave({ok:true,status:200,json:async()=>({user:{id:2,role:'member',name:'Test Member',email:'member@example.test',subscription_active:true,preferred_ai:'auto',gemini_model:'saved-gemini-model',openai_model:'saved-openai-model',has_gemini_key:true,gemini_key_masked:'synthetic-mask-A'},ai_provider:'Gemini configured'})});
 await saving;
 assert.equal(node('aiKeysForm:gemini_key').value,'SYNTHETIC-KEY-B');assert.equal(node('aiKeysForm:openai_key').value,'SYNTHETIC-NEW-OPENAI');
 assert.equal(node('aiKeysForm:gemini_model').value,'newer-unsaved-model');assert.equal(node('aiKeysForm:preferred_ai').value,'openai');
 assert.match(node('aiTestResultBox').textContent,/Newer edits are still unsaved/);
 assert.equal(node('aiKeysForm:submit').disabled,false);assert.equal(node('testAiKeysBtn').disabled,false);
 assert.equal(vm.runInContext('hasUnsavedAISettings()',sandbox),true);
 // A slow test cannot be duplicated by polling and cannot overwrite later edits.
 node('aiKeysForm:gemini_key').value='';node('aiKeysForm:openai_key').value='';vm.runInContext('renderSettings()',sandbox);
 let finishTest,slowTestCalls=0;
 sandbox.fetch=async(url)=>{slowTestCalls++;assert.equal(url,'/api/ai-keys/test');return new Promise(resolve=>{finishTest=resolve})};
 const testing=vm.runInContext("$('testAiKeysBtn').onclick()",sandbox);
 vm.runInContext('renderStatus()',sandbox);
 assert.equal(node('testAiKeysBtn').disabled,true);assert.equal(node('aiKeysForm:submit').disabled,true);
 await vm.runInContext("$('testAiKeysBtn').onclick()",sandbox);
 await vm.runInContext("$('aiKeysForm').onsubmit({preventDefault(){}})",sandbox);
 assert.equal(slowTestCalls,1);
 node('aiKeysForm:openai_key').value='SYNTHETIC-EDIT-DURING-TEST';
 for(const listener of aiForm.listeners.input)listener();
 finishTest({ok:true,status:200,json:async()=>({results:{openai:{ok:true,message:'OLD-RESULT-MUST-NOT-RENDER'}}})});
 await testing;
 assert.match(node('aiTestResultBox').textContent,/old result was discarded/);
 assert.ok(!node('aiTestResultBox').innerHTML.includes('OLD-RESULT-MUST-NOT-RENDER'));
 assert.equal(node('aiKeysForm:openai_key').value,'SYNTHETIC-EDIT-DURING-TEST');
 assert.equal(node('testAiKeysBtn').disabled,false);
 // Changing and then reverting input still invalidates a result for the old revision.
 node('aiKeysForm:openai_key').value='';
 const revertedTest=vm.runInContext("$('testAiKeysBtn').onclick()",sandbox);
 node('aiKeysForm:gemini_model').value='temporary-edit';for(const listener of aiForm.listeners.change)listener();
 node('aiKeysForm:gemini_model').value='saved-gemini-model';
 finishTest({ok:true,status:200,json:async()=>({results:{gemini:{ok:true,message:'STALE-REVERTED-RESULT'}}})});
 await revertedTest;
 assert.match(node('aiTestResultBox').textContent,/old result was discarded/);
 assert.ok(!node('aiTestResultBox').innerHTML.includes('STALE-REVERTED-RESULT'));
 // Background saved-configuration changes also invalidate an otherwise unchanged form.
 const replacedTest=vm.runInContext("$('testAiKeysBtn').onclick()",sandbox);
 vm.runInContext("S.me.gemini_key_masked='synthetic-new-saved-mask'",sandbox);
 finishTest({ok:true,status:200,json:async()=>({results:{gemini:{ok:true,message:'STALE-SAVED-KEY-RESULT'}}})});
 await replacedTest;
 assert.match(node('aiTestResultBox').textContent,/old result was discarded/);
 assert.ok(!node('aiTestResultBox').innerHTML.includes('STALE-SAVED-KEY-RESULT'));
 // A recommendation is explicit selection only, never a hidden save or generation call.
 let recommendationCalls=0;
 sandbox.fetch=async(url)=>{recommendationCalls++;assert.equal(url,'/api/ai-keys/test');return {ok:true,status:200,json:async()=>({results:{gemini:{ok:false,status_code:404,model:'saved-gemini-model',recommended_model:'gemini-3.5-flash-lite'}}})}};
 await vm.runInContext("$('testAiKeysBtn').onclick()",sandbox);
 assert.match(node('aiTestResultBox').innerHTML,/Use gemini-3\.5-flash-lite/);
 assert.match(node('aiTestResultBox').innerHTML,/https:\/\/ai\.google\.dev\/gemini-api\/docs\/deprecations/);
 assert.equal(node('aiKeysForm:gemini_model').value,'saved-gemini-model');
 node('useRecommendedGeminiBtn').onclick();
 assert.equal(node('aiKeysForm:gemini_model').value,'gemini-3.5-flash-lite');
 assert.equal(node('aiKeysForm:gemini_key').value,'');assert.equal(node('aiKeysForm:openai_key').value,'');
 assert.equal(vm.runInContext('S.me.gemini_model',sandbox),'saved-gemini-model');
 assert.equal(recommendationCalls,1);assert.equal(vm.runInContext('hasUnsavedAISettings()',sandbox),true);
 vm.runInContext("S.me.gemini_key_masked='';",sandbox);
 const sharedRecommendation=vm.runInContext("aiDiagnosticsHTML({ok:false,status_code:404,recommended_model:'gemini-3.5-flash-lite'}, {})",sandbox);
 assert.ok(!sharedRecommendation.includes('id="useRecommendedGeminiBtn"'));
 assert.match(sharedRecommendation,/server operator must update GEMINI_MODEL/);
 vm.runInContext("S.me.gemini_key_masked='synthetic-personal-mask'",sandbox);
 const invalidRecommendation=vm.runInContext("aiDiagnosticsHTML({ok:false,status_code:404,recommended_model:'<img src=x>'}, {})",sandbox);
 assert.ok(!invalidRecommendation.includes('useRecommendedGeminiBtn')&&!invalidRecommendation.includes('<img'));
 const non404Recommendation=vm.runInContext("aiDiagnosticsHTML({ok:false,status_code:401,recommended_model:'gemini-3.5-flash-lite'}, {})",sandbox);
 assert.ok(!non404Recommendation.includes('useRecommendedGeminiBtn'));
 // Advanced rules preserve edits across slow saves and loads.
 vm.runInContext("S.me={id:3,role:'member',name:'Advanced User',email:'advanced@example.test',subscription_active:true,connected:true,preferred_ai:'auto',gemini_model:'gemini-3.5-flash-lite',openai_model:'gpt-4.1-mini',auto_requested:true,auto_chat_greeting:true};resetAdvancedState();renderSettings();",sandbox);
 const advancedForm=node('advancedSettingsForm');
 node('advancedSettingsForm:min_bid_interval_seconds').value='59';
 assert.throws(()=>vm.runInContext('readAdvancedSettings()',sandbox),/between 60 and 86400/);
 node('advancedSettingsForm:min_bid_interval_seconds').value='90';
 node('advancedSettingsForm:allowed_currencies').value='USD, GBP';
 const advancedSnapshot=JSON.parse(vm.runInContext('JSON.stringify(readAdvancedSettings())',sandbox));
 assert.deepEqual(advancedSnapshot.allowed_currencies,['USD','GBP']);
 assert.deepEqual(advancedSnapshot.search_fields,['title','description','skills']);
 const escapedRules=vm.runInContext("templateRulesHTML([{name:'<img src=x onerror=alert(1)>',keywords:['x\" autofocus onfocus=alert(1)'],template:'</textarea><svg onload=alert(1)>',enabled:false,match_mode:'all'}])",sandbox);
 assert.ok(!escapedRules.includes('<img')&&!escapedRules.includes('<svg'));
 assert.ok(escapedRules.includes('&lt;img')&&escapedRules.includes('&lt;/textarea&gt;'));
 assert.ok(escapedRules.includes('x&quot; autofocus'));
 let completeAdvanced,advancedSaves=0;
 sandbox.fetch=async(url,opts)=>{advancedSaves++;assert.equal(url,'/api/advanced-settings');assert.equal(JSON.parse(opts.body).min_bid_interval_seconds,90);return new Promise(resolve=>{completeAdvanced=resolve})};
 const advancedSaving=vm.runInContext("$('advancedSettingsForm').onsubmit({preventDefault(){}})",sandbox);
 assert.equal(node('saveAdvancedSettings').disabled,true);
 await vm.runInContext("$('advancedSettingsForm').onsubmit({preventDefault(){}})",sandbox);
 assert.equal(advancedSaves,1);
 node('advancedSettingsForm:min_client_rating').value='4.5';
 for(const listener of advancedForm.listeners.input)listener();
 completeAdvanced({ok:true,status:200,json:async()=>({settings:advancedSnapshot})});
 await advancedSaving;
 assert.equal(node('advancedSettingsForm:min_client_rating').value,'4.5');
 assert.match(node('advancedSettingsMessage').textContent,/newer edits are still unsaved/);
 vm.runInContext('A.settings=null;A.dirty=false;',sandbox);
 let completeAdvancedLoad;
 sandbox.fetch=async()=>new Promise(resolve=>{completeAdvancedLoad=resolve});
 const advancedLoading=vm.runInContext('loadAdvancedSettings()',sandbox);
 node('advancedSettingsForm:timezone').value='Asia/Karachi';for(const listener of advancedForm.listeners.change)listener();
 completeAdvancedLoad({ok:true,status:200,json:async()=>({settings:advancedSnapshot})});
 await advancedLoading;
 assert.equal(node('advancedSettingsForm:timezone').value,'Asia/Karachi');
 assert.equal(vm.runInContext('A.settings',sandbox),null);
 // Local backup files cannot carry keys, tokens, roles or subscription access.
 const backup={format:'bidpilot-settings',version:1,settings:{name:'Restored profile',skills:'Python',search_terms:'Python',auto_requested:true,auto_chat_greeting:true},advanced:advancedSnapshot};
 sandbox.backup=backup;
 assert.equal(vm.runInContext('validateSettingsBackup(backup).version',sandbox),1);
 sandbox.secretBackup={...backup,settings:{...backup.settings,openai_key:'NEVER-RESTORE-THIS-KEY'}};
 assert.throws(()=>vm.runInContext('validateSettingsBackup(secretBackup)',sandbox),/without credentials/);
 sandbox.roleBackup={...backup,settings:{...backup.settings,role:'admin'}};
 assert.throws(()=>vm.runInContext('validateSettingsBackup(roleBackup)',sandbox),/without credentials/);
 sandbox.invalidFormat={...backup,version:99};
 assert.throws(()=>vm.runInContext('validateSettingsBackup(invalidFormat)',sandbox),/supported BidPilot/);
 let importCalls=0,completeImport;
 sandbox.fetch=async(url,opts)=>{importCalls++;assert.equal(url,'/api/settings/import');const data=JSON.parse(opts.body);assert.equal(data.settings.auto_requested,false);assert.equal(data.settings.auto_chat_greeting,false);assert.ok(!opts.body.includes('UNSAVED-PRIVATE-KEY'));return new Promise(resolve=>{completeImport=resolve})};
 sandbox.restoreFile={name:'<img src=x>.json',size:512,text:async()=>JSON.stringify(backup)};
 await vm.runInContext('previewSettingsRestore(restoreFile)',sandbox);
 assert.equal(importCalls,0);assert.equal(node('importSettingsBtn').disabled,false);
 assert.match(node('settingsRestorePreview').textContent,/Automatic bidding and chat will be OFF/);
 assert.ok(node('settingsRestorePreview').textContent.includes('<img src=x>.json'));
 node('aiKeysForm:gemini_key').value='UNSAVED-PRIVATE-KEY';node('aiKeysForm:gemini_model').value='new-unsaved-ai-model';
 const importing=vm.runInContext("$('importSettingsBtn').onclick()",sandbox);
 assert.equal(node('aiKeysForm:submit').disabled,true);assert.equal(node('testAiKeysBtn').disabled,true);
 await vm.runInContext("$('aiKeysForm').onsubmit({preventDefault(){}})",sandbox);
 await vm.runInContext("$('settingsForm').onsubmit({preventDefault(){}})",sandbox);
 await vm.runInContext("$('testAiKeysBtn').onclick()",sandbox);
 assert.equal(importCalls,1);
 node('settingsForm:name').value='Newer typed name';node('advancedSettingsForm:timezone').value='Asia/Karachi';for(const listener of advancedForm.listeners.input)listener();
 completeImport({ok:true,status:200,json:async()=>({ok:true,user:{id:3,role:'member',name:'Restored profile',email:'advanced@example.test',subscription_active:true,auto_requested:false,auto_chat_greeting:false,gemini_model:'gemini-3.5-flash-lite',openai_model:'gpt-4.1-mini'},settings:advancedSnapshot,automation_paused:true})});
 await importing;
 assert.equal(importCalls,1);assert.equal(vm.runInContext('S.me.auto_requested',sandbox),false);assert.equal(vm.runInContext('S.me.auto_chat_greeting',sandbox),false);
 assert.equal(node('settingsForm:name').value,'Newer typed name');assert.equal(node('advancedSettingsForm:timezone').value,'Asia/Karachi');
 assert.equal(node('aiKeysForm:gemini_key').value,'UNSAVED-PRIVATE-KEY');assert.equal(node('aiKeysForm:gemini_model').value,'new-unsaved-ai-model');
 assert.equal(vm.runInContext('A.restore',sandbox),null);
 // Export uses a local JSON Blob download and rejects a response containing a secret field.
 let downloadedBlob,downloadClicks=0,revoked=0;
 class DownloadURL extends URL{};
 DownloadURL.createObjectURL=blob=>{downloadedBlob=blob;return 'blob:local-settings-backup'};
 DownloadURL.revokeObjectURL=url=>{assert.equal(url,'blob:local-settings-backup');revoked++};
 sandbox.URL=DownloadURL;sandbox.Blob=Blob;sandbox.setTimeout=callback=>{callback();return 1};
 sandbox.document.body={appendChild(){}};
 sandbox.document.createElement=tag=>{assert.equal(tag,'a');return {click(){assert.equal(this.href,'blob:local-settings-backup');assert.equal(this.download,'BidPilot_Settings_Backup.json');downloadClicks++},remove(){}}};
 sandbox.fetch=async(url,opts)=>{assert.equal(url,'/api/settings/export');assert.ok(!opts.method);return {ok:true,status:200,json:async()=>backup}};
 await vm.runInContext("$('exportSettingsBtn').onclick()",sandbox);
 assert.equal(downloadClicks,1);assert.equal(revoked,1);assert.deepEqual(JSON.parse(await downloadedBlob.text()),backup);
 sandbox.fetch=async()=>({ok:true,status:200,json:async()=>({...backup,token:'SECRET-SHOULD-NEVER-DOWNLOAD'})});
 await vm.runInContext("$('exportSettingsBtn').onclick()",sandbox);
 assert.equal(downloadClicks,1);assert.match(node('advancedSettingsMessage').textContent,/without credentials/);
 // File-size guards and account changes prevent an earlier user's restore/load applying later.
 sandbox.tooLarge={name:'backup.json',size:131073,text:async()=>{throw Error('Must not read oversized file')}};
 await vm.runInContext('previewSettingsRestore(tooLarge)',sandbox);assert.match(node('settingsRestorePreview').textContent,/128 KB/);
 vm.runInContext('A.settings=null;A.dirty=false;',sandbox);
 sandbox.fetch=async()=>new Promise(resolve=>{completeAdvancedLoad=resolve});
 const previousAdvancedLoad=vm.runInContext('loadAdvancedSettings()',sandbox);
 vm.runInContext("resetSession();S.me={id:4,role:'member',name:'Next User',subscription_active:true};",sandbox);
 completeAdvancedLoad({ok:true,status:200,json:async()=>({settings:{...advancedSnapshot,keyword_templates:[{name:'Previous account private template',keywords:['Python'],template:'Private text'}]}})});
 await previousAdvancedLoad;
 assert.equal(vm.runInContext('A.settings',sandbox),null);assert.equal(vm.runInContext('A.templates.length',sandbox),0);
 assert.equal(node('settingsRestorePreview').textContent,'');
 assert.ok(!node('keywordTemplateList').innerHTML.includes('Previous account private template'));
 sandbox.currentSeconds=Math.floor(Date.now()/1000);
 assert.match(vm.runInContext("freelancerPauseReason({api_pause_reason:'auth_rejected'})",sandbox),/reconnect/);
 assert.match(vm.runInContext("freelancerPauseReason({api_pause_reason:'rate_limited',api_cooldown_until:currentSeconds+300})",sandbox),/rate-limit pause/);
 assert.equal(vm.runInContext("freelancerPauseReason({api_pause_reason:'rate_limited',api_cooldown_until:currentSeconds-1})",sandbox),'');
 vm.runInContext("S.me={...S.me,connected:true,freelancer_name:'Test',api_pause_reason:'access_denied'};renderStatus();S.selected={id:8,type:'fixed',currency:'USD'};syncBidActions();",sandbox);
 assert.equal(node('topScan').disabled,true);assert.equal(node('runAutoNow').disabled,true);assert.equal(node('instantBidBtn').disabled,true);
 assert.match(node('automationSafetyStatus').textContent,/denied access/);
 vm.runInContext("S.me.api_pause_reason='';S.me.last_bid_attempt_at=currentSeconds;A.settings=null;renderAutomationSafety();",sandbox);
 assert.match(node('automationSafetyStatus').textContent,/Open advanced rules/);
 assert.ok(!node('automationSafetyStatus').textContent.includes('Next bid permitted'));

 console.log('Frontend security, account flow and asynchronous race checks passed');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', harness], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
