'use strict';
(()=>{
  const panel=document.getElementById('liveAutomationPanel');
  if(!panel)return;
  const get=id=>document.getElementById(id);
  const nodes={
    state:get('liveAutomationState'),reason:get('liveAutomationReason'),worker:get('liveWorkerStatus'),
    heartbeat:get('liveLastHeartbeat'),api:get('liveApiStatus'),success:get('liveLastSuccessfulScan'),
    next:get('liveNextScan'),queue:get('liveQueueCount'),pacing:get('liveBidPacing'),limits:get('liveAutomationLimits'),
    target:get('liveScanTarget'),
  };
  const interval=15000;
  const state={owner:'',revision:0,pending:false,nextPoll:0,data:null,received:0,serverTime:0,cancel:null};
  const identity=()=>S.me?`${S.epoch}:${S.me.id}`:'';
  const monotonic=()=>globalThis.performance?.now?.()??Date.now();
  const visible=()=>!!S.me&&S.nav==='overview'&&!document.hidden;
  const timestamp=value=>typeof value==='number'&&Number.isFinite(value)&&value>0&&value<4102444800?Math.floor(value):0;
  const count=value=>typeof value==='number'&&Number.isInteger(value)&&value>=0?value:null;
  const date=value=>value?new Date(value*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit'}):'Not recorded yet';
  const remaining=value=>{
    const seconds=Math.max(0,Math.ceil(value));
    return seconds<60?`${seconds}s`:`${Math.floor(seconds/60)}m ${seconds%60}s`;
  };
  const object=value=>value&&typeof value==='object'&&!Array.isArray(value);
  function fillEmpty(message='Sign in to view your automation status.'){
    nodes.state.textContent='Status not loaded';nodes.state.className='live-state neutral';
    nodes.reason.textContent=message;
    nodes.worker.textContent='Not checked';nodes.heartbeat.textContent='No heartbeat recorded';
    nodes.api.textContent='Not checked';nodes.success.textContent='No successful poll recorded';
    nodes.next.textContent='Waiting for service';nodes.queue.textContent='—';
    nodes.pacing.textContent='Your bid limits still apply';nodes.limits.textContent='Limits not loaded';
    nodes.target.textContent='Refresh target: 60 seconds';
  }
  function clear(){
    state.revision++;state.cancel?.abort();
    Object.assign(state,{owner:identity(),pending:false,nextPoll:0,data:null,received:0,serverTime:0,cancel:null});
    fillEmpty(S.me?'Checking your automation service…':undefined);
  }
  function ensureOwner(){if(state.owner!==identity())clear();}
  function clock(){return state.serverTime+Math.max(0,monotonic()-state.received)/1000;}
  function render(){
    if(!state.data)return;
    const {worker,connection,automation}=state.data,now=clock();
    const beat=timestamp(worker.last_heartbeat),stale=Number.isFinite(worker.stale_after_seconds)&&worker.stale_after_seconds>0?worker.stale_after_seconds:35;
    const heartbeatFresh=worker.online===true&&beat>0&&now>=beat-5&&now-beat<=stale;
    const success=timestamp(connection.last_scan_success_at),cooldown=timestamp(connection.cooldown_until);
    const scanning=connection.scan_in_progress===true&&timestamp(connection.scan_claim_until)>now;
    const labels={disconnected:'Connect an account',subscription_inactive:'Subscription inactive',cooldown:'API cooldown',paused:'Automation paused',offline:'Worker offline',outside_schedule:'Outside working hours',waiting_gap:'Waiting for bid gap',scanning:'Scanning projects',watching:'Watching for projects',invalid_settings:'Review settings'};
    const known=Object.hasOwn(labels,automation.state),waiting=automation.state==='watching'||automation.state==='scanning'||automation.state==='waiting_gap';
    const staleWorker=worker.online===true&&!heartbeatFresh;
    nodes.state.textContent=staleWorker&&waiting?'Status needs a refresh':known?labels[automation.state]:'Status unavailable';
    nodes.state.className='live-state '+(staleWorker?'neutral':automation.state==='watching'&&heartbeatFresh?'positive':automation.state==='scanning'&&heartbeatFresh?'active':'neutral');
    nodes.reason.textContent=staleWorker?'The worker heartbeat is no longer fresh. Waiting for the next status check.':typeof automation.reason==='string'&&automation.reason.trim()?automation.reason:'Review your connection and automation settings.';
    nodes.worker.textContent=heartbeatFresh?'Worker online':beat?'Worker not recently seen':'Worker offline';
    nodes.heartbeat.textContent=beat?'Last heartbeat · '+date(beat):'No heartbeat recorded';
    if(!connection.connected)nodes.api.textContent='No account connected';
    else if(cooldown>now)nodes.api.textContent='Freelancer rate-limit pause';
    else if(connection.api_pause_reason&&connection.api_pause_reason!=='rate_limited')nodes.api.textContent='Freelancer access paused';
    else if(scanning)nodes.api.textContent='Checking Freelancer projects';
    else if(connection.last_scan_failed===true)nodes.api.textContent='Last project refresh failed';
    else nodes.api.textContent=success?'Last project poll succeeded':'Waiting for a successful poll';
    nodes.success.textContent=success?'Last success · '+date(success):'No successful poll recorded';
    const next=timestamp(automation.next_scan_at);
    if(!connection.connected)nodes.next.textContent='Connect your Freelancer account';
    else if(automation.state==='subscription_inactive')nodes.next.textContent='Active subscription required';
    else if(cooldown>now)nodes.next.textContent='After cooldown · '+remaining(cooldown-now);
    else if(connection.api_pause_reason&&connection.api_pause_reason!=='rate_limited')nodes.next.textContent='Reconnect to resume scanning';
    else if(!heartbeatFresh)nodes.next.textContent='Waiting for the worker';
    else if(scanning)nodes.next.textContent='Scan in progress';
    else nodes.next.textContent=next>now?'In '+remaining(next-now):'Due · waiting for the worker';
    const queue=count(automation.queue_count);
    nodes.queue.textContent=queue===null?'—':String(queue);
    const bid=timestamp(automation.next_bid_at);
    nodes.pacing.textContent=bid>now?'Next bid window · '+remaining(bid-now):'Final eligibility checks apply';
    const limits=object(automation.limits)?automation.limits:{},hourly=count(limits.hourly_max),daily=count(limits.daily_max);
    nodes.limits.textContent=hourly!==null&&daily!==null?`${hourly}/hour · ${daily}/day`:'Check your saved bid limits';
    const target=Number.isInteger(automation.scan_interval_seconds)&&automation.scan_interval_seconds>=60?automation.scan_interval_seconds:60;
    nodes.target.textContent=`Refresh target: ${target} seconds`;
  }
  async function refresh(){
    ensureOwner();
    if(!visible()||state.pending||monotonic()<state.nextPoll)return;
    const owner=identity(),revision=++state.revision;
    state.pending=true;state.nextPoll=monotonic()+interval;
    const controller=typeof AbortController==='function'?new AbortController():null;
    state.cancel=controller;
    const timeout=controller?setTimeout(()=>controller.abort(),10000):null;
    try{
      const response=await api('/api/automation/status?expected_user_id='+encodeURIComponent(S.me.id),controller?{signal:controller.signal}:{});
      if(owner!==identity()||revision!==state.revision)return;
      if(!object(response)||!object(response.worker)||!object(response.connection)||!object(response.automation))throw Error('Invalid status');
      state.data=response;state.received=monotonic();
      state.serverTime=timestamp(response.server_time)||Math.floor(Date.now()/1000);
      render();
    }catch(error){
      if(owner===identity()&&revision===state.revision){
        if(error.status===409){
          clear();fillEmpty('Your account changed. Reloading your workspace…');window.location.reload();
        }else{state.data=null;fillEmpty('Live status is unavailable. It will be checked again shortly.');}
      }
    }finally{
      if(timeout!==null)clearTimeout(timeout);
      if(owner===identity()&&revision===state.revision){state.pending=false;state.cancel=null;}
    }
  }
  window.clearAutomationLive=clear;
  window.refreshAutomationLive=refresh;
  document.querySelectorAll('[data-nav="overview"]').forEach(button=>button.addEventListener('click',()=>{void refresh()}));
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void refresh()});
  setInterval(()=>{
    ensureOwner();
    if(visible()){render();void refresh();}
  },1000);
  clear();void refresh();
})();
