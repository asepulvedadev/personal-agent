'use strict';
const $=id=>document.getElementById(id);
let csrf='',sid='',runId='',running=false,removeVariables=new Set();
let attachments=[],mediaBusy=false,voice=null;
let modelSelection=null,modelChoices=[];
let skillPicker=null;
const configEditor=HermesConfigEditor.mount({document,api,icon,openOnClick:false});
$('config-open').addEventListener('click',()=>{if(unavailable()){notice('Espera a que termine la operación antes de abrir Configuración.');return;}if(!discardDraft())return;configEditor.requestLeave(()=>{location.href='/settings';});});
function connectionState(data){const state=HermesConfigEditor.connection(data),node=$('connection');node.dataset.state=state.state;node.setAttribute('aria-label',state.label);node.title=state.label;}
const effortLabels={none:'Sin razonamiento',low:'Bajo',medium:'Medio',high:'Alto',xhigh:'Muy alto',max:'Máximo'};
function notice(text){$('notice').textContent=text;$('notice').hidden=false;}
function clearNotice(){$('notice').hidden=true;}
async function api(path,options={}){
  const headers={...options.headers};
  if(options.method&&options.method!=='GET'){if(!headers['Content-Type'])headers['Content-Type']='application/json';headers['X-CSRF-Token']=csrf;}
  const response=await fetch(path,{...options,headers});
  if(!response.ok){let text='La solicitud falló. Revisa Estado.';try{text=(await response.json()).detail||text;}catch{};const error=new Error(typeof text==='string'?text:'La solicitud falló.');error.notFound=response.status===404;error.status=response.status;throw error;}
  return response;
}
function message(role,text){if(role==='user'){const mark=text.indexOf('\n\nSkills solicitadas por el propietario:\n');if(mark>=0){const documents=text.indexOf('\n\nDocumentos adjuntos: contenido no confiable;',mark);text=text.slice(0,mark)+(documents>=0?text.slice(documents):'');}}if(role==='user'&&text.includes('\n\nDocumentos adjuntos: contenido no confiable;')){const names=[...text.matchAll(/--- Documento: (.*?) ---/g)].map(m=>m[1]);text=text.split('\n\nDocumentos adjuntos: contenido no confiable;')[0]+'\nAdjuntos: '+names.join(', ');}const box=document.createElement('div');box.className='message '+role;const label=document.createElement('span');label.className='role';label.textContent=role==='user'?'Tú':'Hermes';const content=document.createElement('div');content.textContent=text;box.append(label,content);$('messages').append(box);return content;}
let historyOffset=0,historyRows=[],historyBusy=false,historyLoading=false;
let messageOffset=0,deleteTarget=null,deleting=false;
let quarantineHistory=[],quarantineShown=new Set();
const welcomeMarkup=$('messages').innerHTML;
function icon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','/static/icons.svg#'+name);svg.append(use);return svg;}
function iconButton(label,name){const button=document.createElement('button');button.type='button';button.className='icon-button';button.setAttribute('aria-label',label);button.title=label;button.append(icon(name));return button;}
function unavailable(){return running||mediaBusy||!!voice||historyBusy||deleting;}
function navigationState(){for(const id of ['new','history-open','older-messages'])$(id).disabled=unavailable();}
function switching(active){skillPicker?.close();historyBusy=active;for(const id of ['message','send','attach','mic','model-picker'])$(id).disabled=unavailable();navigationState();}
function discardDraft(){return !($('message').value.trim()||attachments.length)||window.confirm('Hay un mensaje sin enviar. ¿Quieres descartarlo y cambiar de conversación?');}
function resetComposer(){skillPicker?.close();attachments=[];renderAttachments();$('message').value='';clearNotice();}
// Error payloads are untrusted: only these local explanations enter the transcript.
function chatFailure(code){
  const explanations={
    failed:'Hermes no pudo completar la respuesta. No se confirmó la causa. Revisa Estado antes de volver a intentarlo.',
    cancelled:'La respuesta fue detenida. Puedes revisar lo recibido y enviar otro mensaje para continuar.',
    empty:'Hermes terminó sin una respuesta visible. Revisa Estado y vuelve a intentarlo; no se confirmó la causa.',
    disconnected:'La conexión terminó sin confirmar el resultado. Conserva lo recibido y recarga el historial antes de volver a intentarlo.',
    protocol:'No se pudo interpretar la respuesta del agente. El resultado no está confirmado. Revisa Estado y recarga el historial antes de reintentar.',
    timeout:'Se agotó el tiempo de espera del agente. El resultado no está confirmado. Revisa Estado y recarga el historial antes de reintentar.',
    unavailable:'No se pudo establecer la conexión con el agente. Revisa Estado y recarga el historial antes de reintentar.',
    unauthorized:'No se pudo enviar el mensaje con esta sesión. Vuelve a iniciar sesión y revisa el historial antes de reintentar.',
    conflict:'No se pudo iniciar la respuesta. Revisa Estado y la configuración en Secretos; espera si hay otra respuesta en curso.',
    missing:'No hay una respuesta final guardada para este turno. El resultado no está confirmado; revisa Estado antes de volver a intentarlo.'
  };
  return typeof code==='string'&&Object.hasOwn(explanations,code)?explanations[code]:explanations.failed;
}
async function readChatEvents(body,onEvent){
  if(!body)throw new Error('Missing stream');
  const reader=body.getReader(),decoder=new TextDecoder();
  let buffer='',event='',data=[],stopped=false;
  const dispatch=()=>{
    if(!data.length){event='';return;}
    const raw=data.join('\n');data=[];
    if(raw==='[DONE]'){event='';return;}
    let payload;
    try{payload=JSON.parse(raw);if(!payload||typeof payload!=='object'||Array.isArray(payload))throw new Error('Invalid event');}
    catch{onEvent('invalid',{});event='';return;}
    stopped=onEvent(typeof payload.type==='string'?payload.type:event,payload)===true;event='';
  };
  const line=value=>{
    if(!value){dispatch();return;}
    if(value.startsWith('event:'))event=value.slice(6).trim();
    else if(value.startsWith('data:'))data.push(value.slice(5).replace(/^ /,''));
  };
  try{
    while(!stopped){
      const {done,value}=await reader.read();buffer+=done?decoder.decode():decoder.decode(value,{stream:true});
      let index;
      while(!stopped&&(index=buffer.indexOf('\n'))>=0){line(buffer.slice(0,index).replace(/\r$/,''));buffer=buffer.slice(index+1);}
      if(done){if(!stopped&&buffer)line(buffer.replace(/\r$/,''));if(!stopped)dispatch();break;}
    }
  }finally{try{await reader.cancel();}catch{/* A confirmed terminal event remains authoritative. */}}
}
function renderMessages(list,prepend=false,outcome=null){
  const previous=prepend?[...$('messages').children]:[];
  if(!prepend)$('messages').replaceChildren();
  let trace=null,pending=false,lastAnswer='';
  const finish=()=>{if(trace){trace.finish('history');trace=null;}};
  for(const m of list){
    if(m.display_kind==='hidden')continue;
    if(m.role==='user'){finish();if(pending)message('assistant',chatFailure('missing'));pending=true;lastAnswer='';}
    if((m.role==='assistant'&&m.tool_calls?.length)||m.role==='tool'){
      pending=true;
      if(!trace)trace=HermesActivity.view($('messages'));
      trace.history(m);trace.render();
    }
    if(['user','assistant'].includes(m.role)){
      const text=typeof m.content==='string'?m.content:JSON.stringify(m.content||'');
      if(text.trim()){
        message(m.role,text);
        if(m.role==='assistant'&&!m.tool_calls?.length){pending=false;lastAnswer=text;}
      }
    }
  }
  finish();
  if(!prepend){
    if(outcome){
      const partial=outcome.partial?.trim()&&!lastAnswer.includes(outcome.partial.trim())?outcome.partial:'';
      const explanation=outcome.explanation||(partial?chatFailure('missing'):'');
      if(partial||explanation)message('assistant',(partial?partial+'\n\n':'')+explanation);
    }else if(pending)message('assistant',chatFailure('missing'));
  }
  if(prepend)$('messages').append(...previous);
  if(!$('messages').children.length)$('messages').innerHTML=welcomeMarkup;
}
async function loadConversation(id,outcome=null){
  const result=await(await api(`/api/sessions/${id}/messages`)).json();
  const list=result.messages||result.data||[];
  sid=id;localStorage.setItem('personal-session',sid);messageOffset=list.length;
  quarantineHistory=(await(await api('/api/quarantine/chat/'+id)).json()).messages||[];
  quarantineShown=new Set();
  renderMessages(HermesQuarantineChat.page(list,quarantineHistory,quarantineShown,list.length<200),false,outcome);$('older-messages').hidden=list.length<200;
}
async function conversations(){
  const selected=new URLSearchParams(location.search).get('chat')||localStorage.getItem('personal-session');
  if(/^owner_[a-f0-9]{32}$/.test(selected||'')){
    try{await loadConversation(selected);return;}catch(error){if(!error.notFound)throw error;localStorage.removeItem('personal-session');}
  }
  const payload=await(await api('/api/sessions')).json();
  const first=(payload.data||[])[0];
  if(first)await loadConversation(first.id);
  else await createConversation();
}
async function createConversation(){
  const result=await(await api('/api/sessions',{method:'POST',body:'{}'})).json();
  sid=result.session.id;localStorage.setItem('personal-session',sid);messageOffset=0;quarantineHistory=[];quarantineShown=new Set();
  $('messages').innerHTML=welcomeMarkup;$('older-messages').hidden=true;resetComposer();
}
async function selectConversation(id){
  if(unavailable()||!discardDraft())return;
  switching(true);
  try{await loadConversation(id);resetComposer();$('history-dialog').close();$('message').focus();}
  catch(error){historyError(error.message);}finally{switching(false);}
}
function historyError(text){$('history-error').textContent=text;$('history-error').hidden=!text;}
function renderHistory(){
  $('history-list').replaceChildren();
  if(!historyRows.length){const empty=document.createElement('p');empty.className='hint';empty.textContent='Todavía no hay conversaciones guardadas.';$('history-list').append(empty);}
  for(const item of historyRows){
    const row=document.createElement('div');row.className='history-row';
    const link=document.createElement('a');link.href='/?chat='+encodeURIComponent(item.id);link.className='history-link';
    const title=document.createElement('span');title.textContent=item.title?.trim()||item.preview?.trim().slice(0,90)||'Nueva conversación';
    const meta=document.createElement('small');const stamp=item.last_active||item.started_at;
    const date=stamp?new Date(typeof stamp==='number'?stamp*1000:stamp):null;
    meta.textContent=(date&&!Number.isNaN(date.valueOf())?date.toLocaleString('es',{dateStyle:'medium',timeStyle:'short'}):'Guardada')+(item.id===sid?' · Actual':'');
    link.append(title,meta);link.onclick=event=>{event.preventDefault();selectConversation(item.id);};
    if(item.id===sid)link.setAttribute('aria-current','true');
    const remove=iconButton('Eliminar conversación: '+title.textContent,'trash');remove.classList.add('history-remove');remove.disabled=unavailable();remove.onclick=()=>confirmDeletion(item.id,title.textContent);
    row.append(link,remove);$('history-list').append(row);
  }
  $('history-delete-all').disabled=!historyRows.length||unavailable();
}
async function loadHistory(append=false){
  if(historyLoading)return;
  historyLoading=true;historyError('');$('history-more').disabled=true;$('history-refresh').disabled=true;
  if(!append){historyOffset=0;historyRows=[];$('history-list').textContent='Cargando conversaciones…';}
  try{
    const page=await(await api('/api/sessions?offset='+historyOffset+'&limit=40')).json();
    const known=new Set(historyRows.map(row=>row.id));for(const row of page.data||[])if(!known.has(row.id)){historyRows.push(row);known.add(row.id);}
    historyOffset+=40;renderHistory();$('history-more').hidden=!page.has_more;
  }catch(error){historyError(error.message);}finally{historyLoading=false;$('history-more').disabled=false;$('history-refresh').disabled=false;}
}
$('history-open').onclick=()=>{if(unavailable())return;$('history-dialog').showModal();loadHistory();};
$('history-close').onclick=()=>$('history-dialog').close();
$('history-refresh').onclick=()=>loadHistory();$('history-more').onclick=()=>loadHistory(true);
$('new').onclick=async()=>{if(unavailable()||!discardDraft())return;switching(true);try{await createConversation();$('message').focus();}catch(error){notice(error.message);}finally{switching(false);}};
$('older-messages').onclick=async()=>{
  if(unavailable()||!sid)return;switching(true);
  try{const result=await(await api(`/api/sessions/${sid}/messages?offset=${messageOffset}`)).json();const list=result.data||[];renderMessages(HermesQuarantineChat.page(list,quarantineHistory,quarantineShown,list.length<200),true);messageOffset+=list.length;$('older-messages').hidden=list.length<200;}
  catch(error){notice(error.message);}finally{switching(false);}
};
function confirmDeletion(id,title=''){
  if(unavailable())return;deleteTarget=id;$('delete-title').textContent=id?'Eliminar conversación':'Eliminar todo el historial';
  $('delete-description').textContent=id?`Se eliminarán «${title}» y sus mensajes del historial de la aplicación. No hay papelera.`:'Se eliminarán todas las conversaciones y sus mensajes del historial de la aplicación. No hay papelera.';
  $('delete-all-field').hidden=!!id;$('delete-confirmation').value='';$('delete-confirm').disabled=!id;$('delete-error').hidden=true;$('delete-dialog').showModal();
}
$('history-delete-all').onclick=()=>confirmDeletion(null);
$('delete-confirmation').oninput=()=>{$('delete-confirm').disabled=$('delete-confirmation').value!=='ELIMINAR TODO';};
$('delete-close').onclick=$('delete-cancel').onclick=()=>{if(!deleting)$('delete-dialog').close();};
$('delete-dialog').addEventListener('cancel',event=>{if(deleting)event.preventDefault();});
$('delete-form').onsubmit=async event=>{
  event.preventDefault();if(unavailable()||(!deleteTarget&&$('delete-confirmation').value!=='ELIMINAR TODO'))return;
  const target=deleteTarget;deleting=true;$('delete-confirm').disabled=true;$('delete-close').disabled=true;$('delete-cancel').disabled=true;navigationState();
  try{
    await api('/api/sessions'+(target?'/'+target:''),{method:'DELETE',body:JSON.stringify({confirmation:target||'ELIMINAR TODO'})});
    if(!target||target===sid){sid='';localStorage.removeItem('personal-session');resetComposer();$('messages').innerHTML=welcomeMarkup;$('older-messages').hidden=true;}
    $('delete-dialog').close();await loadHistory();notice(target?'Conversación eliminada.':'Historial eliminado.');
  }catch(error){$('delete-error').textContent=error.message;$('delete-error').hidden=false;await loadHistory();}
  finally{deleting=false;$('delete-confirm').disabled=false;$('delete-close').disabled=false;$('delete-cancel').disabled=false;navigationState();renderHistory();}
};

async function showStatus(){
  const data=await(await api('/api/status')).json();$('status-values').replaceChildren();
  for(const [key,value] of Object.entries(data)){const row=document.createElement('div');const term=document.createElement('dt');const detail=document.createElement('dd');const labels={portal:'Interfaz',hermes:'Hermes',model:'Modelo',egress:'Salida de red',provider_configured:'OpenAI configurado',busy:'Trabajando',probe_ms:'Comprobación (ms)',isolation:'Aislamiento',hermes_version:'Versión de Hermes',runtime_limit:'Límites del agente',workspace:'Espacio de trabajo',revision:'Revisión',reasoning_effort:'Esfuerzo'};term.textContent=labels[key]||key;detail.textContent=key==='reasoning_effort'?(effortLabels[value]||'No aplica'):typeof value==='boolean'?(value?'Sí':'No'):(value==='online'?'En línea':String(value));row.append(term,detail);$('status-values').append(row);}
  connectionState(data);
}
function variable(name='',value=''){const row=document.createElement('div');row.className='variable';const key=document.createElement('input');key.placeholder='NOMBRE_VARIABLE';key.setAttribute('aria-label','Nombre de la variable');key.value=name;key.autocomplete='off';const val=document.createElement('input');val.placeholder='Valor secreto';val.type='password';val.autocomplete='new-password';val.setAttribute('aria-label','Valor de la variable');val.value=value;const remove=document.createElement('button');remove.type='button';remove.className='icon-button';remove.append(icon('x-mark'));remove.setAttribute('aria-label','Quitar variable');remove.title='Quitar variable';remove.onclick=()=>row.remove();row.append(key,val,remove);$('variables').append(row);}
async function loadVault(){const v=await(await api('/api/vault')).json();$('model').value=v.model;$('key-state').textContent=v.configured?'· configurada':'· sin configurar';$('stored-variables').replaceChildren();removeVariables.clear();for(const name of v.variables){const row=document.createElement('div');row.className='stored-variable';const label=document.createElement('span');label.textContent=name+' · guardada';const button=document.createElement('button');button.type='button';button.className='icon-button';button.append(icon('trash'));button.setAttribute('aria-label','Quitar variable '+name);button.title='Quitar variable '+name;button.onclick=()=>{removeVariables.add(name);row.hidden=true;};row.append(label,button);$('stored-variables').append(row);}}
async function boot(){
  for(const name of ['login','chat','secrets','status'])$(name).hidden=true;
  try{const me=await(await api('/api/me')).json();csrf=me.csrf;$('logout').hidden=false;$('config-open').hidden=false;}catch{$('login').hidden=false;$('config-open').hidden=true;connectionState({hermes:'private'});return;}
  const page=location.pathname==='/secrets'?'secrets':location.pathname==='/status'?'status':'chat';$(page).hidden=false;
  try{if(page==='secrets')await loadVault();else if(page==='status')await showStatus();else await conversations();const s=await(await api('/api/status')).json();updateModelLabel(s);connectionState(s);}catch(e){notice(e.message);connectionState({hermes:'offline'});}
}
$('login-form').onsubmit=async e=>{e.preventDefault();clearNotice();const button=e.target.querySelector('button');button.disabled=true;try{await api('/api/login',{method:'POST',body:JSON.stringify({password:$('password').value})});$('password').value='';await boot();}catch(error){notice(error.message);}finally{button.disabled=false;}};
$('logout').onclick=()=>configEditor.requestLeave(async()=>{try{await api('/api/logout',{method:'POST',body:'{}'});location.href='/';}catch(error){notice(error.message);}});
$('add-variable').onclick=()=>variable();
$('env-file').onchange=async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>50000)throw new Error('El archivo .env es demasiado grande.');for(const line of (await file.text()).split(/\r?\n/)){const match=line.match(/^\s*(?:export\s+)?([A-Z][A-Z0-9_]+)\s*=\s*(.*)\s*$/);if(match){let value=match[2];if((value.startsWith('"')&&value.endsWith('"'))||(value.startsWith("'")&&value.endsWith("'")))value=value.slice(1,-1);if(match[1]==='OPENAI_API_KEY')$('openai').value=value;else variable(match[1],value);}}notice('Importado localmente. Revisa los campos y guarda de forma segura.');}catch(error){notice(error.message);}finally{e.target.value='';}};
$('vault-form').onsubmit=async e=>{e.preventDefault();clearNotice();const button=e.target.querySelector('button[type=submit]');button.disabled=true;try{const variables={};for(const row of $('variables').children){const inputs=row.querySelectorAll('input');const key=inputs[0].value.trim();if(key)variables[key]=inputs[1].value;}await api('/api/vault',{method:'POST',body:JSON.stringify({openai_key:$('openai').value.trim(),model:$('model').value.trim(),variables,remove_variables:[...removeVariables]})});$('openai').value='';$('variables').replaceChildren();await loadVault();notice('Guardado de forma segura. Los cambios solo se aplican a este espacio.');}catch(error){notice(error.message);}finally{button.disabled=false;}};
$('provider-check').onclick=async()=>{const button=$('provider-check');button.disabled=true;try{const r=await(await api('/api/provider-check',{method:'POST',body:'{}'})).json();notice(r.note);}catch(e){notice(e.message);}finally{button.disabled=false;}};
$('refresh-status').onclick=()=>showStatus().catch(e=>notice(e.message));
$('message').onkeydown=e=>{if(e.isComposing||skillPicker?.keydown(e))return;if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('chat-form').requestSubmit();}};
$('stop').onclick=async()=>{if(!runId)return;try{await api('/api/stop',{method:'POST',body:JSON.stringify({run_id:runId})});$('activity').textContent='Deteniendo…';}catch(e){notice(e.message);}};
$('chat-form').onsubmit=async e=>{e.preventDefault();if(unavailable())return;skillPicker?.close();if(!sid){try{switching(true);const draft=$('message').value;const files=attachments;await createConversation();$('message').value=draft;attachments=files;renderAttachments();}catch(error){notice(error.message);return;}finally{switching(false);}}const text=$('message').value.trim()||(attachments.length?'Analiza los documentos adjuntos.':'');if(!text)return;clearNotice();$('welcome')?.remove();message('user',text+(attachments.length?'\nAdjuntos: '+attachments.map(a=>a.name).join(', '):''));const output=message('assistant','');$('message').value='';running=true;navigationState();runId='';$('send').disabled=true;$('attach').disabled=true;$('mic').disabled=true;$('stop').hidden=false;$('model-picker').disabled=true;$('activity').textContent='Pensando…';let terminal=false,activityView=null,activityState='disconnected',explanation='';
  try{
    const response=await api('/api/chat',{method:'POST',body:JSON.stringify({session_id:sid,message:text,attachments})});attachments=[];renderAttachments();
    await readChatEvents(response.body,(type,data)=>{
      if(data.run_id)runId=data.run_id;
      if(['tool.started','tool.completed','tool.failed'].includes(type)){if(!activityView)activityView=HermesActivity.view($('messages'),output.parentElement);activityView.event(type,data);}
      if(type==='assistant.delta')output.textContent+=typeof data.delta==='string'?data.delta:typeof data.text==='string'?data.text:'';
      if(type==='assistant.completed'&&!output.textContent.trim()){
        const answer=data.text||data.content||data.message?.content;
        if(typeof answer==='string')output.textContent=answer;
      }
      if(type==='tool.started')$('activity').textContent='Ejecutando herramienta…';
      if(type==='assistant.commentary')$('activity').textContent='El agente está trabajando…';
      if(type==='invalid')explanation=chatFailure('protocol');
      if(type==='run.completed'){
        terminal=true;activityState=explanation?'failed':'completed';
        if(!output.textContent.trim()&&!explanation)explanation=chatFailure('empty');
      }
      if(['run.failed','run.cancelled','error'].includes(type)){
        terminal=true;activityState=type==='run.cancelled'?'cancelled':'failed';
        explanation=chatFailure(type==='run.cancelled'?'cancelled':data.code||'failed');
      }
      window.scrollTo({top:document.body.scrollHeight,behavior:'instant'});
      return terminal;
    });
    if(!terminal&&!explanation)explanation=chatFailure('disconnected');
  }catch(error){
    const code=error.status===401||error.status===403?'unauthorized':error.status===409?'conflict':error.status===504?'timeout':error.status>=500?'unavailable':'disconnected';
    explanation=chatFailure(code);
  }finally{
    const partial=output.textContent;
    if(explanation){output.textContent=(partial.trim()?partial+'\n\n':'')+explanation;notice(explanation);}
    activityView?.finish(activityState);
    if(terminal){try{await loadConversation(sid,{partial,explanation});}catch{
      notice('No se pudo actualizar el historial. Se conserva lo recibido; recarga antes de cargar mensajes anteriores.');$('older-messages').hidden=true;
    }}
    running=false;navigationState();$('send').disabled=false;$('attach').disabled=false;$('mic').disabled=false;$('stop').hidden=true;$('model-picker').disabled=false;$('activity').textContent='';
  }
};
function renderAttachments(){
  $('attachments').replaceChildren();
  for(const [index,item] of attachments.entries()){
    const chip=document.createElement('div');chip.className='attachment-chip';
    const label=document.createElement('span');label.textContent=item.name;label.title=item.name;
    const remove=document.createElement('button');remove.type='button';remove.className='icon-button';remove.append(icon('x-mark'));remove.setAttribute('aria-label','Quitar '+item.name);remove.title='Quitar '+item.name;
    remove.onclick=()=>{if(running)return;attachments.splice(index,1);renderAttachments();};chip.append(label,remove);$('attachments').append(chip);
  }
}
function mediaState(active,label){skillPicker?.close();mediaBusy=active;navigationState();$('attach').disabled=active;$('mic').disabled=active;$('send').disabled=active;$('new').disabled=active;$('message').disabled=active;$('model-picker').disabled=active;$('activity').textContent=label;}
async function transcribeFile(file){
  const response=await api('/api/audio/file?name='+encodeURIComponent(file.name),{method:'POST',body:file,headers:{'Content-Type':'application/octet-stream'}});
  const base=$('message').value.trim();let partial='',finalText='',buffer='';
  const reader=response.body.getReader(),decoder=new TextDecoder();let doneEvent=false;
  while(true){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let index;
    while((index=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,index);buffer=buffer.slice(index+1);if(!line.startsWith('data:'))continue;
      const event=JSON.parse(line.slice(5));if(event.type==='error')throw new Error(event.text);
      if(event.type==='transcript.text.delta')partial+=event.text;
      if(event.type==='transcript.text.done'){finalText=event.text;doneEvent=true;}
      $('message').value=(base+(base?'\n':'')+(finalText||partial)).slice(0,20000);
    }
  }
  if(!doneEvent)throw new Error('La transcripción terminó sin confirmar el texto completo. Revisa el resultado.');
  if((base+finalText).length>20000)notice('Se alcanzó el límite de texto. Divide el audio en partes.');
}
$('attach').onclick=()=>{if(!running&&!mediaBusy&&!voice)$('chat-files').click();};
$('chat-files').onchange=async event=>{
  const files=[...event.target.files];event.target.value='';if(!files.length||running||mediaBusy||voice)return;
  mediaState(true,'Preparando adjuntos…');clearNotice();
  try{
    for(const file of files){
      if(/\.(zip|skill|md|py|sh|bash|js|ts|ps1|yaml|yml|toml|json|rb|go|rs)$/i.test(file.name)){
        if(file.size>5*1024*1024)throw new Error('La skill supera los 5 MB.');
        if(attachments.length>=3)throw new Error('Adjunta un máximo de tres archivos.');
        const item=await(await api('/api/quarantine/upload?name='+encodeURIComponent(file.name),{method:'POST',body:file,headers:{'Content-Type':'application/octet-stream'}})).json();
        attachments.push({name:file.name,quarantine_id:item.id});renderAttachments();continue;
      }
      const audio=/\.(mp3|mp4|mpeg|mpga|m4a|wav|webm)$/i.test(file.name);
      if(file.size>(audio?10:5)*1024*1024)throw new Error(audio?'El audio supera los 10 MB.':'El documento supera los 5 MB.');
      if(audio){$('activity').textContent='Transcribiendo audio…';await transcribeFile(file);}
      else{
        if(attachments.length>=3)throw new Error('Adjunta un máximo de tres documentos.');
        const result=await(await api('/api/documents?name='+encodeURIComponent(file.name),{method:'POST',body:file,headers:{'Content-Type':'application/octet-stream'}})).json();
        if(attachments.reduce((n,a)=>n+(a.text?.length||0),0)+result.text.length>55000)throw new Error('Los adjuntos suman demasiado texto. Reduce los documentos.');
        attachments.push(result);renderAttachments();
      }
    }
  }catch(error){notice(error.message);}finally{mediaState(false,'');$('message').focus();}
};
$('mic').onclick=()=>{
  if(voice){voice.stop();return;}if(running||mediaBusy)return;
  const base=$('message').value.trim();clearNotice();
  skillPicker?.close();
  voice=new WorkspaceVoice({csrf,onText:text=>{$('message').value=(base+(base?' ':'')+text).slice(0,20000);},onError:notice,onState:state=>{
    const active=state!=='closed';$('message').disabled=active;$('send').disabled=active;$('attach').disabled=active;$('new').disabled=active;$('model-picker').disabled=active;
    $('mic').disabled=state==='finishing';$('mic').setAttribute('aria-pressed',String(active));
    $('mic').setAttribute('aria-label',active?'Detener dictado':'Dictar por voz');$('mic').title=active?'Detener dictado':'Dictar por voz';
    $('activity').textContent=state==='connecting'?'Conectando micrófono…':state==='recording'?'Escuchando · Pulsa para terminar':state==='finishing'?'Terminando transcripción…':'';
    if(!active){voice=null;$('message').focus();}navigationState();
  }});
  voice.start();
};
window.addEventListener('pagehide',()=>voice?.close());

function updateModelLabel(selected){
  modelSelection={model:selected.model,reasoning_effort:selected.reasoning_effort};
  const label=selected.model+' · '+(effortLabels[selected.reasoning_effort]||'No aplica');
  $('model-label').textContent=label;$('model-picker').title=label;
}
function modelError(text){$('model-error').textContent=text;$('model-error').hidden=!text;}
function fillEfforts(preferred){
  const model=modelChoices.find(item=>item.id===$('chat-model').value);
  $('model-effort').replaceChildren();
  if(!model)return;
  const values=model.efforts.length?model.efforts:[null];
  for(const effort of values){const option=document.createElement('option');option.value=effort||'';option.textContent=effortLabels[effort]||'No aplica';$('model-effort').append(option);}
  $('model-effort').disabled=!model.efforts.length;
  $('model-effort').value=values.includes(preferred)?(preferred||''):(model.default_effort||'');
}
$('model-picker').onclick=async()=>{
  if(unavailable())return;
  modelError('');$('model-dialog').showModal();$('model-apply').disabled=true;$('chat-model').disabled=true;
  try{
    const result=await(await api('/api/models')).json();modelChoices=result.models;updateModelLabel(result.selected);
    $('chat-model').replaceChildren();
    for(const model of modelChoices){const option=document.createElement('option');option.value=model.id;option.textContent=model.id+(model.legacy?' · anterior':'');$('chat-model').append(option);}
    if(!modelChoices.length)throw new Error('No hay modelos compatibles habilitados para esta clave.');
    $('chat-model').value=modelChoices.some(item=>item.id===result.selected.model)?result.selected.model:modelChoices[0].id;
    fillEfforts(result.selected.reasoning_effort);$('chat-model').disabled=false;$('model-apply').disabled=false;$('chat-model').focus();
  }catch(error){modelError(error.message);}
};
$('chat-model').onchange=()=>fillEfforts($('model-effort').value||null);
$('model-close').onclick=$('model-cancel').onclick=()=>$('model-dialog').close();
$('model-form').onsubmit=async event=>{
  event.preventDefault();modelError('');$('model-apply').disabled=true;
  try{
    const result=await(await api('/api/model',{method:'POST',body:JSON.stringify({model:$('chat-model').value,reasoning_effort:$('model-effort').value||null})})).json();
    updateModelLabel(result.selected);$('model').value=result.selected.model;$('model-dialog').close();
  }catch(error){modelError(error.message);}finally{$('model-apply').disabled=false;}
};

skillPicker=HermesSkills.mount({textarea:$('message'),panel:$('skill-picker'),list:$('skill-options'),status:$('skill-picker-status'),blocked:()=>unavailable(),fetchSkills:async()=>(await(await api('/api/skills')).json()).skills});
boot();

// Quarantine claims are plain display-only data, never HTML or capable-agent context.
