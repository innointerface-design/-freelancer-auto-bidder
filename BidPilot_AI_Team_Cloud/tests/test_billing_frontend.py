"""Billing UI keeps identity, payment settings and delayed writes scoped to one account."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which('node'), reason='Node is required for frontend checks')
def test_payment_settings_and_receipts_preserve_account_and_edit_boundaries():
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(),events={};let handler,requests=[],reloads=0;
function node(id){
 if(!nodes.has(id)){
  const classes=new Set();const el={value:'',checked:false,disabled:false,innerHTML:'',textContent:'',dataset:{},listeners:{},
   classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle:(x,on)=>on?classes.add(x):classes.delete(x)},
   addEventListener(type,fn){(this.listeners[type]??=[]).push(fn)},querySelector:q=>q==='button[type="submit"]'?node(id+':submit'):null,
   toggleAttribute(name,on){if(name==='disabled')this.disabled=on},removeAttribute(){},focus(){},
   reset(){for(const [key,child]of nodes)if(key.startsWith(id+':')){child.value='';child.checked=false}},
  };el.elements=new Proxy({}, {get:(_,key)=>node(id+':'+String(key))});nodes.set(id,el);
 }return nodes.get(id);
}
const sandbox={URL,console,document:{getElementById:node,querySelectorAll:q=>q==='form'?[...nodes].filter(([key])=>key.endsWith('Form')).map(([,value])=>value):[],addEventListener:(type,fn)=>events[type]=fn},
 window:{scrollTo(){},location:{reload(){reloads++}}},navigator:{},setInterval(){},setTimeout(){},clearTimeout(){},
 fetch:async(url,options)=>{requests.push({url,options,body:options.body?JSON.parse(options.body):null});return handler(url,options)}};
const reply=(data,status=200)=>({ok:status<400,status,json:async()=>data});
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return{promise,resolve}}
vm.createContext(sandbox);vm.runInContext(fs.readFileSync('app/static/app.js','utf8').replace('(async()=>{await initialize()})();',''),sandbox);
const run=code=>vm.runInContext(code,sandbox);
const user={id:1,role:'admin',name:'Registered Full Name',email:'owner@example.test',plan_tier:'paid_starter',subscription_status:'active',subscription_active:true,auto_requested:false,auto_admin_allowed:true,connected:false};
const settings={jazzcash:{enabled:true,number:'03128315518',account_title:'Owner'},easypaisa:{enabled:true,number:'03128315518',account_title:''},nayapay:{enabled:true,iban:'PK78NAYA1234503128315518',account_title:''},meezan:{enabled:true,iban:'PK81MEZN0029040112662811',account_title:''},bank:{enabled:false,details:''},stripe:{enabled:false,url:''},whatsapp:''};
const methods={channels:[{id:'jazzcash',label:'JazzCash',details:'03128315518',account_title:'Owner'},{id:'easypaisa',label:'Easypaisa',details:'03128315518',account_title:''},{id:'nayapay',label:'NayaPay',details:'PK78NAYA1234503128315518',account_title:''},{id:'meezan',label:'Meezan',details:'PK81MEZN0029040112662811',account_title:''},{id:'stripe',label:'Card',details:'javascript:alert(1)',account_title:''}],whatsapp:''};
const bill={user_id:1,plan_tier:'paid_starter',subscription_status:'active',subscription_active:true,subscription_started_at:1800000000,subscription_expires:1802678400,payments:[],payment_methods:methods};
const select=node('paymentForm:method');select.options=['jazzcash','easypaisa','nayapay','meezan','bank','stripe'].map(value=>({value,disabled:false}));select.value='jazzcash';
sandbox.user=user;sandbox.settings=settings;sandbox.methods=methods;sandbox.bill=bill;
(async()=>{
 run('S.me={...user};S.csrf="synthetic-csrf";S.platform_auto=true');
 handler=async()=>reply(bill);await run('loadBilling()');
 assert.match(requests[0].url,/expected_user_id=1/);
 assert.match(node('paymentMethodsBox').innerHTML,/PK78NAYA1234503128315518/);assert.match(node('paymentMethodsBox').innerHTML,/PK81MEZN0029040112662811/);
 assert.ok(!node('paymentMethodsBox').innerHTML.includes('javascript:'));
 assert.equal(select.options.find(x=>x.value==='stripe').disabled,true);assert.equal(select.options.find(x=>x.value==='bank').disabled,true);
 assert.match(node('paymentIdentity').textContent,/Registered Full Name.*owner@example.test/);assert.match(node('paymentIdentity').textContent,/Never send your password/);
 assert.match(node('myPlanBanner').innerHTML,/Activated:/);assert.match(node('myPlanBanner').innerHTML,/Access expires:/);assert.match(node('myPlanBanner').innerHTML,/calendar month/);
 handler=async()=>reply({...bill,subscription_started_at:0,billing_period_started:1800000000});await run('loadBilling()');assert.match(node('myPlanBanner').innerHTML,/Activated: Not activated/);
 // Labels and bank details are inert, even if a malicious admin puts markup in them.
 handler=async()=>reply({...bill,payment_methods:{channels:[{id:'nayapay',label:'<img src=x>',details:'<svg onload=bad()>',account_title:'<script>bad()</script>'}]}});
 await run('loadBilling()');assert.ok(!node('paymentMethodsBox').innerHTML.includes('<svg'));assert.match(node('paymentMethodsBox').innerHTML,/&lt;svg/);
 // A pending billing load cannot replace another local account's state.
 const old=deferred();handler=async()=>old.promise;const stale=run('loadBilling()');run('S.me={...user,id:2,name:"New account"}');node('paymentIdentity').textContent='NEW ACCOUNT';old.resolve(reply(bill));await stale;assert.equal(node('paymentIdentity').textContent,'NEW ACCOUNT');
 run('S.me={...user};S.payment_methods=methods');
 // Receipt submission sends only the current account reference, not passwords,
 // fixes the plan price server-side and prevents duplicate pending requests.
 const receipt=deferred();let receiptCalls=0;
 handler=async(url)=>url==='/api/billing/submit'?(receiptCalls++,receipt.promise):reply(bill);
 const f=node('paymentForm');f.elements.plan_tier.value='paid_pro';f.elements.method.value='nayapay';f.elements.amount.value='999';f.elements.transaction_id.value='REFERENCE-1';f.elements.sender_phone.value='03120000000';f.elements.notes.value='Original note';
 const pending=f.onsubmit({preventDefault(){},currentTarget:f});await f.onsubmit({preventDefault(){},currentTarget:f});assert.equal(receiptCalls,1);
 const sent=requests.find(x=>x.url==='/api/billing/submit').body;assert.equal(sent.amount,40);assert.equal(sent.expected_user_id,1);assert.equal(sent.method,'nayapay');assert.ok(!('password'in sent));
 f.elements.transaction_id.value='NEW-REFERENCE';f.elements.notes.value='A newer note';receipt.resolve(reply({ok:true}));await pending;assert.equal(f.elements.transaction_id.value,'NEW-REFERENCE');assert.equal(f.elements.notes.value,'A newer note');
 // Admin destination loading is guarded and does not overwrite newer edits.
 const load=deferred();handler=async()=>load.promise;run('P.settings=null;P.dirty=false');const loading=run('loadPaymentSettings()');
 const form=node('paymentSettingsForm');form.elements.jazzcash_number.value='NEW-DRAFT';form.listeners.input[0]();load.resolve(reply({settings,warnings:[]}));await loading;assert.equal(form.elements.jazzcash_number.value,'NEW-DRAFT');assert.match(node('paymentSettingsMessage').textContent,/newer edits/);
 handler=async()=>reply({settings,warnings:[]});await run('loadPaymentSettings(true)');assert.equal(form.elements.meezan_iban.value,settings.meezan.iban);
 // Saving uses a snapshot while retaining edits made during that request.
 let refreshed=0;sandbox.refreshed=0;run('refresh=async()=>{refreshed++}');const save=deferred();let saves=0;
 handler=async(url)=>{assert.equal(url,'/api/admin/payment-settings');saves++;return save.promise};
 const saving=form.onsubmit({preventDefault(){}});await form.onsubmit({preventDefault(){}});assert.equal(saves,1);
 const payload=requests.at(-1).body;assert.equal(payload.expected_user_id,1);assert.equal(payload.settings.meezan.iban,settings.meezan.iban);assert.ok(!JSON.stringify(payload).includes('password'));
 form.elements.jazzcash_number.value='NEWER-NUMBER';form.listeners.input[0]();save.resolve(reply({settings,warnings:[]}));await saving;assert.equal(form.elements.jazzcash_number.value,'NEWER-NUMBER');assert.equal(run('P.dirty'),true);assert.match(node('paymentSettingsMessage').textContent,/newer edits/);
 // Member views cannot issue admin payment-settings writes or loads.
 run('S.me={...user,role:"member"}');const count=requests.length;await form.onsubmit({preventDefault(){}});await run('loadPaymentSettings(true)');assert.equal(requests.length,count);
 // Administrative decisions are single-flight and include the expected account.
 run('S.me={...user};loadTeam=async()=>{}');const decide=deferred();handler=async()=>decide.promise;
 const target={dataset:{paydecide:'12',act:'approve'},disabled:false};const click={target:{closest(){return target}}};
 const deciding=events.click(click);await events.click(click);assert.equal(requests.filter(x=>x.url==='/api/admin/payments/12/decide').length,1);assert.deepEqual(requests.at(-1).body,{action:'approve',expected_user_id:1});decide.resolve(reply({ok:true}));await deciding;
 // A changed-cookie 409 clears sensitive admin drafts before a clean reload.
 handler=async()=>reply({detail:'Account changed. Reload before continuing.'},409);await run('loadBilling()');assert.equal(reloads,1);assert.equal(run('S.me'),null);assert.equal(node('paymentSettingsMessage').textContent,'');assert.equal(form.elements.meezan_iban.value,'');
 console.log('Billing destinations, identity, pending writes and account boundaries passed');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_payment_forms_do_not_request_customer_passwords_and_have_unique_ids():
    import re
    html = (ROOT / 'app/static/index.html').read_text()
    billing = html.split('<section id="view-billing"', 1)[1].split('<section id="view-team"', 1)[0]
    assert 'type="password"' not in billing
    assert 'name="password"' not in billing
    assert 'Never share your password, PIN or OTP' in billing
    for channel in ('jazzcash', 'easypaisa', 'nayapay', 'meezan', 'bank', 'stripe'):
        assert f'value="{channel}"' in billing
    identifiers = re.findall(r'\bid="([^"]+)"', html)
    assert len(identifiers) == len(set(identifiers))
    assert 'id="paymentSettingsForm"' in html
    assert '/static/security.js' in html
