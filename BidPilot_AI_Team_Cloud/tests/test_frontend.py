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
  const el={value:'',checked:false,disabled:false,innerHTML:'',textContent:'',dataset:{},
   classList:{add:v=>classes.add(v),remove:v=>classes.delete(v),contains:v=>classes.has(v),toggle:(v,on)=>{if(on)classes.add(v);else classes.delete(v)}},
   addEventListener(){},reset(){},removeAttribute(){}};
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
 console.log('Frontend security, account flow and asynchronous race checks passed');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', harness], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
