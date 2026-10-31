"""Login waits come from the server and remain scoped to the submitted identity."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which('node'), reason='Node is needed for frontend checks')
def test_login_cooldown_registration_and_admin_unlock_lifecycle():
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(),events={},requests=[];let now=1800000000000,handler,initialized=0,timerId=0;
const timers=new Map();class ClockDate extends Date{static now(){return now}}
function node(id){
 if(!nodes.has(id)){
  const classes=new Set(),el={value:'',checked:false,disabled:false,innerHTML:'',textContent:'',dataset:{},listeners:{},
   classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle:(x,on)=>on?classes.add(x):classes.delete(x)},
   addEventListener(t,fn){(this.listeners[t]??=[]).push(fn)},querySelector:q=>q==='button[type="submit"]'?node(id+':submit'):null,
   toggleAttribute(n,on){if(n==='disabled')this.disabled=on},removeAttribute(){},focus(){},reset(){},
  };el.elements=new Proxy({}, {get:(_,key)=>node(id+':'+String(key))});nodes.set(id,el);
 }return nodes.get(id);
}
const reply=(body,status=200,headers={})=>({ok:status<400,status,headers:{get:k=>headers[k]??null},json:async()=>body});
const sandbox={URL,Date:ClockDate,console,document:{getElementById:node,querySelectorAll:()=>[],addEventListener:(name,fn)=>events[name]=fn},
 window:{scrollTo(){},location:{reload(){}}},navigator:{},setInterval(){},
 setTimeout(fn){timers.set(++timerId,fn);return timerId},clearTimeout(id){timers.delete(id)},
 fetch:async(url,options)=>{requests.push({url,body:options.body?JSON.parse(options.body):null});return handler(url,options)}};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync('app/static/app.js','utf8').replace('(async()=>{await initialize()})();',''),sandbox);
const run=code=>vm.runInContext(code,sandbox),submit=()=>node('loginForm').onsubmit({preventDefault(){}});
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve}}
function credentials(email='member@example.test',password='Correct-private-password-123!'){node('loginEmail').value=email;node('loginPassword').value=password}
(async()=>{
 sandbox.onInitialize=()=>{initialized++};run('initialize=async()=>onInitialize()');handler=async()=>reply({ok:true});
 // Registration creates its session; no automatic second sign-in request.
 run('setAuthMode(true)');credentials();node('registerName').value='Member Full Name';await submit();
 assert.equal(requests.filter(r=>r.url==='/api/register').length,1);assert.equal(requests.filter(r=>r.url==='/api/login').length,0);
 assert.equal(initialized,1);assert.equal(run('S.registering'),false);assert.equal(node('loginPassword').value,'');

 // Duplicates are blocked; later drafts are not overwritten after an error.
 const pending=deferred();handler=async()=>pending.promise;credentials();const sending=submit();await submit();
 assert.equal(requests.filter(r=>r.url==='/api/login').length,1);assert.equal(node('authSubmit').disabled,true);
 node('loginPassword').value='Newer-unsent-password-123!';
 pending.resolve(reply({detail:'Too many attempts.',retry_after_seconds:125},429,{'Retry-After':'125'}));await sending;
 assert.equal(node('loginPassword').value,'Newer-unsent-password-123!');assert.equal(node('loginEmail').value,'member@example.test');
 assert.match(node('loginMsg').textContent,/2m 5s/);assert.equal(node('authSubmit').disabled,true);assert.equal(node('authToggle').disabled,false);
 const count=requests.length;await submit();assert.equal(requests.length,count);
 now+=65000;run('renderAuthControls()');assert.match(node('loginMsg').textContent,/1m 0s/);
 // A different email or registration is not locked by the old account's wait.
 credentials('another@example.test');node('loginEmail').listeners.input[0]();assert.equal(node('authSubmit').disabled,false);
 credentials();node('loginEmail').listeners.input[0]();assert.equal(node('authSubmit').disabled,true);
 node('authToggle').onclick();assert.equal(run('S.registering'),true);assert.equal(node('authSubmit').disabled,false);
 node('authToggle').onclick();assert.equal(node('authSubmit').disabled,true);
 now+=60000;run('renderAuthControls()');assert.equal(node('authSubmit').disabled,false);assert.match(node('loginMsg').textContent,/try again now/);
 handler=async()=>reply({ok:true});await submit();assert.equal(initialized,2);

 // A response for an old email does not falsely lock the newer unsent draft.
 const oldEmail=deferred();handler=async()=>oldEmail.promise;credentials('old@example.test');const old=submit();
 credentials('new@example.test','Newer-draft-password-123!');oldEmail.resolve(reply({detail:'Wait',retry_after_seconds:20},429));await old;
 assert.equal(node('authSubmit').disabled,false);assert.match(node('loginMsg').textContent,/newer details are still unsent/);
 assert.equal(node('loginEmail').value,'new@example.test');

 // Numeric and server-Date-based HTTP headers work; missing metadata never
 // invents a 15-minute wait, and account reset discards an old pending reply.
 handler=async()=>reply({detail:'Slow down'},429,{'Retry-After':'7'});
 await assert.rejects(run("api('/api/login')"),error=>error.status===429&&error.retryAfter===7);
 handler=async()=>reply({detail:'Slow down'},429,{'Retry-After':'Wed, 01 Jan 2025 00:00:08 GMT','Date':'Wed, 01 Jan 2025 00:00:00 GMT'});
 await assert.rejects(run("api('/api/login')"),error=>error.retryAfter===8);
 handler=async()=>reply({detail:'Slow down'},429);credentials('metadata-missing@example.test');await submit();assert.equal(node('authSubmit').disabled,false);
 const stale=deferred();handler=async()=>stale.promise;credentials('stale@example.test');const staleLogin=submit();
 run('resetSession()');credentials('new-session@example.test');stale.resolve(reply({ok:true}));await staleLogin;
 assert.equal(initialized,2);assert.equal(node('loginEmail').value,'new-session@example.test');assert.equal(node('authSubmit').disabled,false);

 // Admin unlock is single-flight, identifies the acting account and cannot be
 // invoked by a customer. It does not send credentials or mutate a plan.
 run('S.me={id:1,role:"admin"};S.csrf="test-csrf";loadTeam=async()=>{}');
 const unlocked=deferred();handler=async()=>unlocked.promise;
 const target={dataset:{loginUnlock:'2'},disabled:false},event={target:{closest:()=>target}};
 const unlocking=events.click(event);await events.click(event);
 const sent=requests.filter(r=>r.url==='/api/team/2/login-unlock');assert.equal(sent.length,1);assert.deepEqual(sent[0].body,{expected_user_id:1});
 unlocked.resolve(reply({ok:true}));await unlocking;assert.equal(target.disabled,false);
 assert.match(node('toast').textContent,/network limits still apply/);
 run('S.me={id:3,role:"member"}');const before=requests.length;await events.click(event);assert.equal(requests.length,before);
 console.log('Login cooldown, registration, draft, stale-response and unlock checks passed');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_admin_creation_does_not_reuse_operator_login_autofill():
    from html.parser import HTMLParser

    class Forms(HTMLParser):
        def __init__(self):
            super().__init__()
            self.form = None
            self.controls = {}

        def handle_starttag(self, tag, attributes):
            attributes = dict(attributes)
            if tag == 'form':
                self.form = attributes.get('id')
            if tag == 'input' and self.form:
                key = attributes.get('name') or attributes.get('id')
                self.controls[self.form, key] = attributes

        def handle_endtag(self, tag):
            if tag == 'form':
                self.form = None

    forms = Forms()
    forms.feed((Path(__file__).resolve().parents[1] / 'app/static/index.html').read_text())
    assert forms.controls['memberForm', 'name']['autocomplete'] == 'off'
    assert forms.controls['memberForm', 'email']['autocomplete'] == 'off'
    password = forms.controls['memberForm', 'password']
    assert password['autocomplete'] == 'new-password'
    assert password['minlength'] == '12' and password['maxlength'] == '256'
    assert forms.controls['loginForm', 'loginEmail']['autocomplete'] == 'username'
    assert forms.controls['loginForm', 'loginPassword']['autocomplete'] == 'current-password'
