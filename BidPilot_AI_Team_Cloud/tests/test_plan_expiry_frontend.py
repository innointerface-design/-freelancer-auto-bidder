"""Administrator expiry is distinct from disabling sign-in and preserves drafts."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(),events={},requests=[],confirms=[];let handler,confirmed=true,reloaded=0;
const expiryButtons=[];
function node(id){
 if(!nodes.has(id)){
  const classes=new Set(),el={value:'',checked:false,disabled:false,innerHTML:'',textContent:'',dataset:{},listeners:{},
   classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle:(x,on)=>on?classes.add(x):classes.delete(x)},
   addEventListener(type,fn){(this.listeners[type]??=[]).push(fn)},querySelector:q=>q==='button[type="submit"]'?node(id+':submit'):null,
   toggleAttribute(name,on){if(name==='disabled')this.disabled=on},removeAttribute(){},focus(){},reset(){},
  };el.elements=new Proxy({}, {get:(_,key)=>node(id+':'+String(key))});nodes.set(id,el);
 }return nodes.get(id);
}
const reply=(body,status=200)=>({ok:status<400,status,json:async()=>body,headers:{get:()=>null}});
const sandbox={URL,console,document:{getElementById:node,querySelectorAll:selector=>selector==='[data-expire-plan]'?expiryButtons:[],addEventListener:(name,fn)=>events[name]=fn},
 window:{scrollTo(){},location:{reload(){reloaded++}}},navigator:{},setInterval(){},setTimeout(){},clearTimeout(){},
 confirm(message){confirms.push(message);return confirmed},
 fetch:async(url,options)=>{requests.push({url,body:options.body?JSON.parse(options.body):null});return handler(url,options)}};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync('app/static/app.js','utf8').replace('(async()=>{await initialize()})();',''),sandbox);
const run=code=>vm.runInContext(code,sandbox);
const click=target=>events.click({target:{closest:()=>target}});
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve}}
const admin={id:1,role:'admin',active:true,name:'Owner',email:'owner@example.test',plan_tier:'free_team',subscription_status:'active',subscription_active:true};
const member={id:2,role:'member',active:true,name:'Customer <img src=x>',email:'customer@example.test',plan_tier:'paid_pro',subscription_status:'active',subscription_active:true,subscription_expires:2000000000,subscription_started_at:1900000000};
sandbox.admin=admin;sandbox.member=member;
run('S.me={...admin};S.team=[{...member}];S.csrf="test-csrf";loadPaymentSettings=async()=>{}');
'''


def run_browser(body):
    result = subprocess.run(['node', '-e', HARNESS + body], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not shutil.which('node'), reason='Node is needed for frontend checks')
def test_expiry_rendering_confirmation_snapshot_and_single_flight():
    run_browser(r'''
(async()=>{
 const expired={...member,id:3,subscription_status:'expired',subscription_active:false};
 const disabled={...member,id:4,active:false,subscription_active:false};
 const inactive={...member,id:5,subscription_status:'inactive',subscription_active:false};
 const founder={...admin,id:6,name:'Founder'};
 handler=async url=>reply(url==='/api/team'?{members:[admin,member,expired,disabled,inactive,founder]}:{payments:[]});
 await run('loadTeam()');const html=node('teamList').innerHTML;
 assert.ok(html.includes('data-expire-plan="2"'));assert.ok(html.includes('data-expire-plan="4"'));
 for(const id of [1,3,5,6])assert.ok(!html.includes(`data-expire-plan="${id}"`));
 assert.ok(html.includes('Disable login'));assert.ok(html.includes('Enable login'));
 assert.ok(html.includes('Login: disabled'));assert.ok(html.includes('login status unchanged'));
 assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('<img'));

 let loads=0; sandbox.loaded=()=>{loads++};run('loadTeam=async()=>loaded()');
 const button={dataset:{expirePlan:'2'},disabled:false};expiryButtons.push(button);
 confirmed=false;await click(button);assert.equal(requests.filter(r=>r.url.endsWith('/expire-plan')).length,0);
 assert.match(confirms.at(-1),/customer@example.test/);assert.match(confirms.at(-1),/Pro/);
 assert.match(confirms.at(-1),/login status stays unchanged \(enabled\)/);
 assert.match(confirms.at(-1),/immediately/);assert.equal(button.disabled,false);

 confirmed=true;const pending=deferred();handler=async()=>pending.promise;
 const first=click(button);await click(button);assert.equal(button.disabled,true);
 const sent=requests.filter(r=>r.url==='/api/team/2/expire-plan');assert.equal(sent.length,1);
 assert.deepEqual(sent[0].body,{expected_user_id:1,expected_plan_tier:'paid_pro',expected_subscription_status:'active',expected_subscription_expires:2000000000});
 pending.resolve(reply({ok:true,member:{...member,subscription_status:'expired'}}));await first;
 assert.equal(loads,1);assert.equal(button.disabled,false);assert.match(node('toast').textContent,/Login status is unchanged/);

 // A stale renewal response requests a fresh team view; it does not retry expiry.
 handler=async()=>reply({detail:'Plan changed. Reload and review.'},409);await click(button);
 assert.equal(loads,2);assert.match(node('toast').textContent,/Plan changed/);
 assert.equal(requests.filter(r=>r.url==='/api/team/2/expire-plan').length,2);
 const before=requests.length;
 await click({dataset:{expirePlan:'999'},disabled:false});
 await click({dataset:{expirePlan:'6'},disabled:false});
 run('S.me={id:9,role:"member"}');await click(button);assert.equal(requests.length,before);
 console.log('Expiry rendering, target confirmation, snapshot and single-flight checks passed');
})().catch(error=>{console.error(error);process.exitCode=1});
''')


@pytest.mark.skipif(not shutil.which('node'), reason='Node is needed for frontend checks')
def test_own_expiry_preserves_drafts_and_old_session_results_are_discarded():
    run_browser(r'''
(async()=>{
 const own={...member,id:1,role:'admin',name:'Owner',email:'owner@example.test'};
 sandbox.own=own;run('S.me={...own};S.team=[{...own},{...member}];loadTeam=async()=>{}');
 const button={dataset:{expirePlan:'1'},disabled:false};expiryButtons.push(button);
 node('settingsForm').elements.description.value='Newer unsaved bio';node('aiKeysForm').elements.gemini_key.value='Unsent-key-draft';
 node('paymentSettingsForm').elements.meezan_iban.value='Newer receiving draft';
 handler=async url=>reply(url==='/api/me'?{user:{...own,subscription_status:'expired',subscription_active:false,auto_requested:false,auto_chat_greeting:false}}:{ok:true});
 await click(button);
 assert.match(confirms.at(-1),/your own paid plan/);assert.match(confirms.at(-1),/login stays available/);
 assert.equal(run('S.me.subscription_status'),'expired');assert.equal(node('runAutoNow').disabled,true);
 assert.equal(node('settingsForm').elements.description.value,'Newer unsaved bio');
 assert.equal(node('aiKeysForm').elements.gemini_key.value,'Unsent-key-draft');
 assert.equal(node('paymentSettingsForm').elements.meezan_iban.value,'Newer receiving draft');

 // A confirmation invalidated by an account change never sends the action.
 run('S.me={...admin};S.team=[{...member}]');
 sandbox.confirm=()=>{run('S.epoch++;S.me={id:9,role:"admin"}');return true};
 const before=requests.length;await click({dataset:{expirePlan:'2'},disabled:false});assert.equal(requests.length,before);

 // Logout while a mutation is pending cannot restore the old account or toast.
 sandbox.confirm=()=>true;run('S.me={...admin};S.team=[{...member}]');
 const pending=deferred();handler=async()=>pending.promise;
 const staleButton={dataset:{expirePlan:'2'},disabled:false};const stale=click(staleButton);
 run('resetSession();S.me={id:9,role:"member"}');node('toast').textContent='New session';
 pending.resolve(reply({ok:true,member:{...member,subscription_status:'expired'}}));await stale;
 assert.equal(run('S.me.id'),9);assert.equal(node('toast').textContent,'New session');assert.equal(run('P.expiryBusy'),false);
 console.log('Own expiry preserves form drafts and ignores stale session/confirmation results');
})().catch(error=>{console.error(error);process.exitCode=1});
''')
