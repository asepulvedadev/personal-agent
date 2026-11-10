/* Display-only execution metadata. Never retain arguments, output or reasoning. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.HermesActivity=api;})(typeof globalThis==='object'?globalThis:this,function(){
 'use strict';
 const labels={running:'En curso',completed:'Finalizada',failed:'Con error',recorded:'Resultado registrado',unconfirmed:'Sin confirmación',stopped:'Detenida'};
 const tools={terminal:'Terminal',web_search:'Búsqueda web',web_extract:'Lectura web',browser_navigate:'Navegador',browser_snapshot:'Lectura del navegador',browser_click:'Interacción con el navegador',skill_view:'Consultar skill',skills_list:'Listar skills',skill_manage:'Gestionar skills',delegate_task:'Delegar tarea',memory:'Memoria',session_search:'Buscar conversaciones',file_read:'Leer archivo',file_write:'Escribir archivo',file_edit:'Editar archivo',execute_code:'Ejecutar código'};
 function safeName(value){return typeof value==='string'&&/^[A-Za-z][A-Za-z0-9_.\/-]{0,99}$/.test(value)&&!value.includes('..')&&!/^(sk-|key-|bearer|gh[pousr]_)/i.test(value)?value:null;}
 function callId(value){return typeof value==='string'&&/^[A-Za-z0-9_-]{1,160}$/.test(value)&&!/^sk-/i.test(value)?value:null;}
 function skillName(tool,args){
  if(tool!=='skill_view')return null;
  if(typeof args==='string'){if(args.length>8192)return null;try{args=JSON.parse(args);}catch{return null;}}
  return args&&typeof args==='object'&&!Array.isArray(args)?safeName(args.name):null;
 }
 class Tracker{
  constructor(){this.items=[];this.overflow=false;}
  add(tool,args,id,status='running'){
   tool=safeName(tool);id=callId(id);if(!tool||tool==='_thinking')return null;
   if(id){const existing=this.items.find(item=>item.id===id);if(existing)return existing;}
   if(this.items.length>=200){this.overflow=true;return null;}
   const item={tool,skill:skillName(tool,args),id,status};this.items.push(item);return item;
  }
  complete(tool,id,status){
   id=callId(id);tool=safeName(tool);
   let matches=id?this.items.filter(item=>item.id===id):this.items.filter(item=>item.tool===tool&&item.status==='running');
   if(matches.length===1){matches[0].status=status;return;}
   if(matches.length>1)for(const item of matches)item.status='unconfirmed';
   // Hermes session SSE may omit call IDs. Do not assign parallel results arbitrarily.
   this.add(tool,null,id,status);
  }
  event(type,data){
   if(!data||typeof data!=='object')return;
   const tool=data.tool_name||data.tool||data.name;
   if(tool==='_thinking')return;
   if(type==='tool.started')this.add(tool,data.args||data.arguments,data.tool_call_id);
   if(type==='tool.completed'||type==='tool.failed')this.complete(tool,data.tool_call_id,
     type==='tool.failed'||data.error===true||data.is_error===true?'failed':'completed');
  }
  history(row){
   if(!row||row.display_kind==='hidden')return;
   if(row.role==='assistant'&&Array.isArray(row.tool_calls)){
    for(const call of row.tool_calls.slice(0,200))if(call&&call.function)this.add(call.function.name,call.function.arguments,call.id);
   }
   if(row.role==='tool')this.complete(row.tool_name,row.tool_call_id,'recorded');
  }
  finish(state){for(const item of this.items)if(item.status==='running')item.status=state==='cancelled'?'stopped':'unconfirmed';}
 }
 function view(container,before=null){
  const tracker=new Tracker();const details=document.createElement('details');details.className='agent-activity';details.open=true;
  const summary=document.createElement('summary');const list=document.createElement('ol');list.className='activity-list';details.append(summary,list);
  function render(){
   if(!tracker.items.length)return;
   if(!details.parentNode)container.insertBefore(details,before);
   const skills=new Set(tracker.items.map(item=>item.skill).filter(Boolean));
   summary.textContent='Actividad del agente · '+tracker.items.length+(tracker.overflow?'+':'')+' '+(tracker.items.length===1?'acción':'acciones')+(skills.size?' · '+skills.size+' '+(skills.size===1?'skill':'skills'):'');
   list.replaceChildren();
   for(const item of tracker.items){
    const row=document.createElement('li');row.className='activity-row';row.dataset.state=item.status;
    const mark=document.createElement('span');mark.className='activity-mark';mark.setAttribute('aria-hidden','true');
    const name=document.createElement('span');name.className='activity-name';name.textContent=item.skill?'Skill · '+item.skill:tools[item.tool]||'Herramienta · '+item.tool;
    name.title=item.tool;
    const state=document.createElement('span');state.className='activity-state';state.textContent=labels[item.status];
    row.append(mark,name,state);list.append(row);
   }
  }
  return {tracker,render,event(type,data){tracker.event(type,data);render();},history(row){tracker.history(row);},finish(state){tracker.finish(state);render();}};
 }
 return {Tracker,view,labels};
});
