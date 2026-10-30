"""Exercise live status truthfulness and browser request isolation with Node."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(),intervals=[],timeouts=new Map(),listeners={},calls=[];
let monotonic=1000,timerId=0,reloads=0;
function node(id){
  if(!nodes.has(id))nodes.set(id,{
    textContent:'',className:'',
    set innerHTML(_){throw Error('Live status must use plain text')},
    get innerHTML(){return ''},
  });
  return nodes.get(id);
}
const nav={listeners:{},addEventListener(type,handler){this.listeners[type]=handler}};
const server=1800000000;
function snapshot(overrides={}){
  return {server_time:server,
    worker:{online:true,last_heartbeat:server,stale_after_seconds:35},
    connection:{connected:true,last_scan_started_at:server,last_scan_success_at:0,scan_in_progress:false,scan_claim_until:0,api_pause_reason:'',cooldown_until:0},
    automation:{state:'watching',enabled:true,reason:'Watching your saved project feed.',scan_interval_seconds:60,next_scan_at:server+60,next_bid_at:server+30,queue_count:2,limits:{hourly_max:3,daily_max:10,hourly_used:0,daily_used:0}},
    ...overrides,
  };
}
let response=async()=>snapshot();
const sandbox={console,Date,AbortController,encodeURIComponent,
  S:{me:null,epoch:0,nav:'overview'},performance:{now:()=>monotonic},
  window:{location:{reload(){reloads++}}},
  document:{hidden:false,getElementById:node,
    querySelectorAll(selector){return selector==='[data-nav="overview"]'?[nav]:[]},
    addEventListener(type,handler){listeners[type]=handler}},
  setInterval(handler,delay){intervals.push({handler,delay});return intervals.length},
  setTimeout(handler,delay){const id=++timerId;timeouts.set(id,{handler,delay});return id},
  clearTimeout(id){timeouts.delete(id)},
  api:async(path,options)=>{calls.push({path,options});return response(path,options)},
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('app/static/live.js','utf8'),sandbox);
const refresh=()=>sandbox.window.refreshAutomationLive();
const clear=()=>sandbox.window.clearAutomationLive();
const text=id=>node(id).textContent;
const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function tick(milliseconds){monotonic+=milliseconds;intervals[0].handler();await flush()}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
'''


def run_ui(body):
    if not shutil.which('node'):
        pytest.skip('Node is required for browser JavaScript checks')
    script = HARNESS + '\n(async()=>{\n' + body + '\n})().catch(error=>{console.error(error);process.exitCode=1});'
    result = subprocess.run(['node', '-e', script], cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_status_polling_only_uses_authenticated_visible_overview_and_serializes():
    run_ui(r'''
assert.equal(calls.length,0);assert.equal(intervals.length,1);assert.equal(intervals[0].delay,1000);
assert.match(text('liveAutomationReason'),/Sign in/);
sandbox.S.me={id:17};sandbox.S.nav='settings';await refresh();assert.equal(calls.length,0);
sandbox.S.nav='overview';sandbox.document.hidden=true;await refresh();assert.equal(calls.length,0);
sandbox.document.hidden=false;
const pending=deferred();response=()=>pending.promise;const request=refresh();
assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/automation/status?expected_user_id=17');
assert.ok(calls[0].options.signal);assert.equal(timeouts.size,1);
assert.equal([...timeouts.values()][0].delay,10000);
await refresh();await tick(60000);assert.equal(calls.length,1,'An in-flight request must not overlap');
pending.resolve(snapshot());await request;assert.equal(timeouts.size,0);
response=async()=>snapshot();await refresh();assert.equal(calls.length,2);
await refresh();await tick(14000);assert.equal(calls.length,2);
await tick(1000);assert.equal(calls.length,3);
sandbox.S.nav='jobs';await tick(30000);assert.equal(calls.length,3);
sandbox.S.nav='overview';sandbox.document.hidden=true;await tick(30000);assert.equal(calls.length,3);
sandbox.document.hidden=false;listeners.visibilitychange();await flush();assert.equal(calls.length,4);
assert.ok(calls.every(call=>call.path.startsWith('/api/automation/status?')),'Browser must not poll Freelancer or AI directly');
''')


def test_status_uses_real_poll_success_and_server_clock_for_countdowns():
    run_ui(r'''
sandbox.S.me={id:1};await refresh();
assert.equal(text('liveWorkerStatus'),'Worker online');
assert.equal(text('liveAutomationState'),'Watching for projects');
assert.equal(text('liveApiStatus'),'Waiting for a successful poll');
assert.equal(text('liveLastSuccessfulScan'),'No successful poll recorded','A started scan must not claim success');
assert.equal(text('liveNextScan'),'In 1m 0s');assert.equal(text('liveBidPacing'),'Next bid window · 30s');
assert.equal(text('liveQueueCount'),'2');assert.equal(text('liveAutomationLimits'),'3/hour · 10/day');
await tick(5000);assert.equal(text('liveNextScan'),'In 55s');assert.equal(text('liveBidPacing'),'Next bid window · 25s');
const stalled=deferred();response=()=>stalled.promise;
await tick(31000);
assert.equal(text('liveAutomationState'),'Status needs a refresh');
assert.equal(text('liveWorkerStatus'),'Worker not recently seen');
assert.equal(text('liveNextScan'),'Waiting for the worker');

clear();response=async()=>{
  const data=snapshot();data.connection.last_scan_success_at=server-10;data.worker.online=false;data.worker.last_heartbeat=server-80;data.automation.state='offline';return data;
};await refresh();
assert.equal(text('liveAutomationState'),'Worker offline');
assert.equal(text('liveApiStatus'),'Last project poll succeeded');assert.match(text('liveLastSuccessfulScan'),/^Last success/);
assert.equal(text('liveNextScan'),'Waiting for the worker');

clear();response=async()=>{
  const data=snapshot();data.connection.last_scan_success_at=server-100;data.connection.last_scan_failed=true;return data;
};await refresh();
assert.equal(text('liveApiStatus'),'Last project refresh failed');assert.match(text('liveLastSuccessfulScan'),/^Last success/);

clear();response=async()=>{
  const data=snapshot();data.automation.state='cooldown';data.connection.cooldown_until=server+90;return data;
};await refresh();assert.equal(text('liveAutomationState'),'API cooldown');
assert.equal(text('liveApiStatus'),'Freelancer rate-limit pause');assert.equal(text('liveNextScan'),'After cooldown · 1m 30s');
clear();response=async()=>{
  const data=snapshot();data.automation.state='paused';data.connection.api_pause_reason='auth_error';return data;
};await refresh();assert.equal(text('liveNextScan'),'Reconnect to resume scanning');
clear();response=async()=>{
  const data=snapshot();data.automation.state='subscription_inactive';return data;
};await refresh();assert.equal(text('liveNextScan'),'Active subscription required');
clear();response=async()=>{
  const data=snapshot();data.automation.state='scanning';data.connection.scan_in_progress=true;data.connection.scan_claim_until=server+120;return data;
};await refresh();assert.equal(text('liveNextScan'),'Scan in progress');assert.equal(text('liveApiStatus'),'Checking Freelancer projects');
clear();response=async()=>{
  const data=snapshot();data.automation.state='disconnected';data.connection.connected=false;return data;
};await refresh();assert.equal(text('liveApiStatus'),'No account connected');assert.equal(text('liveNextScan'),'Connect your Freelancer account');
''')


def test_account_change_clears_private_status_and_discards_stale_responses():
    run_ui(r'''
sandbox.S.me={id:1};const previous=deferred();response=()=>previous.promise;
const oldRequest=refresh();assert.equal(calls.length,1);
sandbox.S.epoch++;sandbox.S.me={id:2};clear();assert.equal(calls[0].options.signal.aborted,true);
assert.equal(text('liveQueueCount'),'—');
const current=deferred();response=()=>current.promise;const newRequest=refresh();assert.equal(calls.length,2);
const privateData=snapshot();privateData.automation.reason='Old private reason';privateData.automation.queue_count=777;
previous.resolve(privateData);await oldRequest;
assert.notEqual(text('liveQueueCount'),'777');assert.ok(!text('liveAutomationReason').includes('Old private'));
await refresh();assert.equal(calls.length,2,'An old response must not unlock the newer pending request');
current.resolve(snapshot());await newRequest;assert.equal(text('liveQueueCount'),'2');
sandbox.S.epoch++;sandbox.S.me=null;clear();
assert.equal(text('liveQueueCount'),'—');assert.equal(text('liveWorkerStatus'),'Not checked');
assert.match(text('liveAutomationReason'),/Sign in/);await tick(30000);assert.equal(calls.length,2);

// A cookie replaced by another tab is rejected before any new-account data is rendered.
sandbox.S.me={id:3};response=async()=>{const error=Error('Account changed');error.status=409;throw error};await refresh();
assert.equal(reloads,1);assert.match(text('liveAutomationReason'),/account changed/);assert.equal(text('liveQueueCount'),'—');
''')


def test_invalid_or_failed_status_is_never_reported_as_online_and_text_is_safe():
    run_ui(r'''
sandbox.S.me={id:1};response=async()=>{
  const data=snapshot();data.automation.reason='<img src=x onerror=alert(1)>';return data;
};await refresh();assert.equal(text('liveAutomationReason'),'<img src=x onerror=alert(1)>');
clear();response=async()=>({worker:[],connection:{},automation:{}});await refresh();
assert.equal(text('liveWorkerStatus'),'Not checked');assert.equal(text('liveQueueCount'),'—');
assert.match(text('liveAutomationReason'),/unavailable/);
const previousCalls=calls.length;await refresh();assert.equal(calls.length,previousCalls,'Failed requests must respect the poll interval');
await tick(15000);assert.equal(calls.length,previousCalls+1);
clear();response=async()=>{throw Error('Private provider credential details')};await refresh();
assert.ok(!text('liveAutomationReason').includes('credential'));assert.equal(text('liveLastSuccessfulScan'),'No successful poll recorded');
clear();response=async()=>{
  const data=snapshot();data.automation.state='unexpected';data.automation.queue_count=-5;data.automation.scan_interval_seconds=0;return data;
};await refresh();assert.equal(text('liveAutomationState'),'Status unavailable');
assert.equal(text('liveQueueCount'),'—');assert.equal(text('liveScanTarget'),'Refresh target: 60 seconds');
''')


def test_live_panel_markup_matches_script_and_preserves_existing_help():
    html = (ROOT / 'app/static/index.html').read_text()
    for name in ('liveAutomationPanel', 'liveAutomationState', 'liveAutomationReason', 'liveWorkerStatus',
                 'liveLastHeartbeat', 'liveApiStatus', 'liveLastSuccessfulScan', 'liveNextScan',
                 'liveQueueCount', 'liveBidPacing', 'liveAutomationLimits', 'liveScanTarget'):
        assert html.count(f'id="{name}"') == 1
    assert html.index('/static/app.js') < html.index('/static/support.js') < html.index('/static/live.js')
    assert 'PROJECTS AWAITING CHECKS' in html
    assert 'id="supportForm"' in html
    assert 'style=' not in html
