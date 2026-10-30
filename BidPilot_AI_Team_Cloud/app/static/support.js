'use strict';
(()=>{
  const get=id=>document.getElementById(id);
  const form=get('supportForm');
  if(!form)return;
  const log=get('supportMessages'),question=get('supportQuestion'),provider=get('supportProvider');
  const mode=get('supportMode'),send=get('supportSend'),clear=get('supportClear'),notice=get('supportNotice');
  const copy={
    general:{title:'A place for your next idea.',description:'Ask a question, write something, explore an idea, or work through code.',
      welcome:'What would you like to work on?',detail:'Bring a question, a rough idea, or a piece of code. Your selected Gemini or OpenAI model will help you think it through.',
      notice:'Use Gemini or OpenAI for general questions, writing and coding. Keep passwords, API keys and private customer data out of chat.',
      placeholder:'Ask a question, describe an idea, or paste code without secrets…',hint:'Conversation stays in this tab. Clear chat to start fresh.',
      starters:['Help me turn a rough idea into a clear plan.','Write a professional follow-up email.','Explain a coding concept with a simple example.']},
    support:{title:'A little guidance, right when you need it.',description:'Get help with BidPilot setup, API errors, plans and bidding settings.',
      welcome:'How can I help with BidPilot?',detail:'Describe what you were doing and the error you saw. App help can explain settings and errors, while you stay in control of account actions.',
      notice:'App help may use a limited snapshot of your own workspace settings. Keep passwords and API keys out of chat.',
      placeholder:'Describe the issue or paste an error without secrets…',hint:'App help offers guidance. You control your account actions.',
      starters:['Why is automatic bidding paused?','How do I save and test my AI keys?','How do working hours and bid limits work?']},
  };
  const chat={messages:[],busy:false,revision:0,owner:'',mode:'general',cancel:null};
  const identity=()=>S.me?`${S.epoch}:${S.me.id}`:'';
  const selectedMode=()=>mode.value==='support'?'support':'general';
  const chips=[...document.querySelectorAll('[data-support-question]')];

  function controls(){
    send.disabled=chat.busy||!S.me;
    send.textContent=chat.busy?'Thinking…':'Send message';
    form.setAttribute('aria-busy',String(chat.busy));
  }
  function render(){
    const content=copy[chat.mode];
    get('supportIntroTitle').textContent=content.title;
    get('supportIntroDescription').textContent=content.description;
    get('supportComposerHint').textContent=content.hint;
    question.placeholder=content.placeholder;
    chips.forEach((button,index)=>{
      button.dataset.supportQuestion=content.starters[index%content.starters.length];
      button.textContent=button.dataset.supportQuestion;
    });
    log.setAttribute('aria-label',chat.mode==='general'?'AI conversation':'App help conversation');
    log.replaceChildren();
    if(!chat.messages.length){
      const empty=document.createElement('div');empty.className='support-welcome';
      const heading=document.createElement('h3');heading.textContent=content.welcome;
      const description=document.createElement('p');description.textContent=content.detail;
      empty.append(heading,description);log.append(empty);
    }
    for(const entry of chat.messages){
      const message=document.createElement('article');
      message.className='support-message '+(entry.role==='user'?'user':'assistant');
      const label=document.createElement('small');label.className='support-message-label';
      label.textContent=entry.role==='user'?'You':entry.label;
      const text=document.createElement('p');text.className='support-message-text';text.textContent=entry.content;
      message.append(label,text);log.append(message);
    }
    log.scrollTop=log.scrollHeight;controls();
  }
  function reset({resetChoices=false,keepDraft=false}={}){
    chat.revision++;chat.cancel?.abort();
    if(resetChoices){mode.value='general';provider.value='auto'}
    chat.messages=[];chat.busy=false;chat.owner=identity();chat.mode=selectedMode();chat.cancel=null;
    if(!keepDraft)question.value='';
    notice.textContent=copy[chat.mode].notice;render();
  }
  function checkOwner(){if(chat.owner!==identity())reset({resetChoices:true});}
  window.clearSupportChat=()=>reset({resetChoices:true});
  clear.onclick=()=>reset();
  mode.onchange=()=>{checkOwner();reset({keepDraft:true})};
  chips.forEach(button=>button.addEventListener('click',()=>{
    checkOwner();question.value=String(button.dataset.supportQuestion||'').slice(0,3000);question.focus();
  }));
  document.querySelectorAll('[data-nav="support"]').forEach(button=>button.addEventListener('click',()=>{checkOwner();controls()}));
  form.onsubmit=async event=>{
    event.preventDefault();checkOwner();
    if(chat.busy)return;
    if(!S.me){notice.textContent='Sign in to use AI Chat.';return;}
    const original=question.value,text=original.trim();
    if(!text||text.length>3000){notice.textContent='Enter a message between 1 and 3,000 characters.';return;}
    const selected=['auto','gemini','openai'].includes(provider.value)?provider.value:'auto';
    const history=chat.messages.filter(entry=>entry.context!==false).slice(-10).map(entry=>({role:entry.role,content:entry.content.slice(0,2000)}));
    const owner=identity(),revision=++chat.revision,requestedMode=chat.mode;
    const controller=typeof AbortController==='function'?new AbortController():null;
    chat.cancel=controller;chat.busy=true;
    chat.messages=[...chat.messages,{role:'user',content:text,turn:revision}].slice(-10);
    if(question.value===original)question.value='';
    notice.textContent='Getting an answer from your selected AI connection…';render();
    const timeout=controller?setTimeout(()=>controller.abort(),90000):null;
    try{
      const result=await api('/api/support/chat',{method:'POST',body:JSON.stringify({question:text,provider:selected,mode:requestedMode,history,expected_user_id:S.me.id}),...(controller?{signal:controller.signal}:{})});
      if(owner!==identity()||revision!==chat.revision||requestedMode!==chat.mode)return;
      if(!result||typeof result.reply!=='string'||!result.reply.trim())throw Error('The chat service returned an unreadable answer. Try again.');
      const live=result.ai_used===true&&['gemini','openai'].includes(result.provider)&&typeof result.model==='string'&&result.model.trim();
      const label=live?`${result.provider==='gemini'?'Gemini':'OpenAI'} · ${result.model.slice(0,120)}`:requestedMode==='general'?'AI unavailable':'Local help';
      if(!live&&requestedMode==='general')chat.messages.forEach(entry=>{if(entry.turn===revision)entry.context=false});
      chat.messages=[...chat.messages,{role:'assistant',content:result.reply.slice(0,requestedMode==='general'?8000:4000),label,context:live||requestedMode==='support'}].slice(-10);
      notice.textContent=typeof result.notice==='string'&&result.notice.trim()?result.notice.slice(0,2000):
        (live?'Answer generated using the provider shown below.':requestedMode==='general'?'No live AI answer was generated. Check your saved AI connection in Settings.':'Local guidance shown. Live AI was not used for this answer.');
      render();
    }catch(error){
      if(owner!==identity()||revision!==chat.revision||requestedMode!==chat.mode)return;
      if(error?.status===409){
        reset({resetChoices:true});notice.textContent='Your account changed. Reload the app before continuing this chat.';
        window.location?.reload?.();return;
      }
      chat.messages=chat.messages.filter(entry=>entry.turn!==revision);render();
      notice.textContent=error?.message&&error.message!=='Session changed'?String(error.message).slice(0,1000):'Your session changed. Sign in again to continue.';
      if(!question.value)question.value=original;
    }finally{
      if(timeout!==null)clearTimeout(timeout);
      if(owner===identity()&&revision===chat.revision){chat.busy=false;chat.cancel=null;controls();}
    }
  };
  reset({resetChoices:true});
})();
