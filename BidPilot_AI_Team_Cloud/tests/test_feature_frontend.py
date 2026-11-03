"""Behavior checks for model discovery, proposal provenance and verification views."""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
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
const sandbox={URL,console,document:{getElementById:node,querySelectorAll:()=>[],addEventListener(){}},window:{scrollTo(){},location:{reload(){}}},navigator:{},setInterval(){},setTimeout(){},clearTimeout(){},fetch:async(url,opts)=>{requests.push({url,opts});return {ok:true,status:200,json:async()=>({})}}};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('app/static/app.js','utf8').replace('(async()=>{await initialize()})();',''),sandbox);
const run=source=>vm.runInContext(source,sandbox);
const response=data=>({ok:true,status:200,json:async()=>data});
'''


@unittest.skipUnless(shutil.which('node'), 'Node required for frontend behavior tests')
class FeatureFrontendTests(unittest.TestCase):
    def run_js(self, source):
        result = subprocess.run(['node', '-e', HARNESS + '\n(async()=>{\n' + source + '\n})().catch(error=>{console.error(error);process.exitCode=1});'], cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_blocked_projects_are_separated_and_reasons_escaped(self):
        self.run_js(r'''
run(`S.jobs=[
 {id:1,title:'Verified project',score:90,payment_verified:true,description:'Design'},
 {id:2,title:'Unknown project',score:90,payment_verified:null,description:'Design'},
 {id:3,title:'Unverified project',score:90,payment_verified:false,description:'Design'},
 {id:4,title:'Optional check failed',score:90,payment_verified:true,verification:{blocked:false,reasons:[],checks:{email_verified:false}}},
 {id:5,title:'<img src=x onerror=alert(1)>',score:90,payment_verified:false}
];$('scoreFilter').value='0';$('typeFilter').value='all';renderJobs();renderTopJobs();renderBlockedJobs();`);
assert.match(node('jobsTable').innerHTML,/Verified project/);
assert.doesNotMatch(node('jobsTable').innerHTML,/Unknown project|Unverified project/);
assert.doesNotMatch(node('topJobs').innerHTML,/Unknown project|Unverified project/);
assert.match(node('jobsTable').innerHTML,/Optional check failed/);
assert.doesNotMatch(node('blockedList').innerHTML,/Optional check failed/);
assert.match(node('blockedList').innerHTML,/Unknown project/);
assert.match(node('blockedList').innerHTML,/Client payment verification is unknown/);
assert.match(node('blockedList').innerHTML,/Client payment is unverified/);
assert.match(node('blockedList').innerHTML,/&lt;img/);
assert.doesNotMatch(node('blockedList').innerHTML,/<img/);
assert.equal(node('blockedCount').textContent,3);
run(`$('blockedSearch').value='img';renderBlockedJobs();`);
assert.match(node('blockedList').innerHTML,/&lt;img/);
assert.doesNotMatch(node('blockedList').innerHTML,/Unknown project/);
assert.equal(run('isBlockedJob({payment_verified:true})'),false);
assert.equal(run('isBlockedJob({payment_verified:true,email_verified:false})'),false);
assert.equal(run('isBlockedJob({payment_verified:"true"})'),true);
''')

    def test_blocked_projects_cannot_trigger_bid_requests(self):
        self.run_js(r'''
run(`S.me={id:1,subscription_active:true,connected:true};S.selected={id:7,payment_verified:null,type:'fixed',currency:'USD'};syncBidActions();`);
assert.equal(node('instantBidBtn').disabled,true);
assert.equal(node('prepareBid').disabled,true);
assert.match(node('modalNotice').textContent,/Bidding blocked/);
await run('instantBid()');await run('prepareBid()');assert.equal(requests.length,0);
run(`S.me={require_payment_verified:false};renderBasicSettings();`);
assert.equal(node('settingsForm:require_payment_verified').checked,true);
''')

    def test_model_refresh_preserves_typed_selection_and_filters_unsafe_ids(self):
        self.run_js(r'''
let count=0;
sandbox.fetch=async(url)=>{requests.push({url});count++;return response({user_id:5,refresh_interval_seconds:21600,providers:{gemini:{configured:true,selected_model:'gemini-saved',status:'complete',models:[{id:'gemini-new-future'},{id:'<img onerror=alert(1)>'}],message:'Catalog available',refresh_after:new Date(Date.now()+21600000).toISOString()},openai:{configured:true,selected_model:'gpt-saved',status:'complete',models:[{id:'gpt-next'},{id:'ft:gpt-4.1-mini:org:custom:id'}]}}})};
run(`S.me={id:5};$('aiKeysForm').elements.gemini_model.value='my-unsaved-model';$('aiKeysForm').elements.openai_model.value='my-other-unsaved-model';`);
await run('loadModelCatalog()');
assert.equal(node('aiKeysForm:gemini_model').value,'my-unsaved-model');
assert.equal(node('aiKeysForm:openai_model').value,'my-other-unsaved-model');
assert.match(node('geminiModels').innerHTML,/gemini-new-future/);
assert.match(node('geminiModels').innerHTML,/gemini-saved/);
assert.doesNotMatch(node('geminiModels').innerHTML,/<img/);
assert.match(node('openaiModels').innerHTML,/ft:gpt-4.1-mini/);
assert.match(node('modelCatalogStatus').innerHTML,/does not verify generation access/);
await run('loadModelCatalog()');assert.equal(count,1);
await run('loadModelCatalog(true)');assert.equal(count,2);
assert.match(requests[1].url,/refresh=true&expected_user_id=5/);
assert.equal(node('aiKeysForm:gemini_model').value,'my-unsaved-model');
''')

    def test_old_account_model_response_is_discarded(self):
        self.run_js(r'''
let release;sandbox.fetch=async()=>new Promise(resolve=>{release=resolve});
run('S.me={id:5}');const pending=run('loadModelCatalog(true)');
run(`S.epoch++;S.me={id:6};C.revision++;C.data=null;$('geminiModels').innerHTML='new account';`);
release(response({user_id:5,providers:{gemini:{models:[{id:'private-old-model'}]}}}));await pending;
assert.equal(node('geminiModels').innerHTML,'new account');
assert.equal(run('C.data'),null);
''')

    def test_ai_result_reports_provider_and_fallback_keeps_existing_proposal(self):
        self.run_js(r'''
let payload={proposal:'A generated proposal from the requested AI model.',ai_used:true,provider:'openai',model:'gpt-next',status:'generated'};
sandbox.fetch=async()=>response(payload);
run(`S.me={id:5,subscription_active:true};S.selected={id:7,payment_verified:true};$('bidProposal').value='';`);
await run('makeProposal(true)');
assert.equal(node('bidProposal').value,payload.proposal);
assert.match(node('proposalAiStatus').textContent,/Generated with OpenAI · gpt-next/);
payload={proposal:'Local replacement template.',ai_used:false,source:'local_template',status:'provider_cooldown',message:'The provider is temporarily cooling down.',retry_after:30,suggested_amount:150,suggested_days:3,suggested_milestone:100};
run(`$('bidProposal').value='My carefully written original proposal';$('bidAmount').value='275';$('bidDays').value='10';$('bidMilestone').value='50';`);
await run('makeProposal(true)');
assert.equal(node('bidProposal').value,'My carefully written original proposal');
assert.equal(node('bidAmount').value,'275');assert.equal(node('bidDays').value,'10');assert.equal(node('bidMilestone').value,'50');
assert.match(node('proposalAiStatus').textContent,/cooling down/);
assert.match(node('proposalAiStatus').textContent,/Retry after 30 seconds/);
assert.match(node('proposalAiStatus').textContent,/Your current proposal was kept/);
''')

    def test_slow_ai_keeps_edits_and_duplicate_clicks_do_not_send_requests(self):
        self.run_js(r'''
let release,count=0;sandbox.fetch=async()=>{count++;return new Promise(resolve=>{release=resolve})};
run(`S.me={id:5,subscription_active:true};S.selected={id:7,payment_verified:true};$('bidProposal').value='';`);
const pending=run('makeProposal(true)');await run('makeProposal(true)');assert.equal(count,1);
node('bidProposal').value='New text typed while AI was writing';run('S.editorRevision++');
release(response({proposal:'Generated replacement',ai_used:true,provider:'gemini',model:'gemini-next'}));await pending;
assert.equal(node('bidProposal').value,'New text typed while AI was writing');
assert.match(node('proposalAiStatus').textContent,/Your newer edits were kept/);
assert.doesNotMatch(node('proposalAiStatus').textContent,/Contacting|Preparing/);
assert.equal(run('S.proposalBusy'),false);
''')

    def test_payment_cards_escape_details_and_show_distinct_review_statuses(self):
        self.run_js(r'''
sandbox.fetch=async()=>response({user_id:5,plan_tier:'paid_pro',subscription_status:'active',subscription_active:true,payment_methods:{channels:[{id:'bank',label:'Bank',details:'<img src=x onerror=alert(1)>',account_title:'Test <title>'},{id:'stripe',label:'Card',details:'javascript:alert(1)'}]},payments:[{plan_tier:'paid_pro',method:'bank',currency:'USD',amount:40,transaction_id:'<script>evil</script>',status:'rejected',created:100},{plan_tier:'paid_starter',method:'jazzcash',currency:'USD',amount:10,transaction_id:'OK',status:'approved',created:101}]});
run(`S.me={id:5,name:'Test account',email:'test@example.test'};`);await run('loadBilling()');
assert.match(node('paymentMethodsBox').innerHTML,/payment-method-card/);
assert.match(node('paymentMethodsBox').innerHTML,/&lt;img/);
assert.doesNotMatch(node('paymentMethodsBox').innerHTML,/<img|javascript:/);
assert.match(node('myPaymentsList').innerHTML,/status-rejected/);
assert.match(node('myPaymentsList').innerHTML,/status-approved/);
assert.match(node('myPaymentsList').innerHTML,/&lt;script/);
assert.doesNotMatch(node('myPaymentsList').innerHTML,/<script>/);
assert.match(node('myPlanBanner').innerHTML,/\$10\/month/);
assert.match(node('myPlanBanner').innerHTML,/\$40\/month/);
''')
