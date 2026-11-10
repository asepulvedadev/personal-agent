/* Settings UI: credentials stay write-only; resource drafts are never executed. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else{root.HermesSettings=api;document.addEventListener('DOMContentLoaded',()=>api.mount(document));}})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  function createEditor({read,write,change=()=>{}}){
    let serial=0, key=null;
    let state={item:null,content:'',baseline:'',phase:'idle',error:''};
    const get=()=>({...state,dirty:state.content!==state.baseline});
    const emit=()=>change(get());
    const apply=item=>{state={item,content:item.content,baseline:item.content,phase:'ready',error:''};};
    return {get,
      clear(){serial++;key=null;state={item:null,content:'',baseline:'',phase:'idle',error:''};emit();},
      async load(next,discard=false){
        if(state.phase==='saving'||(get().dirty&&!discard))return false;
        const token=++serial;key={...next};state={item:null,content:'',baseline:'',phase:'loading',error:''};emit();
        try{const item=await read(key);if(token!==serial)return false;apply(item);}catch(error){if(token!==serial)return false;state.phase='error';state.error=error.message;}
        emit();return true;
      },
      edit(content){if(!state.item?.editable||['saving','loading'].includes(state.phase))return;state.content=content;emit();},
      async save(){
        if(!state.item?.editable||!get().dirty||['saving','loading','conflict'].includes(state.phase))return;
        const token=++serial;state.phase='saving';state.error='';emit();
        try{const item=await write(key,{content:state.content,revision:state.item.revision});if(token!==serial)return;apply(item);state.phase='saved';}
        catch(error){if(token!==serial)return;state.error=error.message;state.phase=error.status===409?'conflict':'error';}
        emit();
      }
    };
  }

  function assetSelection(profile,kind){return profile==='@shared'?{scope:'shared',kind}:{profile,kind};}

  async function mount(doc){
    const $=id=>doc.getElementById(id);
    let csrf='',section='agents',profiles=[],integrations=[],integration=null,integrationDirty=false,creatingIntegration=false;
    let integrationBusy=false,assetNew=false,assetBusy=false,catalogSerial=0,assistantSession='',assistantBusy=false;
    const labels={skill:'Skills',tool:'Tools',plugin:'Plugins',script:'Scripts'};
    const statuses={active:'Activa',installed:'Instalada',draft:'Borrador sin activar',shadowed:'Conflicto de nombre'};
    const aliases={'app-config':'Configurador'};
    function feedback(text){$('hub-message').textContent=text;}
    async function api(path,options={}){
      const response=await fetch(path,{...options,headers:{...options.headers,...(options.method&&options.method!=='GET'?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{})}});
      if(response.status===401){location.href='/';throw new Error('Inicia sesión para continuar.');}
      if(!response.ok){let detail='No se pudo completar la operación.';try{detail=(await response.json()).detail||detail;}catch{}throw Object.assign(new Error(typeof detail==='string'?detail:'Solicitud inválida.'),{status:response.status});}
      return response;
    }
    const json=async(path,options)=>await(await api(path,options)).json();
    function icon(name){const svg=doc.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');const use=doc.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','/static/icons.svg#'+name);svg.append(use);return svg;}
    const config=HermesConfigEditor.mount({document:doc,api,icon});
    function button(text,action,style='secondary'){const node=doc.createElement('button');node.type='button';node.className=style;node.textContent=text;node.onclick=action;return node;}
    function hasDraft(){return integrationDirty||editor.get().dirty||assetNew||$('assistant-message').value.trim();}
    function mayLeave(){if(integrationBusy||assetBusy||assistantBusy||editor.get().phase==='saving'){feedback('Espera a que termine la operación.');return false;}return !hasDraft()||window.confirm('Hay cambios o un mensaje sin enviar. ¿Quieres descartarlos?');}
    async function openSection(next){
      if(next===section)return;
      if(!mayLeave())return;
      editor.clear();assetNew=false;integrationDirty=false;$('assistant-message').value='';$('integration-credential').value='';$('integration-form').hidden=true;$('asset-create-form').hidden=true;
      section=next;
      for(const node of doc.querySelectorAll('[data-settings-section]'))node.hidden=node.dataset.settingsSection!==next;
      for(const node of doc.querySelectorAll('[data-settings-nav]'))node.setAttribute('aria-current',node.dataset.settingsNav===next?'page':'false');
      feedback('');history.replaceState(null,'','#'+next);
      window.scrollTo(0,0);
      if(next==='integrations')await loadIntegrations();
      if(next==='customization')await loadAssets();
    }
    const assetQuery=(key={})=>'?'+new URLSearchParams({...assetSelection($('asset-profile').value,$('asset-kind').value),...key});
    const editor=createEditor({
      read:key=>json('/api/customization/item?'+new URLSearchParams(key)),
      write:(key,data)=>json('/api/customization/item?'+new URLSearchParams(key),{method:'PUT',body:JSON.stringify(data)}),
      change(state){
        $('asset-title').textContent=state.item?.name||'Selecciona un archivo';
        $('asset-content').disabled=!state.item||state.phase==='loading'||state.phase==='saving';
        $('asset-content').readOnly=!state.item?.editable;
        if($('asset-content').value!==state.content)$('asset-content').value=state.content;
        $('asset-save').disabled=!state.dirty||!state.item?.editable||['saving','conflict'].includes(state.phase);
        $('asset-reload').disabled=!state.item||state.phase==='saving';
        $('asset-new').disabled=state.phase==='saving';
        $('asset-profile').disabled=state.phase==='saving';$('asset-kind').disabled=state.phase==='saving';
        $('asset-state').textContent=state.item?(state.item.id.startsWith('shared:')?'Compartida para todos · '+statuses[state.item.status]:state.item.editable?'Editable · '+statuses[state.item.status]:'Solo lectura · crea una copia para adaptarlo'):'';
        $('asset-feedback').textContent=state.error||({loading:'Cargando…',saving:'Guardando…',saved:'Cambios guardados.'}[state.phase]||(state.dirty?'Cambios sin guardar.':''));
      }
    });
    async function listProfiles(){
      profiles=(await json('/api/settings/profiles')).profiles;
      const picker=$('asset-profile');picker.replaceChildren();$('agent-list').replaceChildren();
      const shared=doc.createElement('option');shared.value='@shared';shared.textContent='Todos los agentes';picker.append(shared);
      for(const profile of profiles){
        const label=aliases[profile.id]||profile.name;
        const option=doc.createElement('option');option.value=profile.id;option.textContent=label;picker.append(option);
        const row=doc.createElement('div');row.className='settings-row';
        const info=doc.createElement('div'),title=doc.createElement('h3'),note=doc.createElement('p');title.textContent=label;
        note.textContent=profile.id==='app-config'?'Especialista en configuración de aplicaciones.':profile.id==='carpediev'?'Tu asistente de Telegram. Identidad independiente.':profile.id==='default'?'Tu asistente del espacio web.':'Perfil persistente de Hermes.';
        info.append(title,note);row.append(info,button('Editar agente',async()=>{await config.open();await config.selectProfile(profile.id);}));$('agent-list').append(row);
      }
      picker.value=profiles[0]?.id||'@shared';
      $('agent-note').textContent=profiles.length+' agentes disponibles. Los nuevos perfiles persistentes aparecen automáticamente.';
    }
    async function loadIntegrations(){
      try{const data=await json('/api/integrations');integrations=data.items;$('integration-list').replaceChildren();$('integration-note').textContent=data.note;
        if(!integrations.length)$('integration-list').textContent='Todavía no hay APIs configuradas. Crea tu primera integración.';
        for(const item of integrations){const row=doc.createElement('div');row.className='settings-row';const info=doc.createElement('div'),name=doc.createElement('h3'),note=doc.createElement('p');name.textContent=item.name;note.textContent=(item.enabled?'Habilitada':'Deshabilitada')+' · '+(item.configured?'Clave guardada':'Sin clave')+' · Conexión no verificada';info.append(name,note);row.append(info,button('Editar',()=>selectIntegration(item)));$('integration-list').append(row);}
      }catch(error){feedback(error.message);}
    }
    function selectIntegration(item){
      if(integrationBusy)return;
      if(integrationDirty&&!window.confirm('¿Descartar los cambios de la integración?'))return;
      integration=item;creatingIntegration=!item;integrationDirty=false;
      $('integration-form').hidden=false;$('integration-id').disabled=!!item;
      for(const key of ['id','name','url','description'])$('integration-'+key).value=item?.[key]||'';
      $('integration-enabled').checked=item?.enabled??true;$('integration-credential').value='';$('integration-remove').checked=false;
      $('integration-key-note').textContent=item?.configured?'Hay una clave guardada. Deja el campo vacío para conservarla.':'La clave es opcional. Se guarda cifrada y no se vuelve a mostrar.';
      $('integration-name').focus();
    }
    $('integration-form').addEventListener('input',()=>{integrationDirty=true;});
    $('integration-new').onclick=()=>selectIntegration(null);
    $('integration-cancel').onclick=()=>{if(integrationBusy)return;if(!integrationDirty||window.confirm('¿Descartar los cambios de la integración?')){$('integration-form').hidden=true;integrationDirty=false;$('integration-credential').value='';}};
    $('integration-form').onsubmit=async event=>{
      event.preventDefault();if(integrationBusy)return;integrationBusy=true;$('integration-save').disabled=true;
      const payload={name:$('integration-name').value,url:$('integration-url').value,description:$('integration-description').value,enabled:$('integration-enabled').checked,credential:$('integration-credential').value,remove_credential:$('integration-remove').checked};
      if(creatingIntegration)payload.id=$('integration-id').value;else payload.revision=integration.revision;
      for(const control of $('integration-form').elements)control.disabled=true;
      try{await json('/api/integrations'+(creatingIntegration?'':'/'+encodeURIComponent(integration.id)),{method:creatingIntegration?'POST':'PUT',body:JSON.stringify(payload)});integrationDirty=false;$('integration-credential').value='';$('integration-form').hidden=true;await loadIntegrations();feedback('Integración guardada. La API no se ha probado; habilitarla no amplía los permisos de red.');}
      catch(error){feedback(error.message);}finally{integrationBusy=false;for(const control of $('integration-form').elements)control.disabled=false;$('integration-id').disabled=!creatingIntegration;}
    };
    async function loadAssets(){
      const serial=++catalogSerial;
      $('asset-list').textContent='Cargando archivos…';
      try{const data=await json('/api/customization'+assetQuery());if(serial!==catalogSerial)return;$('asset-list').replaceChildren();$('customization-note').textContent='Elige Todos los agentes para crear o editar skills compartidas: se conserva una sola versión. Una skill específica con el mismo nombre tiene prioridad. Skills propias activas; skills de proyecto editables. Las incorporaciones aprobadas y de fábrica son de solo lectura. Tools, plugins y scripts permanecen como borradores.';
        if(!data.items.length)$('asset-list').textContent='No hay archivos en esta categoría.';
        for(const item of data.items){const node=button(item.name,async()=>{if(assetBusy)return;if((editor.get().dirty||assetNew)&&!window.confirm('¿Descartar el borrador actual?'))return;assetNew=false;$('asset-create-form').hidden=true;await editor.load({...assetSelection($('asset-profile').value,$('asset-kind').value),id:item.id},true);},'asset-list-item');const state=doc.createElement('small');state.textContent=(item.id.startsWith('shared:')?'Compartida':item.editable?'Editable':'Solo lectura')+' · '+statuses[item.status];node.append(state);$('asset-list').append(node);}
      }catch(error){if(serial===catalogSerial)$('asset-list').textContent=error.message;}
    }
    let previousProfile='',previousKind='skill';
    async function changeAssets(){
      if(assetBusy||((editor.get().dirty||assetNew)&&!window.confirm('¿Descartar el borrador para cambiar de categoría o agente?'))){$('asset-profile').value=previousProfile;$('asset-kind').value=previousKind;return;}
      if($('asset-kind').value!=='skill'&&$('asset-profile').value==='@shared')$('asset-profile').value=profiles[0]?.id||'';
      $('asset-profile').querySelector('option[value="@shared"]').disabled=$('asset-kind').value!=='skill';
      previousProfile=$('asset-profile').value;previousKind=$('asset-kind').value;editor.clear();assetNew=false;$('asset-create-form').hidden=true;await loadAssets();
    }
    $('asset-profile').onchange=changeAssets;$('asset-kind').onchange=changeAssets;
    $('asset-content').oninput=()=>editor.edit($('asset-content').value);
    $('asset-save').onclick=()=>editor.save();
    $('asset-reload').onclick=async()=>{const current=editor.get();if(!current.item||current.phase==='saving')return;if(current.dirty&&!window.confirm('¿Descartar cambios y recargar el archivo?'))return;await editor.load({...assetSelection($('asset-profile').value,$('asset-kind').value),id:current.item.id},true);};
    $('asset-new').onclick=()=>{if(assetBusy||editor.get().phase==='saving')return;if(editor.get().dirty&&!window.confirm('¿Descartar cambios para crear un archivo?'))return;editor.clear();assetNew=true;$('asset-create-form').hidden=false;$('asset-create-id').value='';$('asset-create-content').value=$('asset-kind').value==='skill'?'---\nname: my-skill\ndescription: Describe the owner-authored capability and when it applies.\n---\n\n# My skill\n\n':'';$('asset-create-id').focus();};
    $('asset-create-cancel').onclick=()=>{if(assetBusy)return;if(window.confirm('¿Descartar este archivo nuevo?')){assetNew=false;$('asset-create-form').hidden=true;}};
    $('asset-create-form').onsubmit=async event=>{event.preventDefault();if(assetBusy)return;assetBusy=true;$('asset-create-save').disabled=true;try{const item=await json('/api/customization'+assetQuery(),{method:'POST',body:JSON.stringify({id:$('asset-create-id').value,content:$('asset-create-content').value})});assetNew=false;$('asset-create-form').hidden=true;await loadAssets();await editor.load({...assetSelection($('asset-profile').value,$('asset-kind').value),id:item.id},true);feedback('Archivo creado. Los borradores de tools, plugins y scripts no se ejecutan ni se activan.');}catch(error){feedback(error.message);}finally{assetBusy=false;$('asset-create-save').disabled=false;}};
    function ask(){
      $('assistant-panel').hidden=false;
      const context=section==='customization'?'Customización: '+labels[$('asset-kind').value]+', agente '+($('asset-profile').value==='@shared'?'todos los agentes':$('asset-profile').value)+(editor.get().item?', archivo '+editor.get().item.name:''):section==='integrations'?'Integraciones API (sin claves en el chat).':'Configuración de agentes.';
      $('assistant-context').textContent=context;$('assistant-message').focus();
    }
    for(const node of doc.querySelectorAll('[data-ask-config]'))node.onclick=ask;
    $('assistant-close').onclick=()=>{if(assistantBusy)return;$('assistant-panel').hidden=true;};
    function conversation(role,text){const node=doc.createElement('div');node.className='assistant-bubble';const label=doc.createElement('strong'),body=doc.createElement('p');label.textContent=role;body.textContent=text;node.append(label,body);$('assistant-conversation').append(node);}
    $('assistant-form').onsubmit=async event=>{
      event.preventDefault();if(assistantBusy)return;const message=$('assistant-message').value.trim();if(!message)return;assistantBusy=true;$('assistant-send').disabled=true;$('assistant-message').disabled=true;const context=$('assistant-context').textContent;
      $('assistant-state').textContent='Configurador está trabajando…';conversation('Tú',message);
      try{const data=await json('/api/settings/assistant',{method:'POST',body:JSON.stringify({message:'Contexto de la interfaz (sin permisos adicionales): '+context+'\n\nSolicitud del propietario: '+message,...(assistantSession?{session_id:assistantSession}:{})})});assistantSession=data.session_id;conversation('Configurador',data.reply);$('assistant-message').value='';$('assistant-state').textContent='Respuesta recibida.';}
      catch(error){$('assistant-state').textContent=error.message;}finally{assistantBusy=false;$('assistant-send').disabled=false;$('assistant-message').disabled=false;}
    };
    for(const node of doc.querySelectorAll('[data-settings-nav]'))node.onclick=()=>openSection(node.dataset.settingsNav);
    for(const node of doc.querySelectorAll('a[data-leave-settings]'))node.onclick=event=>{if(!mayLeave()){event.preventDefault();return;}if(!config.requestLeave(()=>{location.href=node.href;}))event.preventDefault();};
    window.addEventListener('beforeunload',event=>{if(hasDraft()||integrationBusy||assetBusy||assistantBusy){event.preventDefault();event.returnValue='';}});
    try{csrf=(await json('/api/me')).csrf;await listProfiles();previousProfile=$('asset-profile').value;$('hub-loading').hidden=true;const initial=location.hash.slice(1);if(['integrations','customization'].includes(initial))await openSection(initial);}
    catch(error){feedback(error.message);$('hub-loading').textContent='No se pudo cargar la configuración. Recarga la página.';}
  }
  return {createEditor,assetSelection,mount};
});
