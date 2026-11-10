/* Dollar-mention completion from the installed Hermes catalogue; selection never sends. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.HermesSkills=api;})(typeof globalThis==='object'?globalThis:this,function(){
 'use strict';
 const categories={'web-development':'Desarrollo web','software-development':'Desarrollo de software',creative:'Creatividad',productivity:'Productividad',research:'Investigación',devops:'Infraestructura',github:'GitHub',mlops:'Aprendizaje automático','autonomous-ai-agents':'Agentes de IA','social-media':'Redes sociales',finance:'Finanzas',security:'Seguridad'};
 const safeName=name=>typeof name==='string'&&/^[A-Za-z][A-Za-z0-9_./-]{0,99}$/.test(name)&&!name.includes('..')&&!/^(sk-|ghp_|bearer)/i.test(name);
 function token(value,caret){
  const match=value.slice(0,caret).match(/(?:^|\s)\$([\p{L}][\p{L}\p{N}_./-]{0,99}|)$/u);
  if(!match)return null;
  const query=match[1];const start=caret-query.length-1;
  const suffix=value.slice(caret).match(/^[\p{L}\p{N}_./-]*/u)[0];
  return {start,end:caret+suffix.length,query};
 }
 function insert(value,range,name){
  const tail=value.slice(range.end);const space=tail.startsWith(' ')?'':' ';
  return {value:value.slice(0,range.start)+'$'+name+space+tail,cursor:range.start+1+name.length+1};
 }
 const fold=value=>String(value||'').normalize('NFD').replace(/\p{M}/gu,'').toLowerCase();
 function filter(rows,query){
  const q=fold(query);
  return rows.filter(row=>fold([row.name,row.description,row.category,categories[row.category]].join(' ')).includes(q))
   .sort((a,b)=>(fold(b.name).startsWith(q)?1:0)-(fold(a.name).startsWith(q)?1:0));
 }
 function mount({textarea,panel,list,status,fetchSkills,blocked=()=>false}){
  const doc=textarea.ownerDocument;let catalog=null,loadedAt=0,inflight=null,generation=0,active=0,options=[],nodes=[],range=null,dismissed='',composing=false;
  const signature=()=>textarea.value+'|'+textarea.selectionStart;
  function close(dismiss=false){generation++;panel.hidden=true;textarea.removeAttribute('aria-activedescendant');textarea.setAttribute('aria-expanded','false');if(dismiss)dismissed=signature();}
  async function load(){
   if(catalog&&Date.now()-loadedAt<60000)return catalog;
   if(!inflight)inflight=Promise.resolve().then(fetchSkills).then(rows=>{
    if(!Array.isArray(rows))throw new Error('Invalid catalogue');
    catalog=rows.filter(row=>row&&safeName(row.name));loadedAt=Date.now();return catalog;
   }).finally(()=>{inflight=null;});
   return inflight;
  }
  function highlight(){
   for(const [index,node] of nodes.entries())node.setAttribute('aria-selected',String(index===active));
   if(nodes[active]){textarea.setAttribute('aria-activedescendant',nodes[active].id);nodes[active].scrollIntoView?.({block:'nearest'});}
   else textarea.removeAttribute('aria-activedescendant');
  }
  function choose(index){
   if(blocked()||!options[index])return;
   const current=token(textarea.value,textarea.selectionStart);
   if(!current||textarea.selectionStart!==textarea.selectionEnd)return close();
   const result=insert(textarea.value,current,options[index].name);
   if(result.value.length>(textarea.maxLength||20000)){status.textContent='El mensaje es demasiado largo para insertar esta skill.';return;}
   textarea.value=result.value;textarea.setSelectionRange(result.cursor,result.cursor);close();textarea.focus();
   textarea.dispatchEvent(new Event('input',{bubbles:true}));
  }
  function render(){
   list.replaceChildren();nodes=[];
   options.forEach((row,index)=>{
    const option=doc.createElement('div');option.className='skill-option';option.id='skill-option-'+index;option.setAttribute('role','option');option.tabIndex=-1;
    const name=doc.createElement('span');name.className='skill-name';name.textContent='$'+row.name;
    const category=doc.createElement('small');category.textContent=categories[row.category]||'Skill instalada';option.append(name,category);
    option.addEventListener('mousedown',event=>event.preventDefault());option.addEventListener('click',()=>choose(index));
    nodes.push(option);list.append(option);
   });
   status.textContent=options.length?options.length+' '+(options.length===1?'skill disponible':'skills disponibles')+' · ↑ ↓ para navegar · Enter o Tab para elegir':'No hay skills que coincidan.';
   highlight();
  }
  async function update(){
   const current=token(textarea.value,textarea.selectionStart);
   if(blocked()||textarea.disabled||composing||!current||textarea.selectionStart!==textarea.selectionEnd||signature()===dismissed)return close();
   range=current;const stamp=++generation;panel.hidden=false;textarea.setAttribute('aria-expanded','true');
   options=[];nodes=[];list.replaceChildren();textarea.removeAttribute('aria-activedescendant');status.textContent='Cargando skills del agente…';
   try{
    const rows=await load();if(stamp!==generation||blocked()||textarea.disabled)return;
    options=filter(rows,range.query);active=0;render();
   }catch{
    if(stamp!==generation)return;
    options=[];nodes=[];list.replaceChildren();textarea.removeAttribute('aria-activedescendant');
    status.textContent='No se pudieron cargar las skills. Vuelve a escribir $ para reintentar.';
   }
  }
  function keydown(event){
   if(panel.hidden||event.isComposing||composing)return false;
   if(event.key==='Escape'){event.preventDefault();close(true);return true;}
   if((event.key==='Tab'||event.key==='Enter')&&event.shiftKey){close();return false;}
   if(event.key==='ArrowDown'||event.key==='ArrowUp'){
    event.preventDefault();if(options.length){active=(active+(event.key==='ArrowDown'?1:-1)+options.length)%options.length;highlight();}return true;
   }
   if(event.key==='Enter'||event.key==='Tab'){
    if(options.length){event.preventDefault();choose(active);return true;}
    close(true);if(event.key==='Enter'){event.preventDefault();return true;}
   }
   return false;
  }
  textarea.addEventListener('input',()=>{dismissed='';update();});textarea.addEventListener('click',update);
  textarea.addEventListener('keyup',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key))update();});
  textarea.addEventListener('compositionstart',()=>{composing=true;close();});textarea.addEventListener('compositionend',()=>{composing=false;update();});
  doc.addEventListener('pointerdown',event=>{if(event.target!==textarea&&!panel.contains(event.target))close();});
  doc.addEventListener('focusin',event=>{if(event.target!==textarea&&!panel.contains(event.target))close();});
  return {update,close,keydown};
 }
 return {token,insert,filter,mount};
});
