'use strict';
(()=>{
  const get=id=>document.getElementById(id),form=get('accountPasswordForm');
  if(!form)return;
  const fields=['currentPassword','newPassword','confirmPassword'].map(get);
  const summary=get('accountSecuritySummary'),message=get('accountSecurityMessage');
  const change=get('changePasswordButton'),logout=get('logoutAllSessionsButton');
  const state={owner:'',revision:0,loadRevision:0,busy:false,loading:false,nextCheck:0,cancel:null};
  const identity=()=>S.me?`${S.epoch}:${S.me.id}`:'';
  const clearFields=()=>fields.forEach(field=>{field.value=''});
  function controls(){
    change.disabled=logout.disabled=state.busy||!S.me;
    fields.forEach(field=>{field.disabled=state.busy||!S.me});
    form.setAttribute('aria-busy',String(state.busy));
  }
  function clear(){
    state.revision++;state.loadRevision++;state.cancel?.abort();
    Object.assign(state,{owner:identity(),busy:false,loading:false,nextCheck:0,cancel:null});
    clearFields();summary.textContent='Sign in to review account security.';
    message.textContent='A successful password change signs out all existing sessions.';controls();
  }
  function checkOwner(){if(state.owner!==identity())clear();}
  function accountChanged(){clear();window.location?.reload?.();}
  async function refresh(){
    checkOwner();
    if(!S.me||S.nav!=='settings'||document.hidden||state.loading||state.busy||Date.now()<state.nextCheck)return;
    const owner=identity(),revision=++state.loadRevision;
    state.loading=true;state.nextCheck=Date.now()+10000;
    const controller=typeof AbortController==='function'?new AbortController():null;
    state.cancel=controller;
    try{
      const result=await api(`/api/security?expected_user_id=${encodeURIComponent(S.me.id)}`,controller?{signal:controller.signal}:{});
      if(owner!==identity()||revision!==state.loadRevision)return;
      if(!result||!Number.isInteger(result.active_sessions)||result.active_sessions<0)throw Error('Invalid security status');
      const password=result.password_storage==='salted_pbkdf2'?'Password stored as a salted hash. ':'Review password settings. ';
      const encryption=result.saved_credentials_encrypted===true?'Saved API credentials are encrypted. ':'';
      summary.textContent=password+encryption+`${result.active_sessions} active session(s). Payment approval does not require your password.`;
    }catch(error){
      if(owner!==identity()||revision!==state.loadRevision)return;
      if(error?.status===409){accountChanged();return;}
      summary.textContent='Security status is unavailable. Try opening Settings again shortly.';
    }finally{
      if(owner===identity()&&revision===state.loadRevision){state.loading=false;state.cancel=null;controls();}
    }
  }
  async function mutateSecurity(path,body,success){
    const owner=identity(),revision=++state.revision;
    state.loadRevision++;state.cancel?.abort();state.cancel=null;state.loading=false;
    state.busy=true;clearFields();controls();message.textContent='Updating your account security…';
    try{
      const result=await post(path,{...body,expected_user_id:S.me.id});
      if(owner!==identity()||revision!==state.revision)return;
      if(result?.ok!==true||result.sign_in_required!==true)throw Error('Security update could not be confirmed.');
      resetSession();get('loginMsg').textContent=success;
    }catch(error){
      if(owner!==identity()||revision!==state.revision)return;
      if(error?.status===409){accountChanged();return;}
      message.textContent=error?.message&&error.message!=='Session changed'?String(error.message).slice(0,400):'Your session changed. Sign in again.';
    }finally{
      if(owner===identity()&&revision===state.revision){state.busy=false;clearFields();controls();}
    }
  }
  form.onsubmit=async event=>{
    event.preventDefault();checkOwner();
    if(state.busy||!S.me)return;
    const [current,next,confirm]=fields.map(field=>field.value);
    if(!current||next.length<12||next.length>256||!next.trim()||next!==confirm){
      message.textContent='Enter your current password and matching new passwords of 12–256 characters.';return;
    }
    if(current===next){message.textContent='Choose a different new password.';return;}
    await mutateSecurity('/api/security/password',{current_password:current,new_password:next},'Password changed. Sign in with your new password.');
  };
  logout.onclick=async()=>{
    checkOwner();if(state.busy||!S.me)return;
    await mutateSecurity('/api/security/logout-all',{},'All account sessions signed out. Sign in again to continue.');
  };
  window.clearAccountSecurity=clear;window.refreshAccountSecurity=refresh;
  document.querySelectorAll('[data-nav="settings"]').forEach(button=>button.addEventListener('click',()=>{void refresh()}));
  clear();
})();
