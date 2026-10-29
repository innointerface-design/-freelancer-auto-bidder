'use strict';
const $=id=>document.getElementById(id),S={me:null,csrf:'',jobs:[],selected:null,nav:'overview',intent:null,team:[],seen:null,alerts:false,ai_provider:'',payment_methods:{},registering:false,epoch:0,refreshRevision:0,proposalRevision:0,editorRevision:0,bidBusy:false,authBusy:false};
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const usd=j=>{const c=j.currency||'Currency unknown',a=j.budget_min,b=j.budget_max;return a==null&&b==null?`${c} · Budget flexible`:`${c} ${a??'?'}${b!=null?'–'+b:''}${j.type?.includes('hour')?'/hr':''}`};
const age=t=>{if(!t)return 'Not scanned yet';return new Date(Number(t)*1000).toLocaleString()};
const trust=j=>j.payment_verified===true?'Verified payment':j.payment_verified===false?'Unverified payment':'Payment unverified/new';
const scoreStyle=n=>n>=68?'':n>=45?'medium':'low';
const notify=(s,err=false)=>{if(!s||s==='Session changed')return;const e=$('toast');e.textContent=s;e.className='toast show'+(err?' error':'');clearTimeout(e.timer);e.timer=setTimeout(()=>e.className='toast',4800)};

function setAuthMode(registering){
  S.registering=registering;
  $('authTitle').textContent=registering?'Create your account.':'Welcome back.';
  $('registerNameLabel').classList.toggle('hidden',!registering);
  $('registerName').required=registering;
  $('loginPassword').minLength=registering?12:1;
  $('loginPassword').autocomplete=registering?'new-password':'current-password';
  $('authSubmit').textContent=registering?'Create account →':'Sign in to workspace →';
  $('authToggle').textContent=registering?'Already registered? Sign in':'New here? Create an account';
  $('loginMsg').textContent='';
}
function resetSession(){
  S.epoch++;S.refreshRevision++;S.proposalRevision++;S.editorRevision++;
  Object.assign(S,{me:null,csrf:'',jobs:[],selected:null,nav:'overview',intent:null,team:[],seen:null,alerts:false,ai_provider:'',payment_methods:{},bidBusy:false,authBusy:false});
  document.querySelectorAll('form').forEach(form=>form.reset());
  for(const id of ['teamList','adminPaymentsList','teamOverview','billingSummaryBox','topJobs','jobsTable','proposalsBody','liveBidsBody','activityBody','myPlanBanner','myPaymentsList','paymentMethodsBox','aiKeysBanner','aiTestResultBox','testChatBox','aiReviewBox','connectionBanner','autoStatus','chatStatus','aiStatus','modalNotice','modalTitle','modalDesc','modalMeta','modalTrust','modalReasons','confirmText','confirmProposal','accountName','accountEmail','greeting','avatar','planStatus','connStatus','lastScan','planBadgeTitle','planBadgeDesc','geminiMaskBadge','openaiMaskBadge'])$(id).textContent='';
  for(const id of ['bidProposal','bidAmount','bidDays','bidMilestone','loginEmail','loginPassword','registerName','connectToken','confirmation','jobSearch'])$(id).value='';
  $('visitProject').removeAttribute('href');$('whatsappApproveBtn').removeAttribute('href');
  $('scoreFilter').value='0';$('countryFilter').value='';$('typeFilter').value='all';
  for(const id of ['statJobs','statStrong','statDraft','statBids','sideCount'])$(id).textContent='0';
  document.querySelectorAll('.admin-only').forEach(el=>el.classList.add('hidden'));
  $('teamOverview').classList.add('hidden');$('aiTestResultBox').classList.add('hidden');$('testChatBox').classList.add('hidden');
  $('shell').classList.add('hidden');$('auth').classList.remove('hidden');
  $('authSubmit').disabled=false;$('authToggle').disabled=false;
  closeJob();setAuthMode(false);
}
function applyAccount(x){
  if(!x?.user)throw Error('Server returned an invalid account response');
  if(S.me&&(S.me.id!==x.user.id||S.me.role!==x.user.role)){
    // Another tab may replace this HttpOnly session cookie. Clear the prior
    // account before reloading the new cookie, and discard this entire batch.
    resetSession();window.location.reload();throw Error('Session changed');
  }
  S.me=x.user;S.platform_auto=x.platform_auto_authorized;S.ai_enabled=x.ai_enabled;S.ai_provider=x.ai_provider||'Template fallback';S.payment_methods=x.payment_methods||{};
  $('accountName').textContent=S.me.name;$('accountEmail').textContent=S.me.email;
  $('avatar').textContent=S.me.name.slice(0,1).toUpperCase();$('greeting').textContent=S.me.name.split(' ')[0];
}

async function api(url,opts={}){
  const epoch=S.epoch;
  let r;
  try{
    r=await fetch(url,{credentials:'same-origin',...opts,headers:{'Content-Type':'application/json',...(opts.method&&opts.method!=='GET'&&url!=='/api/login'?{'X-CSRF-Token':S.csrf}:{}),...(opts.headers||{})}});
  }catch{throw Error('Network connection unavailable')}
  let data;try{data=await r.json()}catch{throw Error(`Server returned an unreadable response (${r.status})`)}
  if(epoch!==S.epoch)throw Error('Session changed');
  if(r.status===401&&S.me)resetSession();
  if(!r.ok){const error=new Error(typeof data.detail==='string'?data.detail:Array.isArray(data.detail)?data.detail.map(v=>`${v.loc?.slice(1).join('.')||'Input'}: ${v.msg||'Invalid value'}`).join(';'):`Request failed (${r.status})`);error.status=r.status;throw error;}
  return data;
}
const post=(path,data={})=>api(path,{method:'POST',body:JSON.stringify(data)});
const planName=tier=>({free_team:'Founder',paid_starter:'Starter',paid_pro:'Pro'})[tier]||'Subscription';
const planPrice=tier=>tier==='free_team'?0:tier==='paid_pro'?40:10;
function safePaymentUrl(value){try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password?u.href:''}catch{return ''}}

async function initialize(){
  try{
    let x=await api('/api/me');
    applyAccount(x);
    S.csrf=(await api('/api/csrf')).csrf;
    $('auth').classList.add('hidden');$('shell').classList.remove('hidden');
    $('accountName').textContent=S.me.name;$('accountEmail').textContent=S.me.email;
    $('avatar').textContent=S.me.name.slice(0,1).toUpperCase();
    $('greeting').textContent=S.me.name.split(' ')[0];
    $('today').textContent=new Date().toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'});
    document.querySelectorAll('.admin-only').forEach(e=>e.classList.toggle('hidden',S.me.role!=='admin'));
    renderSettings();renderStatus();navigate(S.nav);await refresh();
  }catch(e){if(e.message==='Session changed')return;if(e.status===401||!S.me){$('auth').classList.remove('hidden');$('shell').classList.add('hidden');if(e.status!==401)$('loginMsg').textContent=e.message;}else notify(e.message,true)}
}

async function refresh(){
  const revision=++S.refreshRevision;
  try{
    const [account,jobs,stats]=await Promise.all([api('/api/me'),api('/api/jobs'),api('/api/stats')]);
    if(revision!==S.refreshRevision)return;
    applyAccount(account);renderStatus();
    if(S.seen&&S.alerts&&'Notification' in window&&Notification.permission==='granted'){
      const newGood=jobs.jobs.filter(j=>!S.seen.has(j.remote_id)&&!j.demo&&j.score>=68);
      if(newGood.length)new Notification(`BidPilot: ${newGood.length} new strong matches`,{body:newGood.slice(0,2).map(j=>j.title).join(' · ')});
    }
    S.seen=new Set(jobs.jobs.map(j=>j.remote_id));
    S.jobs=jobs.jobs;
    if(S.selected){const latest=S.jobs.find(j=>j.id===S.selected.id);if(latest)S.selected={...latest};syncBidActions();}
    $('statJobs').textContent=stats.jobs;$('statStrong').textContent=stats.strong;
    $('statDraft').textContent=stats.drafts;$('statBids').textContent=stats.submitted;
    $('sideCount').textContent=stats.jobs;
    renderTopJobs();renderJobs();renderProposals();
  }catch(e){notify(e.message,true)}
}

function navigate(view){
  if(view==='team'&&S.me?.role!=='admin')view='overview';
  S.nav=view;
  document.querySelectorAll('[id^="view-"]').forEach(e=>e.classList.add('hidden'));
  const panel=$('view-'+view);if(!panel)return;
  panel.classList.remove('hidden');
  document.querySelectorAll('[data-nav]').forEach(el=>el.classList.toggle('active',el.dataset.nav===view));
  $('crumb').textContent=({overview:'Overview',jobs:'Opportunities',proposals:'My Proposals',activity:'Activity Log',settings:'AI & Bidder Settings',billing:'Subscription & Payment',team:'Team & SaaS Admin'})[view]||'Dashboard';
  if(view==='activity')loadActivity();
  if(view==='proposals'){renderProposals();loadLiveBids();}
  if(view==='team')loadTeam();
  if(view==='billing')loadBilling();
  if(view==='settings')renderSettings();
  window.scrollTo(0,0);
}

function renderStatus(){
  const u=S.me;if(!u)return;
  $('connStatus').textContent=u.connected?`@${u.freelancer_name} (${u.environment})`:'Not connected';
  $('lastScan').textContent=age(u.last_scan);
  const isVip=u.plan_tier==='free_team';
  $('planStatus').textContent=isVip?'Founder (free)':`${planName(u.plan_tier)} ($${planPrice(u.plan_tier)}/mo — ${u.subscription_status})`;
  $('planBadgeTitle').textContent=isVip?'✦ Founder (free)':`✦ ${planName(u.plan_tier)} (${String(u.subscription_status).toUpperCase()})`;
  $('planBadgeDesc').textContent=!u.subscription_active?'Subscription inactive — demo templates available':u.auto_requested?`Auto-bid requested (${u.milestone_pct||100}% milestone)`:'Manual bidding available';
  $('autoStatus').textContent=u.auto_requested&&u.auto_admin_allowed&&S.platform_auto&&u.connected&&u.subscription_active?'Enabled (worker required)':'Paused / Manual Mode';
  if($('chatStatus'))$('chatStatus').textContent=u.auto_chat_greeting&&S.platform_auto&&u.connected&&u.subscription_active?'Enabled (worker required)':'Off';
  $('aiStatus').textContent=u.subscription_active?(S.ai_provider||'Template fallback'):'Templates (subscription inactive)';
  for(const id of ['topScan','pageScan','runAutoNow','refreshLiveBidsBtn','testAiKeysBtn','testChatBtn'])$(id).disabled=!u.subscription_active;
  $('proposalAI').textContent=u.subscription_active?'✧ AI Write Proposal (Gemini / OpenAI)':'✦ Template Proposal (demo)';
  syncBidActions();
  $('connectionBanner').textContent=u.connected?`Connected to @${u.freelancer_name} (${u.environment}). Token encrypted. ${u.last_error?'Last error: '+u.last_error:''}`:'Connect your Freelancer OAuth Token below to enable live scanning, automated bidding, and auto-chat replies.';
}

function renderTopJobs(){
  const jobs=S.jobs.filter(j=>j.score>=65).slice(0,5);
  $('topJobs').innerHTML=jobs.length?jobs.map(j=>`<button class="opportunity" data-job="${esc(j.id)}"><div><div class="job-title">${esc(j.title)}</div><div class="job-sub"><span>${esc(j.country||'Global')}</span><span>${esc(usd(j))}</span>${j.bid?`<span>Bid: ${esc(j.bid.status.toUpperCase())}</span>`:''}</div></div><div class="op-right"><div class="score-chip ${scoreStyle(j.score)}">${esc(j.score)}/100</div><div class="job-sub">AI Bid →</div></div></button>`).join(''):'<div class="empty"><b>No matches loaded yet</b>Click "Load demo projects" or connect Freelancer and scan.</div>';
}

function renderJobs(){
  const q=$('jobSearch').value.toLowerCase().trim(),min=Number($('scoreFilter').value),country=$('countryFilter').value,type=$('typeFilter').value;
  let jobs=S.jobs.filter(j=>j.score>=min&&(!country||j.country_code===country)&&(type==='all'||(type==='demo')===!!j.demo)&&(!q||(j.title+' '+j.description+' '+(j.skills||[]).join(' ')+' '+j.country).toLowerCase().includes(q)));
  $('jobsTable').innerHTML=jobs.length?jobs.map(j=>`<div class="job-card"><div class="jc-top"><div><h3>${esc(j.title)}</h3><div class="job-sub">${esc(j.country||'Global')} · ${esc(j.demo?'DEMO':'Live API')} · ${esc(j.client_name||'Client')}</div></div><div><span class="score-chip ${scoreStyle(j.score)}">${esc(j.score)}/100</span></div></div><div class="tags"><span class="tag ${j.payment_verified===true?'green':'orange'}">${esc(trust(j))}</span><span class="tag">${esc(j.decision)}</span>${j.bid?`<span class="tag green">Bid: ${esc(j.bid.status)} (${esc(j.bid.source)} · ${esc(j.bid.milestone_pct||100)}% MS)</span>`:''}</div><p>${esc((j.description||'').slice(0,230))}${(j.description||'').length>230?'…':''}</p><div class="jc-bottom"><b>${esc(usd(j))}</b><button class="primary" data-job="${esc(j.id)}">AI Proposal & Bid →</button></div></div>`).join(''):'<div class="empty"><b>No opportunities match these filters</b>Try lowering the score filter or load demo projects.</div>';
}

function renderProposals(){
  const bids=S.jobs.filter(j=>j.bid);
  $('proposalsBody').innerHTML=bids.length?bids.map(j=>`<div class="proposal-row"><div><b>${esc(j.title)}</b><div class="job-sub">${esc(j.currency||'Currency unknown')} ${esc(j.bid.amount)} · ${esc(j.bid.days)} days · Milestone: ${esc(j.bid.milestone_pct||100)}% · Status: <b>${esc(j.bid.status.toUpperCase())}</b> (${esc(j.bid.source)})</div></div><button class="secondary" data-job="${esc(j.id)}">Open →</button></div>`).join(''):'<div class="empty"><b>No proposals or auto-bids yet</b>Open any job to generate an AI proposal or run Auto-Bid.</div>';
}

function safeUrl(u){try{const v=new URL(u);return v.protocol==='https:'&&!v.username&&!v.password&&['www.freelancer.com','www.freelancer-sandbox.com','freelancer.com'].includes(v.hostname)?v.href:''}catch{return ''}}

async function openJob(id){
  S.editorRevision++;S.proposalRevision++;
  const j=S.jobs.find(j=>j.id===Number(id));if(!j)return;
  S.selected=j;
  $('modalTitle').textContent=j.title;
  $('modalDesc').textContent=j.description||'No client description provided.';
  $('modalMeta').innerHTML=`<span class="tag">${esc(usd(j))}</span><span class="tag">${esc(j.country||'Global')}</span><span class="tag ${j.demo?'orange':'green'}">${j.demo?'Demo mode':'Live Freelancer API'}</span><span class="score-chip ${scoreStyle(j.score)}">${esc(j.score)}/100</span>`;
  $('modalTrust').innerHTML=`<div><b>Payment:</b> ${esc(trust(j))}</div><div><b>Employer country:</b> ${esc(j.country||'Global')}</div><div><b>Employer reviews:</b> ${esc(j.client_reviews==null?'New / 0':j.client_reviews)}</div><div><b>Employer rating:</b> ${esc(j.client_rating==null?'N/A':j.client_rating)}</div>`;
  $('modalReasons').innerHTML=(j.reasons||[]).map(x=>`<div>• ${esc(x)}</div>`).join('');
  $('bidProposal').value=j.bid?.proposal||'';
  $('bidAmount').value=j.bid?.amount??j.budget_min??S.me.min_budget;
  $('bidDays').value=j.bid?.days??S.me.default_days??3;
  $('bidMilestone').value=j.bid?.milestone_pct??S.me.milestone_pct??100;
  $('aiReviewBox').classList.add('hidden');$('aiReviewBox').textContent='';
  const alreadySent=['sending','submitted','uncertain'].includes(j.bid?.status);
  $('modalNotice').textContent=j.demo?'Demo Job: Test AI Proposal generation & saving drafts. Connect live Freelancer account for real bidding.':alreadySent?`Bid status: ${j.bid.status.toUpperCase()} (${j.bid.source}). Duplicate bids are prevented.`:'Click "⚡ 1-Click Instant Bid Now" to send immediately, or "Save draft".';
  const url=safeUrl(j.url);
  $('visitProject').href=url||'#';$('visitProject').classList.toggle('hidden',!url);
  syncBidActions();
  $('jobModal').classList.remove('hidden');
  if(!$('bidProposal').value){await makeProposal(true)}
}

function closeJob(){
  $('jobModal').classList.add('hidden');$('confirmModal').classList.add('hidden');S.selected=null;S.intent=null;S.editorRevision++;S.proposalRevision++;
}
function readBidEditor(){return {amount:Number($('bidAmount').value),days:Number($('bidDays').value),milestone_pct:Number($('bidMilestone').value||100),proposal:$('bidProposal').value};}
function syncBidActions(){
  const j=S.selected,u=S.me;
  const sent=['sending','submitted','uncertain'].includes(j?.bid?.status);
  const missingUnits=!!j&&!j.demo&&(!/^[A-Z]{3}$/.test(j.currency||'')||!['fixed','hourly'].includes(j.type));
  const disabled=!j||!u||j.demo||missingUnits||!u.connected||!u.subscription_active||sent||S.bidBusy;
  if(j)$('modalNotice').textContent=missingUnits?'This live project is missing its currency or pricing type. Scan again before bidding.':j.demo?'Demo project: generate proposals and save local drafts. Real bidding requires an active subscription and Freelancer connection.':sent?`Bid status: ${String(j.bid.status).toUpperCase()}. Check Freelancer before any further action.`:'Review the proposal, amount and listing currency before submitting a bid.';
  $('prepareBid').disabled=disabled;$('instantBidBtn').disabled=disabled;
  $('saveDraft').disabled=!j||sent||S.bidBusy;
  $('aiReviewBtn').disabled=!u?.subscription_active||S.bidBusy;
  $('proposalAI').disabled=S.bidBusy;$('proposalTemplate').disabled=S.bidBusy;
}

async function makeProposal(ai){
  if(!S.selected)return;
  try{
    const selectedId=S.selected.id,revision=++S.proposalRevision,editorRevision=S.editorRevision;
    const previous={proposal:$('bidProposal').value,amount:$('bidAmount').value,days:$('bidDays').value,milestone:$('bidMilestone').value};
    const useCloud=ai&&S.me.subscription_active;
    const x=await post(`/api/jobs/${selectedId}/${useCloud?'ai-proposal':'proposal'}`);
    if(S.selected?.id!==selectedId||revision!==S.proposalRevision||editorRevision!==S.editorRevision)return;
    if($('bidProposal').value===previous.proposal)$('bidProposal').value=x.proposal;
    if(x.suggested_amount&&$('bidAmount').value===previous.amount)$('bidAmount').value=x.suggested_amount;
    if(x.suggested_days&&$('bidDays').value===previous.days)$('bidDays').value=x.suggested_days;
    if(x.suggested_milestone&&$('bidMilestone').value===previous.milestone)$('bidMilestone').value=x.suggested_milestone;
    notify(useCloud?'Proposal generated. Review it before bidding; a template is used if AI is unavailable.':'Template proposal generated. Review and personalize it.');
  }catch(e){notify(e.message,true)}
}

async function saveDraft(){
  if(!S.selected)return false;
  try{
    await post(`/api/jobs/${S.selected.id}/draft`,{
      amount:Number($('bidAmount').value),
      days:Number($('bidDays').value),
      milestone_pct:Number($('bidMilestone').value||100),
      proposal:$('bidProposal').value
    });
    notify('Proposal draft saved');await refresh();return true;
  }catch(e){notify(e.message,true);return false}
}

async function instantBid(){
  if(!S.selected||S.bidBusy)return;
  const j=S.selected,revision=S.editorRevision,epoch=S.epoch,payload=readBidEditor();
  if(j.demo){notify('Demo projects only support local drafts',true);return}
  if(payload.proposal.length<30){notify('Generate or enter a proposal of at least 30 characters before submitting.',true);return}
  S.bidBusy=true;syncBidActions();
  try{
    const x=await post(`/api/jobs/${j.id}/instant-bid`,payload);
    if(S.selected?.id===j.id&&S.editorRevision===revision)closeJob();
    notify(`Freelancer confirmed bid submission (ID: ${x.bid_id||'confirmed'})`);
  }catch(e){notify(e.message,true)}finally{
    if(epoch===S.epoch){S.bidBusy=false;await refresh();syncBidActions();}
  }
}

async function prepareBid(){
  if(!S.selected||S.bidBusy)return;
  const j=S.selected,revision=S.editorRevision,epoch=S.epoch,payload=readBidEditor();
  if(j.demo){notify('Demo cannot submit a real bid',true);return}
  S.bidBusy=true;syncBidActions();
  try{
    await post(`/api/jobs/${j.id}/draft`,payload);
    if(S.selected?.id!==j.id||revision!==S.editorRevision)return;
    if(JSON.stringify(readBidEditor())!==JSON.stringify(payload)){notify('Draft saved. Review your new edits and prepare again.');return}
    const x=await post(`/api/jobs/${j.id}/prepare`);
    if(S.selected?.id!==j.id||revision!==S.editorRevision)return;
    S.intent={id:j.id,token:x.intent};
    $('confirmText').textContent=`${x.preview.title} · ${x.preview.currency} ${x.preview.amount} · ${x.preview.days} days · Milestone ${x.preview.milestone_pct||100}% · ${x.preview.environment}`;
    $('confirmProposal').textContent=x.preview.proposal;
    $('confirmation').value='';$('confirmSubmit').disabled=true;
    $('confirmModal').classList.remove('hidden');
  }catch(e){notify(e.message,true)}finally{if(epoch===S.epoch){S.bidBusy=false;syncBidActions();}}
}

async function confirmBid(){
  if(!S.intent||S.bidBusy)return;
  const {id,token}=S.intent;
  const epoch=S.epoch;S.bidBusy=true;syncBidActions();
  $('confirmSubmit').disabled=true;
  try{
    await post(`/api/jobs/${id}/submit`,{intent:token,confirmation:$('confirmation').value});
    if(S.intent?.token===token)closeJob();
    notify('Freelancer API confirmed bid submission');
  }catch(e){notify(e.message,true);$('confirmModal').classList.add('hidden')}
  if(epoch===S.epoch){S.intent=null;S.bidBusy=false;await refresh();syncBidActions();}
}

async function loadActivity(){
  try{
    let a=await api('/api/audit');
    $('activityBody').innerHTML=a.events.length?a.events.map(e=>`<div class="activity-row"><div><b>${esc(e.action.replaceAll('_',' '))}</b><div class="job-sub">${esc(e.message)}</div></div><small>${esc(age(e.created))}</small></div>`).join(''):'<div class="empty">No activities yet</div>';
  }catch(e){notify(e.message,true)}
}

function renderSettings(){
  if(!S.me)return;
  const u=S.me,form=$('settingsForm');
  for(const k of ['name','skills','search_terms','excluded_keywords','countries','portfolio','description','proposal_tone','bid_strategy','default_days','milestone_pct','chat_greeting_template','min_score','min_budget','max_bid','max_daily','max_hourly']){
    if(form.elements[k])form.elements[k].value=u[k]??'';
  }
  for(const chk of ['auto_chat_greeting','allow_hourly','allow_any_currency','require_payment_verified','require_country_match','auto_requested']){
    if(form.elements[chk])form.elements[chk].checked=!!u[chk];
  }
  const aiForm=$('aiKeysForm');
  if(aiForm){
    aiForm.elements.preferred_ai.value=u.preferred_ai||'auto';
    aiForm.elements.gemini_model.value=u.gemini_model||'gemini-2.5-flash';
    aiForm.elements.openai_model.value=u.openai_model||'gpt-4.1-mini';
    $('geminiMaskBadge').textContent=u.has_gemini_key?`(Saved: ${u.gemini_key_masked})`:'(Not set)';
    $('openaiMaskBadge').textContent=u.has_openai_key?`(Saved: ${u.openai_key_masked})`:'(Not set)';
    $('aiKeysBanner').innerHTML=`<b>Active AI Provider:</b> ${esc(S.ai_provider)} · Gemini Key: <b>${u.has_gemini_key?esc(u.gemini_key_masked):'Not Set'}</b> · ChatGPT Key: <b>${u.has_openai_key?esc(u.openai_key_masked):'Not Set'}</b>`;
  }
  renderStatus();
}

async function loadBilling(){
  try{
    const x=await api('/api/billing');
    if(S.me&&typeof x.subscription_active==='boolean'){Object.assign(S.me,{plan_tier:x.plan_tier,subscription_status:x.subscription_status,subscription_expires:x.subscription_expires,subscription_active:x.subscription_active});renderStatus();}
    const isFree=x.plan_tier==='free_team';
    $('myPlanBanner').innerHTML=isFree
      ?`<b>✦ Founder account (free)</b><p>No subscription fee. Your configured bidding limits and Freelancer account limits still apply.</p>`
      :`<b>✦ ${esc(planName(x.plan_tier))} ($${planPrice(x.plan_tier)}/month — ${esc(String(x.subscription_status).toUpperCase())})</b><p>Starter: $10/month, 150 bids per billing period. Pro: $40/month, no monthly app quota; your hourly, daily and Freelancer limits apply. ${x.subscription_expires?'Access expires: '+esc(age(x.subscription_expires))+'. ':''}Submit a receipt for the selected package; an administrator verifies it before activation.</p>`;
    const pm=S.payment_methods||{};
    const stripe=safePaymentUrl(pm.stripe_url);
    const methods=[['JazzCash',pm.jazzcash],['EasyPaisa',pm.easypaisa],['Bank transfer',pm.bank]].filter(([,value])=>value);
    $('paymentMethodsBox').innerHTML=methods.map(([label,value])=>`<div><b>${esc(label)}:</b> ${esc(value)}</div>`).join('')+(stripe?`<div><b>Card payment:</b> <a href="${esc(stripe)}" target="_blank" rel="noopener noreferrer">Open payment provider ↗</a></div>`:'')||'Payment details are not configured. Contact the administrator before sending money.';
    const methodSelect=$('paymentForm').elements.method;
    for(const option of methodSelect.options||[])option.disabled=!({jazzcash:pm.jazzcash,easypaisa:pm.easypaisa,bank:pm.bank,stripe}[option.value]);
    if(methodSelect.selectedOptions?.[0]?.disabled){const available=Array.from(methodSelect.options).find(option=>!option.disabled);methodSelect.value=available?.value||'';}
    $('paymentForm').querySelector?.('button[type="submit"]')?.toggleAttribute('disabled',!methods.length&&!stripe);
    const wa=String(pm.whatsapp||'').replace(/[^0-9]/g,'');
    $('whatsappApproveBtn').classList.toggle('hidden',!wa);
    if(wa)$('whatsappApproveBtn').href=`https://wa.me/${wa}?text=${encodeURIComponent(`Salam! I submitted a BidPilot subscription payment for account ${S.me.email}. Please verify receipt.`)}`;
    $('myPaymentsList').innerHTML=x.payments.length?x.payments.map(p=>`<div class="activity-row"><div><b>${esc(planName(p.plan_tier))} · ${esc(p.method)} — ${esc(p.currency)} ${esc(p.amount)}</b><div class="job-sub">TID: ${esc(p.transaction_id)} · Status: <span class="tag ${p.status==='approved'?'green':'orange'}">${esc(p.status.toUpperCase())}</span></div></div><small>${esc(age(p.created))}</small></div>`).join(''):'<div class="empty">No payment submissions yet</div>';
  }catch(e){notify(e.message,true)}
}

async function loadTeam(){
  if(S.me?.role!=='admin')return;
  try{
    const [x,payData]=await Promise.all([api('/api/team'),api('/api/admin/payments')]);
    S.team=x.members;
    const b=x.billing_summary||{};
    $('billingSummaryBox').innerHTML=`<b>SaaS Billing & Team Overview:</b> ${esc(b.free_team_count||0)} Founder Accounts (maximum 2) • ${esc(b.paid_subscribers_count||0)} Commercial Paid Users • <b>Pending Payment Approvals: ${esc(b.pending_payments||0)}</b> • <b>Estimated Monthly Revenue (MRR): $${esc(b.estimated_mrr_usd||0)}/mo</b>`;
    $('adminPaymentsList').innerHTML=payData.payments.length?payData.payments.map(p=>`<div class="member-row"><div><b>${esc(p.user_name)} (${esc(p.user_email)})</b> <span class="tag ${p.status==='approved'?'green':'orange'}">${esc(p.status.toUpperCase())}</span><small>${esc(p.method)} · ${esc(p.currency)} ${esc(p.amount)} · TID: <b>${esc(p.transaction_id)}</b> ${p.sender_phone?'· Phone: '+esc(p.sender_phone):''} ${p.notes?'· '+esc(p.notes):''}</small></div><div class="member-actions">${p.status==='pending'?`<button class="primary" data-paydecide="${esc(p.id)}" data-act="approve">✓ Approve & Activate</button><button class="secondary danger" data-paydecide="${esc(p.id)}" data-act="reject">✕ Reject</button>`:`<small>${esc(age(p.created))}</small>`}</div></div>`).join(''):'<div class="empty">No payment requests submitted yet</div>';
    $('teamList').innerHTML=x.members.map(m=>{
      const isFree=m.plan_tier==='free_team';
      return `<div class="member-row"><div><b>${esc(m.name)}</b> <span class="tag ${isFree?'green':'orange'}">${isFree?'FOUNDER (free)':`${esc(planName(m.plan_tier))} ($${planPrice(m.plan_tier)}/mo · ${esc(m.subscription_status)})`}</span><small>${esc(m.email)} · ${m.connected?'Freelancer Connected':'Not Connected'} · Auto-Bid: ${m.auto_admin_allowed&&m.auto_requested?'ON':'OFF'}</small></div><div class="member-actions"><button class="secondary" data-inspect="${esc(m.id)}">Activity</button><button class="secondary" data-toggleplan="${esc(m.id)}" data-tier="${m.plan_tier==='paid_starter'?'paid_pro':'paid_starter'}">${m.plan_tier==='paid_starter'?'Switch to Pro ($40)':'Switch to Starter ($10)'}</button>${m.id===S.me.id?'':`<button class="secondary" data-permission="${esc(m.id)}" data-value="${m.auto_admin_allowed?'0':'1'}">${m.auto_admin_allowed?'Pause Auto':'Allow Auto'}</button><button class="secondary" data-active="${esc(m.id)}" data-value="${m.active?'0':'1'}">${m.active?'Disable':'Enable'}</button>`}</div></div>`;
    }).join('')||'<div class="empty">No members</div>';
  }catch(e){notify(e.message,true)}
}

async function inspectMember(id){
  if(S.me?.role!=='admin')return;
  try{
    const x=await api(`/api/team/${encodeURIComponent(id)}/overview`);
    const m=x.member;
    $('teamOverview').classList.remove('hidden');
    $('teamOverview').innerHTML=`<div class="panel-heading"><div><div class="eyebrow">ADMIN READ-ONLY REVIEW</div><h3>${esc(m.name)} — ${esc(m.email)} (${esc(planName(m.plan_tier))})</h3></div></div>`+
      `<p>${esc(x.counts.jobs)} jobs • ${esc(x.counts.bids)} total bids • ${esc(x.counts.submitted)} submitted</p>`+
      `<h3>Recent projects and bids</h3>`+
      (x.jobs.length?x.jobs.map(j=>`<div class="activity-row"><div><b>${esc(j.title)}</b><div class="job-sub">${esc(j.demo?'DEMO':'API')} • ${esc(j.score)}/100 • ${esc(j.bid_status)}${j.bid_amount==null?'':' • '+esc(j.currency)+' '+esc(j.bid_amount)}</div></div></div>`).join(''):'<div class="empty">No jobs yet</div>')+
      `<h3>Recent account activity</h3>`+
      (x.activity.length?x.activity.map(a=>`<div class="activity-row"><div><b>${esc(a.action.replaceAll('_',' '))}</b><div class="job-sub">${esc(a.message)}</div></div><small>${esc(age(a.created))}</small></div>`).join(''):'<div class="empty">No activities yet</div>');
  }catch(e){notify(e.message,true)}
}

async function manualScan(){
  const btns=[$('pageScan'),$('topScan')];btns.forEach(b=>b.disabled=true);
  try{
    const x=await post('/api/scan');
    notify(`Scan complete: ${x.found} jobs, ${x.new} new, ${x.auto_submitted||0} Auto-Bids, ${x.chat_replied||0} Auto-Chats sent!`);
    await refresh();S.me=(await api('/api/me')).user;renderStatus();
  }catch(e){notify(e.message,true)}finally{if(S.me)renderStatus()}
}

$('runAutoNow').onclick=async()=>{
  $('runAutoNow').disabled=true;
  try{
    const x=await post('/api/autobid/run');
    notify(`Sweep Complete: ${x.submitted||0} AI bids & ${x.chat_replied||0} Auto-Chat replies sent!${x.reason?' ('+x.reason+')':''}`);
    await refresh();
  }catch(e){notify(e.message,true)}finally{if(S.me)renderStatus()}
};

if($('aiKeysForm')){
  $('aiKeysForm').onsubmit=async e=>{
    e.preventDefault();
    const f=e.currentTarget;
    try{
      const x=await post('/api/ai-keys',{
        preferred_ai:f.elements.preferred_ai.value,
        gemini_key:f.elements.gemini_key.value,
        gemini_model:f.elements.gemini_model.value,
        openai_key:f.elements.openai_key.value,
        openai_model:f.elements.openai_model.value,
        clear_gemini:f.elements.clear_gemini.checked,
        clear_openai:f.elements.clear_openai.checked
      });
      f.elements.gemini_key.value='';f.elements.openai_key.value='';f.elements.clear_gemini.checked=false;f.elements.clear_openai.checked=false;
      S.me=x.user;S.ai_provider=x.ai_provider;
      renderSettings();
      notify('Gemini & ChatGPT AI Keys encrypted and saved!');
    }catch(ex){notify(ex.message,true)}
  };
}

if($('testAiKeysBtn')){
  $('testAiKeysBtn').onclick=async()=>{
    $('testAiKeysBtn').disabled=true;
    try{
      const x=await post('/api/ai-keys/test',{provider:'both'});
      const g=x.results?.gemini||{},o=x.results?.openai||{};
      $('aiTestResultBox').innerHTML=`<div><b>Google Gemini (${esc(g.model||'gemini')}):</b> <span class="tag ${g.ok?'green':'orange'}">${g.ok?'CONNECTED ✓':'NOT ACTIVE'}</span> ${esc(g.message||'')}</div><div class="mt-6"><b>OpenAI ChatGPT (${esc(o.model||'gpt')}):</b> <span class="tag ${o.ok?'green':'orange'}">${o.ok?'CONNECTED ✓':'NOT ACTIVE'}</span> ${esc(o.message||'')}</div>`;
      $('aiTestResultBox').classList.remove('hidden');
    }catch(ex){notify(ex.message,true)}finally{if(S.me)renderStatus()}
  };
}

if($('testChatBtn')){
  $('testChatBtn').onclick=async()=>{
    try{
      const x=await post('/api/chat/preview',{client_name:'David M.',project_title:'AI Video Ads & Reels Editing',client_message:'Hi! We liked your proposal. Can you share a quick sample and let us know if you can start today?'});
      $('testChatBox').textContent='AI Chat Reply: '+x.reply;
      $('testChatBox').classList.remove('hidden');
    }catch(e){notify(e.message,true)}
  };
}

if($('paymentForm')){
  $('paymentForm').onsubmit=async e=>{
    e.preventDefault();
    const f=e.currentTarget;
    try{
      await post('/api/billing/submit',{
        plan_tier:f.elements.plan_tier.value,
        method:f.elements.method.value,
        amount:planPrice(f.elements.plan_tier.value),
        currency:'USD',
        transaction_id:f.elements.transaction_id.value,
        sender_phone:f.elements.sender_phone.value,
        notes:f.elements.notes.value
      });
      f.elements.transaction_id.value='';f.elements.notes.value='';
      notify('Payment receipt submitted for administrator verification.');
      await loadBilling();
    }catch(ex){notify(ex.message,true)}
  };
}

$('authToggle').onclick=()=>{if(!S.authBusy)setAuthMode(!S.registering)};
$('loginForm').onsubmit=async e=>{
  e.preventDefault();if(S.authBusy)return;S.authBusy=true;$('authSubmit').disabled=true;$('authToggle').disabled=true;
  const credentials={email:$('loginEmail').value.trim(),password:$('loginPassword').value};
  try{
    if(S.registering){await post('/api/register',{...credentials,name:$('registerName').value.trim()});setAuthMode(false);}
    await post('/api/login',credentials);
    $('loginMsg').textContent='';$('loginPassword').value='';await initialize();
  }catch(ex){if(ex.message!=='Session changed')$('loginMsg').textContent=ex.message}finally{S.authBusy=false;$('authSubmit').disabled=false;$('authToggle').disabled=false}
};
$('logout').onclick=async()=>{try{await post('/api/logout');resetSession()}catch(e){notify(e.message,true)}};
$('findJobs').onclick=()=>navigate('jobs');$('topScan').onclick=manualScan;$('pageScan').onclick=manualScan;
$('demoButton').onclick=async()=>{try{const x=await post('/api/demo');notify(`${x.new} demo opportunities loaded`);await refresh();navigate('jobs')}catch(e){notify(e.message,true)}};
document.querySelectorAll('[data-nav]').forEach(b=>b.onclick=()=>navigate(b.dataset.nav));
for(const id of ['jobSearch','scoreFilter','countryFilter','typeFilter'])$(id).addEventListener(id==='jobSearch'?'input':'change',renderJobs);

document.addEventListener('click',async e=>{
  const target=e.target.closest('[data-job],[data-permission],[data-active],[data-inspect],[data-toggleplan],[data-paydecide]');
  if(!target)return;
  if(target.dataset.job){openJob(target.dataset.job);return}
  if(target.dataset.inspect){await inspectMember(target.dataset.inspect);return}
  try{
    if(target.dataset.paydecide){
      await post(`/api/admin/payments/${target.dataset.paydecide}/decide`,{action:target.dataset.act});
      notify(`Payment ${target.dataset.act==='approve'?'Approved & Subscription Activated':'Rejected'}!`);
    }
    if(target.dataset.toggleplan){
      const newTier=target.dataset.tier;
      await post(`/api/team/${target.dataset.toggleplan}/plan`,{plan_tier:newTier,subscription_status:'active',monthly_fee:planPrice(newTier)});
      notify(`Updated member plan to ${planName(newTier)} ($${planPrice(newTier)}/month)`);
    }
    if(target.dataset.permission){await post(`/api/team/${target.dataset.permission}/permission`,{enabled:target.dataset.value==='1'});notify('Member auto-bid permission updated')}
    if(target.dataset.active){await post(`/api/team/${target.dataset.active}/active`,{enabled:target.dataset.value==='1'});notify('Member status updated')}
    await loadTeam();
  }catch(ex){notify(ex.message,true)}
});

$('aiReviewBtn').onclick=async()=>{if(!S.selected)return;const id=S.selected.id,revision=S.editorRevision;try{let x=await post(`/api/jobs/${id}/ai-review`);if(S.selected?.id!==id||revision!==S.editorRevision)return;$('aiReviewBox').textContent=x.review;$('aiReviewBox').classList.remove('hidden')}catch(e){notify(e.message,true)}};
$('alertBtn').onclick=async()=>{if(!('Notification' in window)){notify('Browser notifications not supported',true);return}const p=await Notification.requestPermission();S.alerts=p==='granted';notify(S.alerts?'Desktop alerts activated for new matches':'Notification permission denied',!S.alerts)};
$('modalClose').onclick=closeJob;$('jobModal').addEventListener('click',e=>{if(e.target===$('jobModal'))closeJob()});
$('proposalTemplate').onclick=()=>makeProposal(false);$('proposalAI').onclick=()=>makeProposal(true);
$('saveDraft').onclick=saveDraft;$('instantBidBtn').onclick=instantBid;$('prepareBid').onclick=prepareBid;
$('confirmation').oninput=()=>{$('confirmSubmit').disabled=$('confirmation').value!=='PLACE BID'};
$('cancelConfirm').onclick=()=>{$('confirmModal').classList.add('hidden');S.intent=null};
$('confirmSubmit').onclick=confirmBid;
$('proposalCopy').onclick=async()=>{try{await navigator.clipboard.writeText($('bidProposal').value);notify('Proposal copied to clipboard')}catch{notify('Select text manually to copy',true)}};

$('settingsForm').onsubmit=async e=>{
  e.preventDefault();
  const form=e.currentTarget,data={};
  for(const k of ['name','skills','search_terms','excluded_keywords','countries','portfolio','description','proposal_tone','bid_strategy','chat_greeting_template'])data[k]=form.elements[k].value;
  for(const k of ['default_days','milestone_pct','min_score','min_budget','max_bid','max_daily','max_hourly'])data[k]=Number(form.elements[k].value);
  for(const chk of ['auto_chat_greeting','allow_hourly','allow_any_currency','require_payment_verified','require_country_match','auto_requested'])data[chk]=form.elements[chk].checked;
  try{
    const x=await post('/api/settings',data);
    S.me=x.user;renderSettings();notify('Bidman Pro settings, Milestones & Auto-Chat saved!');await refresh();
  }catch(e){notify(e.message,true)}
};

$('connectForm').onsubmit=async e=>{
  e.preventDefault();
  try{
    const x=await post('/api/connect',{environment:$('connectEnv').value,token:$('connectToken').value});
    $('connectToken').value='';S.me=(await api('/api/me')).user;renderSettings();
    notify(`Connected @${x.freelancer_name}; imported ${x.skills_imported.length} skills`);
  }catch(e){notify(e.message,true)}
};

$('disconnect').onclick=async()=>{if(!confirm('Disconnect your Freelancer API account?'))return;try{await post('/api/disconnect');S.me=(await api('/api/me')).user;renderSettings();notify('Connection removed')}catch(e){notify(e.message,true)}};

$('memberForm').onsubmit=async e=>{
  e.preventDefault();
  const f=e.currentTarget;
  try{
    const x=await post('/api/team',{name:f.elements.name.value,email:f.elements.email.value,password:f.elements.password.value,plan_tier:f.elements.plan_tier.value,monthly_fee:Number(f.elements.monthly_fee.value||0),subscription_status:'active'});
    f.reset();f.elements.monthly_fee.value=planPrice(f.elements.plan_tier.value);notify(`Account created: ${x.member.email} (${x.member.plan_tier})`);await loadTeam();
  }catch(ex){notify(ex.message,true)}
};

document.addEventListener('keydown',e=>{if(e.key==='Escape')closeJob()});
setInterval(()=>{if(S.me&&!document.hidden)refresh()},30000);
(async()=>{await initialize()})();


async function loadLiveBids(){
  const box=$('liveBidsBody');
  if(!box)return;
  if(!S.me?.subscription_active){box.innerHTML='<div class="empty"><b>Activate a subscription to sync live Freelancer bids</b></div>';return;}
  box.innerHTML='<div class="empty"><b>Fetching live bids from Freelancer...</b></div>';
  try{
    const res=await api('/api/live-bids');
    if(!res.connected){
      box.innerHTML='<div class="empty"><b>Freelancer account not connected</b><p>Connect your personal OAuth token in Settings to view live bids placed on Freelancer.</p></div>';
      return;
    }
    const arr=res.bids||[];
    box.innerHTML=arr.length?arr.map(b=>`<div class="opportunity"><div><div class="job-title">${esc(b.project_title)}</div><div class="job-sub">Amount: <b>${esc(b.currency||'Currency unknown')} ${esc(b.amount)}</b> · Period: ${esc(b.period)} days · Milestone: ${esc(b.milestone_percentage||100)}% · Status: <span class="tag green">${esc(String(b.status||'unknown').toUpperCase())}</span></div></div><div class="op-right"><a href="https://www.freelancer.com/projects/${esc(b.project_id)}" target="_blank" rel="noopener" class="secondary small-link">View →</a></div></div>`).join(''):'<div class="empty"><b>No active bids found on Freelancer</b><p>Any bid placed through the app or directly on Freelancer will appear here.</p></div>';
  }catch(e){
    box.innerHTML=`<div class="empty"><b>Could not fetch live bids</b><p>${esc(e.message)}</p></div>`;
  }
}
if($('refreshLiveBidsBtn'))$('refreshLiveBidsBtn').onclick=loadLiveBids;

function updatePaymentAmount(){const f=$('paymentForm');if(f.elements.currency.value==='USD')f.elements.amount.value=planPrice(f.elements.plan_tier.value);}
$('paymentPlan').onchange=updatePaymentAmount;
$('paymentForm').elements.currency.onchange=updatePaymentAmount;
$('memberForm').elements.plan_tier.onchange=()=>{$('memberForm').elements.monthly_fee.value=planPrice($('memberForm').elements.plan_tier.value)};
