"""Sensitive account controls clear inputs and discard prior-account responses."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which('node'), reason='Node required for frontend behavior')
def test_security_controls_session_and_password_privacy():
    harness = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',disabled:false,setAttribute(){},set innerHTML(_){throw Error('Unsafe HTML')}});return nodes.get(id)}
const nav={addEventListener(){}};
const sandbox={S:{me:{id:1},epoch:0,nav:'settings'},window:{location:{reload(){sandbox.reloads++}}},reloads:0,Date,encodeURIComponent,AbortController,
 document:{hidden:false,getElementById:node,querySelectorAll(){return [nav]}},post:async()=>({ok:true,sign_in_required:true}),
 api:async()=>({active_sessions:2,password_storage:'salted_pbkdf2',saved_credentials_encrypted:true})};
sandbox.resetSession=()=>{sandbox.S.me=null;sandbox.S.epoch++;sandbox.window.clearAccountSecurity()};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync('app/static/security.js','utf8'),sandbox);
const form=node('accountPasswordForm'),submit=()=>form.onsubmit({preventDefault(){}});
const fields=['currentPassword','newPassword','confirmPassword'].map(node),message=node('accountSecurityMessage'),summary=node('accountSecuritySummary');
function draft(){fields[0].value='Old-private-password';fields[1].value=fields[2].value='New-private-password-123'}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
(async()=>{
 await sandbox.window.refreshAccountSecurity();assert.match(summary.textContent,/2 active session/);
 let calls=0;sandbox.post=async()=>{calls++;return {ok:true,sign_in_required:true}};
 draft();fields[2].value='mismatch';await submit();assert.equal(calls,0);assert.match(message.textContent,/matching/);
 const pending=deferred();let sent;
 sandbox.post=async(path,body)=>{calls++;sent={path,body};return pending.promise};draft();const request=submit();
 assert.equal(sent.path,'/api/security/password');assert.equal(sent.body.expected_user_id,1);assert.equal(sent.body.current_password,'Old-private-password');
 assert.ok(fields.every(field=>field.value===''&&field.disabled));await node('logoutAllSessionsButton').onclick();assert.equal(calls,1);
 pending.resolve({ok:true,sign_in_required:true});await request;assert.equal(sandbox.S.me,null);assert.match(node('loginMsg').textContent,/Password changed/);
 sandbox.S.me={id:2};sandbox.window.clearAccountSecurity();draft();
 sandbox.post=async()=>{const error=Error('Current password is incorrect');error.status=400;throw error};await submit();
 assert.ok(fields.every(field=>field.value===''));assert.match(message.textContent,/incorrect/);assert.equal(sandbox.S.me.id,2);
 const stale=deferred();sandbox.post=async()=>stale.promise;draft();const old=submit();
 sandbox.S.epoch++;sandbox.S.me={id:3};sandbox.window.clearAccountSecurity();stale.resolve({ok:true,sign_in_required:true});await old;
 assert.equal(sandbox.S.me.id,3);assert.ok(fields.every(field=>field.value===''));
 const oldStatus=deferred();sandbox.api=async()=>oldStatus.promise;const loading=sandbox.window.refreshAccountSecurity();
 sandbox.S.epoch++;sandbox.S.me={id:4};sandbox.window.clearAccountSecurity();oldStatus.resolve({active_sessions:999,password_storage:'salted_pbkdf2'});await loading;
 assert.ok(!summary.textContent.includes('999'));assert.equal(sandbox.S.me.id,4);
 sandbox.api=async()=>{const error=Error('Account changed');error.status=409;throw error};await sandbox.window.refreshAccountSecurity();assert.equal(sandbox.reloads,1);
 sandbox.S.me={id:5};sandbox.window.clearAccountSecurity();let logoutBody;
 sandbox.post=async(path,body)=>{assert.equal(path,'/api/security/logout-all');logoutBody=body;return {ok:true,sign_in_required:true}};
 await node('logoutAllSessionsButton').onclick();assert.equal(logoutBody.expected_user_id,5);assert.equal(sandbox.S.me,null);
 assert.match(node('loginMsg').textContent,/All account sessions/);
 console.log('Security controls privacy checks passed');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', harness], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
