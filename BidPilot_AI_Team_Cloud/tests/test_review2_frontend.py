"""Browser behavior for large settings backups and pending administrator actions."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(),events={},requests=[],buttons=[];let handler,confirmed=true;
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
const sandbox={URL,console,document:{getElementById:node,querySelectorAll:selector=>selector.includes('[data-toggleplan]')?buttons:[],addEventListener:(name,fn)=>events[name]=fn},
 window:{scrollTo(){},location:{reload(){}}},navigator:{},setInterval(){},setTimeout(){},clearTimeout(){},confirm:()=>confirmed,
 fetch:async(url,options)=>{requests.push({url,body:options.body?JSON.parse(options.body):null});return handler(url,options)}};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync('app/static/app.js','utf8').replace('(async()=>{await initialize()})();',''),sandbox);
const run=code=>vm.runInContext(code,sandbox);
const click=target=>events.click({target:{closest:()=>target}});
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve}}
const admin={id:1,role:'admin',active:true,name:'Owner',email:'owner@example.test',plan_tier:'free_team',subscription_status:'active',subscription_active:true};
const member={id:2,role:'member',active:true,name:'Customer',email:'customer@example.test',plan_tier:'paid_pro',subscription_status:'active',subscription_active:true,subscription_expires:2000000000};
sandbox.admin=admin;sandbox.member=member;
run('S.me={...admin};S.team=[{...member}];S.csrf="test-csrf";loadPaymentSettings=async()=>{}');
process.on('beforeExit',()=>assert.equal(sandbox.completed,true,'Browser scenario did not finish'));
'''


def browser_check(body):
    result = subprocess.run(['node', '-e', HARNESS + body], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not shutil.which('node'), reason='Node is needed for frontend checks')
def test_large_unicode_backup_preview_and_size_rejection():
    browser_check(r'''
(async()=>{
 const backup={format:'bidpilot-settings',version:1,settings:{},advanced:{keyword_templates:Array.from({length:10},(_,i)=>({
  name:'Rule '+i,keywords:Array.from({length:30},(_,j)=>'😀'.repeat(95)+j),match_mode:'any',template:'😀'.repeat(2800),enabled:true
 }))}};
 const content=JSON.stringify(backup,null,2),size=Buffer.byteLength(content);
 assert.ok(size>128*1024&&size<512*1024);sandbox.file={name:'My settings.json',size,text:async()=>content};
 await run('previewSettingsRestore(file)');
 assert.equal(run('A.restore.advanced.keyword_templates.length'),10);
 assert.equal(node('importSettingsBtn').disabled,false);assert.match(node('settingsRestorePreview').textContent,/Ready to restore/);
 let reads=0;sandbox.file={name:'Too large.json',size:512*1024+1,text:async()=>{reads++;return content}};
 await run('previewSettingsRestore(file)');
 assert.equal(reads,0);assert.equal(run('A.restore'),null);assert.equal(node('importSettingsBtn').disabled,true);
 assert.match(node('settingsRestorePreview').textContent,/512 KiB/);
 sandbox.completed=true;
})().catch(error=>{console.error(error);process.exitCode=1});
''')


@pytest.mark.skipif(not shutil.which('node'), reason='Node is needed for frontend checks')
def test_admin_actions_do_not_overlap_or_overwrite_a_new_session():
    browser_check(r'''
(async()=>{
 let loads=0;sandbox.loaded=()=>{loads++};run('loadTeam=async()=>loaded()');
 const plan={dataset:{toggleplan:'2',tier:'paid_starter'},disabled:false};
 const permission={dataset:{permission:'2',value:'0'},disabled:false};
 const expiry={dataset:{expirePlan:'2'},disabled:false};
 const payment={dataset:{paydecide:'3',act:'approve'},disabled:false};buttons.push(plan,permission,expiry,payment);
 const pending=deferred();handler=async()=>pending.promise;
 const first=click(plan);await click(plan);await click(permission);await click(expiry);await click(payment);
 assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/team/2/plan');
 assert.ok(buttons.every(button=>button.disabled));
 pending.resolve(reply({ok:true,member:{...member,plan_tier:'paid_starter'}}));await first;
 assert.equal(loads,1);assert.ok(buttons.every(button=>!button.disabled));
 handler=async()=>reply({detail:'Temporary failure'},503);await click(permission);
 assert.equal(requests.length,2);assert.ok(buttons.every(button=>!button.disabled));
 // The reverse direction is guarded too: a pending expiry cannot be followed
 // by a plan grant that silently restores the plan being expired.
 const expiring=deferred();handler=async()=>expiring.promise;
 const exp=click(expiry);await click(plan);await click(permission);
 assert.equal(requests.length,3);assert.equal(requests.at(-1).url,'/api/team/2/expire-plan');
 expiring.resolve(reply({ok:true}));await exp;
 // A delayed previous-session mutation cannot refresh the new team's screen.
 const old=deferred();handler=async()=>old.promise;const stale=click(plan);
 run('resetSession();S.me={id:9,role:"member"}');node('toast').textContent='New session';
 const before=loads;old.resolve(reply({ok:true}));await stale;
 assert.equal(loads,before);assert.equal(node('toast').textContent,'New session');assert.equal(run('P.memberBusy'),false);
 const count=requests.length;await click(plan);assert.equal(requests.length,count);
 sandbox.completed=true;
})().catch(error=>{console.error(error);process.exitCode=1});
''')


@pytest.mark.skipif(not shutil.which('node'), reason='Node is needed for frontend checks')
def test_own_plan_change_refreshes_access_without_overwriting_settings_drafts():
    browser_check(r'''
(async()=>{
 run('loadTeam=async()=>{}');
 node('settingsForm').elements.description.value='Unsent profile changes';
 node('aiKeysForm').elements.gemini_key.value='Unsent synthetic credential';
 const own={...admin,plan_tier:'paid_starter',subscription_status:'active',subscription_active:true};
 handler=async url=>reply(url==='/api/me'?{user:own,platform_auto_authorized:true}:{ok:true,member:own});
 await click({dataset:{toggleplan:'1',tier:'paid_starter'},disabled:false});
 assert.equal(run('S.me.plan_tier'),'paid_starter');
 assert.match(node('planStatus').textContent,/Starter/);
 assert.equal(node('settingsForm').elements.description.value,'Unsent profile changes');
 assert.equal(node('aiKeysForm').elements.gemini_key.value,'Unsent synthetic credential');
 assert.equal(requests.filter(request=>request.url==='/api/me').length,1);
 sandbox.completed=true;
})().catch(error=>{console.error(error);process.exitCode=1});
''')


@pytest.mark.skipif(not shutil.which('node'), reason='Node is needed for frontend checks')
def test_team_rows_report_automation_readiness_instead_of_only_saved_switches():
    browser_check(r'''
(async()=>{
 const base={...member,auto_admin_allowed:true,auto_requested:true,connected:true};
 const members=[{...base,id:2,connected:false},{...base,id:3,subscription_active:false},
  {...base,id:4,auto_admin_allowed:false},{...base,id:5}];
 handler=async url=>reply(url==='/api/team'?{members}:{payments:[]});
 run('S.platform_auto=true');await run('loadTeam()');
 const html=node('teamList').innerHTML;
 for(const status of ['Connect Freelancer','Subscription inactive','Paused by administrator','Enabled (worker required)']){
  assert.ok(html.includes('Auto-Bid: '+status),status);
 }
 assert.ok(!html.includes('Auto-Bid: ON'));
 run('S.platform_auto=false');await run('loadTeam()');
 assert.equal((node('teamList').innerHTML.match(/Auto-Bid: Server switch OFF/g)||[]).length,4);
 sandbox.completed=true;
})().catch(error=>{console.error(error);process.exitCode=1});
''')
