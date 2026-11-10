/* Owner-only, explicit-save configuration editor. Drafts remain in memory. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.HermesConfigEditor=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  function connection(data){
    if(!data)return {state:'connecting',label:'Conectando con Hermes'};
    if(data.hermes==='private')return {state:'offline',label:'Acceso privado'};
    if(data.hermes==='online')return data.provider_configured?{state:'online',label:'Hermes en línea'}:{state:'unconfigured',label:'Hermes disponible; configura OpenAI en Secretos'};
    if(['starting','connecting'].includes(data.hermes))return {state:'connecting',label:'Hermes está iniciando'};
    return {state:'offline',label:'Hermes sin conexión; revisa Estado'};
  }
  function create({list,read,write,change=()=>{}}){
    let serial=0;
    const state={opened:false,files:[],selected:null,name:'',content:'',baseline:'',revision:'',note:'',phase:'idle',error:'',pending:null};
    const get=()=>({...state,files:[...state.files],dirty:state.content!==state.baseline});
    const emit=()=>change(get());
    function apply(file){state.selected=file.id;state.name=file.name;state.content=file.content;state.baseline=file.content;state.revision=file.revision;state.note=file.note||'';}
    async function load(id){
      if(state.phase==='saving')return;
      const token=++serial;state.selected=id;state.name=state.files.find(f=>f.id===id)?.name||'';state.content='';state.baseline='';state.revision='';state.note='';state.error='';state.phase='loading';emit();
      try{const file=await read(id);if(token!==serial||!state.opened)return;apply(file);state.phase='ready';}
      catch(error){if(token!==serial||!state.opened)return;state.error=error.message;state.phase='error';}
      emit();
    }
    function guard(action){if(state.phase==='saving')return false;if(get().dirty){state.pending=action;emit();return false;}return true;}
    function close(){serial++;state.opened=false;state.pending=null;emit();}
    return {
      get,
      async open(){
        if(state.opened)return;
        const token=++serial;Object.assign(state,{opened:true,files:[],selected:null,name:'',content:'',baseline:'',revision:'',note:'',phase:'loading',error:'',pending:null});emit();
        try{const result=await list();if(token!==serial||!state.opened)return;state.files=(result.files||[]).filter(file=>['config','soul','user'].includes(file.id));if(state.files.length)await load(state.files[0].id);else{state.phase='empty';emit();}}
        catch(error){if(token!==serial||!state.opened)return;state.phase='error';state.error=error.message;emit();}
      },
      edit(content){if(!state.revision||['loading','saving'].includes(state.phase))return;state.content=content;if(state.phase!=='conflict'){state.phase='ready';state.error='';}emit();},
      async select(id){if(!state.files.some(f=>f.id===id)||id===state.selected)return;if(guard({kind:'select',id}))await load(id);},
      async reload(){if(!state.selected)return;if(guard({kind:'reload'}))await load(state.selected);},
      async save(){
        if(!state.opened||!get().dirty||!state.revision||['saving','loading','conflict'].includes(state.phase))return;
        const id=state.selected,payload={content:state.content,revision:state.revision},token=++serial;state.phase='saving';state.pending=null;state.error='';emit();
        try{const file=await write(id,payload);if(token!==serial)return;apply(file);state.phase='saved';}
        catch(error){if(token!==serial)return;state.phase=error.status===409?'conflict':'error';state.error=error.message;}
        emit();
      },
      requestClose(){if(!guard({kind:'close'}))return false;close();return true;},
      requestLeave(callback){if(!guard({kind:'leave',callback})){state.opened=true;emit();return false;}callback();return true;},
      keep(){state.pending=null;emit();},
      async discard(){const action=state.pending;if(!action||state.phase==='saving')return;state.pending=null;state.content=state.baseline;if(action.kind==='close')close();else if(action.kind==='leave'){close();action.callback();}else await load(action.id||state.selected);}
    };
  }
  function mount({document:doc,api,icon}){
    const $=id=>doc.getElementById(id),dialog=$('config-dialog'),editor=$('config-content'),opener=$('config-open');
    let renderedSelection=null,focusAfterLoad=false,lastPhase='';
    const controller=create({
      list:async()=>await(await api('/api/settings/files')).json(),
      read:async id=>await(await api('/api/settings/files/'+id)).json(),
      write:async(id,payload)=>await(await api('/api/settings/files/'+id,{method:'PUT',body:JSON.stringify(payload)})).json(),
      change(state){
        if(state.opened&&!dialog.open){dialog.showModal();$('config-close').focus();focusAfterLoad=true;}
        if(!state.opened&&dialog.open){dialog.close();opener.focus();}
        const busy=state.phase==='saving',loading=state.phase==='loading';
        dialog.setAttribute('aria-busy',String(busy||loading));
        editor.disabled=busy||loading||!state.revision;editor.readOnly=busy;
        if(editor.value!==state.content)editor.value=state.content;
        $('config-current-name').textContent=state.name||'Archivos de Hermes';
        $('config-scope').textContent=state.selected==='config'?'Vista editable sin secretos':'Instrucciones del asistente';
        $('config-dirty').hidden=!state.dirty;
        $('config-note').textContent=state.note;
        const labels={loading:'Cargando archivo…',saving:'Guardando cambios…',saved:'Cambios guardados.',empty:'No hay archivos disponibles.',conflict:'El archivo cambió. Tu borrador se conserva. Recarga para revisar la versión actual.',error:'No se pudo completar la operación.'};
        $('config-message').textContent=(labels[state.phase]||'')+(state.error?' '+state.error:'');
        $('config-message').dataset.state=state.phase;
        $('config-save').disabled=!state.dirty||busy||loading||!state.revision||state.phase==='conflict';
        $('config-reload').disabled=busy||!state.selected;
        $('config-close').disabled=busy;
        $('config-discard').hidden=!state.pending;
        $('config-discard-description').textContent=state.pending?.kind==='leave'?'Hay cambios sin guardar. Descártalos para cerrar sesión.':'Hay cambios sin guardar. Descártalos para continuar.';
        const fileList=$('config-files');
        if(renderedSelection!==state.selected||fileList.children.length!==state.files.length){
          fileList.replaceChildren();for(const file of state.files){const button=doc.createElement('button');button.type='button';button.className='config-file';button.setAttribute('aria-label','Editar '+file.name);button.title=file.description||file.name;button.setAttribute('aria-current',String(file.id===state.selected));button.append(icon('document-text'));const label=doc.createElement('span');label.textContent=file.name;button.append(label);button.addEventListener('click',()=>{focusAfterLoad=true;controller.select(file.id);});button.dataset.fileId=file.id;fileList.append(button);}renderedSelection=state.selected;
        }
        for(const button of fileList.children)button.disabled=busy;
        if(focusAfterLoad&&lastPhase==='loading'&&state.phase==='ready'){editor.focus();editor.setSelectionRange(0,0);editor.scrollTop=0;focusAfterLoad=false;}
        if(state.pending&&lastPhase!==state.pending.kind)$('config-keep').focus();
        lastPhase=state.pending?.kind||state.phase;
      }
    });
    opener.addEventListener('click',()=>controller.open());
    editor.addEventListener('input',()=>controller.edit(editor.value));
    $('config-save').addEventListener('click',()=>controller.save());
    $('config-reload').addEventListener('click',()=>controller.reload());
    $('config-close').addEventListener('click',()=>controller.requestClose());
    $('config-keep').addEventListener('click',()=>{controller.keep();editor.focus();});
    $('config-discard-confirm').addEventListener('click',()=>controller.discard());
    dialog.addEventListener('cancel',event=>{event.preventDefault();controller.requestClose();});
    dialog.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();if(!controller.get().pending)controller.save();}});
    doc.defaultView.addEventListener('beforeunload',event=>{if(controller.get().dirty){event.preventDefault();event.returnValue='';}});
    return controller;
  }
  return {create,connection,mount};
});
