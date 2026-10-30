'use strict';
(()=>{
  const form=document.getElementById('supportForm');
  if(!form)return;
  const log=document.getElementById('supportMessages');
  const question=document.getElementById('supportQuestion');
  const provider=document.getElementById('supportProvider');
  const send=document.getElementById('supportSend');
  const clear=document.getElementById('supportClear');
  const notice=document.getElementById('supportNotice');
  const initialNotice='Ask about setup, API errors, plans, or bidding settings. Keep passwords and API keys out of this chat.';
  const chat={messages:[],busy:false,revision:0,owner:''};
  const identity=()=>S.me?`${S.epoch}:${S.me.id}`:'';

  function controls(){
    send.disabled=chat.busy||!S.me;
    send.textContent=chat.busy?'Getting an answer…':'Send question';
    form.setAttribute('aria-busy',String(chat.busy));
  }
  function render(){
    log.replaceChildren();
    if(!chat.messages.length){
      const empty=document.createElement('div');
      empty.className='support-welcome';
      const heading=document.createElement('h3');
      heading.textContent='How can I help?';
      const description=document.createElement('p');
      description.textContent='Describe what you were doing and the error you saw. This chat explains the app and cannot place bids or change settings.';
      empty.append(heading,description);log.append(empty);
    }
    for(const entry of chat.messages){
      const message=document.createElement('article');
      message.className='support-message '+(entry.role==='user'?'user':'assistant');
      const label=document.createElement('small');
      label.className='support-message-label';
      label.textContent=entry.role==='user'?'You':entry.label;
      const text=document.createElement('p');
      text.className='support-message-text';
      text.textContent=entry.content;
      message.append(label,text);log.append(message);
    }
    log.scrollTop=log.scrollHeight;
    controls();
  }
  function reset(){
    chat.revision++;
    chat.messages=[];chat.busy=false;chat.owner=identity();
    question.value='';provider.value='auto';notice.textContent=initialNotice;
    render();
  }
  function checkOwner(){
    if(chat.owner!==identity())reset();
  }
  window.clearSupportChat=reset;
  clear.onclick=reset;
  document.querySelectorAll('[data-support-question]').forEach(button=>{
    button.addEventListener('click',()=>{
      checkOwner();
      question.value=String(button.dataset.supportQuestion||'').slice(0,3000);
      question.focus();
    });
  });
  document.querySelectorAll('[data-nav="support"]').forEach(button=>{
    button.addEventListener('click',()=>{checkOwner();controls()});
  });
  form.onsubmit=async event=>{
    event.preventDefault();checkOwner();
    if(chat.busy)return;
    if(!S.me){notice.textContent='Sign in to use workspace help.';return;}
    const original=question.value,text=original.trim();
    if(!text||text.length>3000){notice.textContent='Enter a question between 1 and 3,000 characters.';return;}
    const selected=['auto','gemini','openai'].includes(provider.value)?provider.value:'auto';
    const history=chat.messages.slice(-10).map(entry=>({role:entry.role,content:entry.content.slice(0,2000)}));
    const owner=identity(),revision=++chat.revision;
    chat.busy=true;
    chat.messages=[...chat.messages,{role:'user',content:text}].slice(-10);
    if(question.value===original)question.value='';
    notice.textContent='Getting help with your question…';render();
    try{
      const result=await post('/api/support/chat',{question:text,provider:selected,history,expected_user_id:S.me.id});
      if(owner!==identity()||revision!==chat.revision)return;
      if(!result||typeof result.reply!=='string'||!result.reply.trim())throw Error('The help service returned an unreadable answer. Try again.');
      const live=result.ai_used===true&&['gemini','openai'].includes(result.provider)&&typeof result.model==='string'&&result.model.trim();
      const label=live?`${result.provider==='gemini'?'Gemini':'OpenAI'} · ${result.model.slice(0,120)}`:'Local help';
      chat.messages=[...chat.messages,{role:'assistant',content:result.reply.slice(0,4000),label}].slice(-10);
      notice.textContent=typeof result.notice==='string'&&result.notice.trim()?result.notice.slice(0,2000):
        (live?'AI answer generated using the provider shown below.':'Local guidance shown. Live AI was not used for this answer.');
      render();
    }catch(error){
      if(owner!==identity()||revision!==chat.revision)return;
      if(error?.status===409){
        reset();
        notice.textContent='Your account changed. Reload the app before continuing this chat.';
        window.location?.reload?.();
        return;
      }
      notice.textContent=error?.message&&error.message!=='Session changed'?String(error.message).slice(0,1000):'Your session changed. Sign in again to continue.';
      if(!question.value)question.value=original;
    }finally{
      if(owner===identity()&&revision===chat.revision){chat.busy=false;controls();}
    }
  };
  reset();
})();
