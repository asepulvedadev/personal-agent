/* Project workspace: owner and bearer-scoped client views share no owner-side state. */
'use strict';
(function () {
  const STATES = {todo:'Por ejecutar',doing:'En ejecución',blocked:'Bloqueada',done:'Finalizada'};
  const PROJECT_STATES = {draft:'Borrador',active:'Activo',paused:'Pausado',finished:'Finalizado',archived:'Archivado'};
  const PRIORITIES = {high:'Alta',normal:'Normal',low:'Baja'};
  const APPROVALS = {pending:'Pendiente de aprobación',approved:'Aprobado'};
  const CONTACT_STATES = {contact:'Contactos',client:'Clientes',archived:'Archivados'};
  const KINDS = {comment:'Comentario',question:'Pregunta',answer:'Respuesta'};
  const id = () => globalThis.crypto.randomUUID().replace(/-/g,'');
  const duration = minutes => minutes == null ? 'Sin estimar' : `${Math.floor(minutes/60)} h ${minutes%60} min`;
  function summary(project) {
    const tasks=project.tasks||[];
    const questions=tasks.reduce((count,t)=>{
      let pending=0;
      for(const c of t.comments||[]) {if(c.kind==='question')pending++;if(c.kind==='answer')pending=0;}
      return count+pending;
    },0);
    return {total:tasks.length,done:tasks.filter(t=>t.status==='done').length,
      percent:tasks.length?Math.round(tasks.filter(t=>t.status==='done').length/tasks.length*100):0,
      entryCount:tasks.reduce((n,t)=>n+(t.time_entries||[]).length,0),
      minutes:tasks.reduce((n,t)=>n+(t.time_entries||[]).reduce((a,e)=>a+(e.minutes||0),0),0),
      estimate:tasks.length&&tasks.some(t=>t.estimated_minutes==null)?null:tasks.reduce((n,t)=>n+(t.estimated_minutes||0),0),
      blocked:tasks.filter(t=>t.status==='blocked').length,questions};
  }
  function normalize(data) {
    const result={...data};
    for(const key of ['client_id','group_id','task_id'])if(key in result&&result[key]==='')result[key]=null;
    for(const key of ['estimated_minutes','minutes'])if(key in result)result[key]=result[key]===''?null:Number(result[key]);
    return result;
  }
  function validateTask(data) {
    if(data.status==='done'&&(!data.resolution?.trim()||!data.evidence?.trim()))throw Error('Para terminar, registra la resolución real y su evidencia.');
    if(data.status==='blocked'&&(!data.blocker?.trim()||!data.next_step?.trim()))throw Error('Indica el bloqueo y el siguiente paso antes de guardar.');
    if(data.estimated_minutes!=null&&(!Number.isInteger(data.estimated_minutes)||data.estimated_minutes<0))throw Error('La estimación debe ser un número entero de minutos.');
  }
  class ProjectAPI {
    constructor({client=false,token='',csrf='',fetcher=globalThis.fetch}={}){Object.assign(this,{client,token,csrf,fetcher});}
    async request(path,{method='GET',body,blob=false,retry=false}={}){
      const headers={};if(this.client)headers.Authorization=`Bearer ${this.token}`;
      if(body!==undefined){headers['Content-Type']='application/json';if(!this.client)headers['X-CSRF-Token']=this.csrf;}
      const options={method,headers,credentials:this.client?'omit':'same-origin',cache:'no-store',referrerPolicy:'no-referrer'};
      if(body!==undefined)options.body=JSON.stringify(body);
      let response;
      try{response=await this.fetcher.call(globalThis,path,options);}catch(error){
        if(!retry)throw Error('No hay conexión. Revisa tu red e inténtalo de nuevo.');
        try{response=await this.fetcher.call(globalThis,path,options);}catch{throw Error('No se pudo confirmar el guardado. Recarga antes de intentarlo de nuevo para comprobar si se aplicó.');}
      }
      if(!response.ok){
        const messages={401:this.client?'Este enlace no es válido o ya fue revocado. Solicita uno nuevo.':'Inicia sesión para acceder a tus proyectos.',403:'No tienes permiso para realizar esta acción.',404:'No se encontró el recurso. Puede que ya no esté disponible.',409:'Otra sesión actualizó el proyecto. Tus cambios no se guardaron: recarga los datos y revisa antes de intentarlo de nuevo.',413:'El archivo supera el tamaño permitido.',422:'Revisa los campos. No se pudo guardar esta información.',400:'Revisa los campos y los requisitos de la tarea antes de guardar.'};
        const error=Error(messages[response.status]||'El servicio no está disponible. Inténtalo de nuevo más tarde.');error.status=response.status;throw error;
      }
      return blob?response.blob():response.json();
    }
    project(projectId){return this.request(this.client?'/api/project-view':`/api/projects/${encodeURIComponent(projectId)}`);}
    index(){if(this.client)return Promise.reject(Error('El cliente no puede consultar el índice.'));return this.request('/api/projects');}
    async command(action,data,project,operation=id()){
      if(this.client)throw Error('El cliente no puede modificar el proyecto.');
      const body={action,data,operation_id:operation};if(project){body.project_id=project.id;body.revision=project.revision;}
      return this.request('/api/projects/command',{method:'POST',body,retry:true});
    }
    comment(taskId,text,kind,operation=id()){
      if(!this.client)throw Error('Usa el comando de comentarios del propietario.');
      return this.request('/api/project-view/comments',{method:'POST',body:{task_id:taskId,text,kind,operation_id:operation},retry:true});
    }
    download(projectId,documentId){return this.request(this.client?`/api/project-view/documents/${encodeURIComponent(documentId)}`:`/api/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(documentId)}`,{blob:true});}
  }
  if(typeof module!=='undefined')module.exports={summary,normalize,validateTask,ProjectAPI,duration,STATES};
  if(typeof document==='undefined')return;

  function boot(){
    const $=selector=>document.querySelector(selector);
    const client=location.pathname.replace(/\/$/,'')==='/project';
    // A fragment never reaches HTTP logs; retain it so reload keeps scoped access.
    let token='';if(client){try{token=decodeURIComponent(location.hash.slice(1));}catch{token='';}}
    $('#skip-link').addEventListener('click',event=>{if(client){event.preventDefault();$('#workspace').focus();}});
    const api=new ProjectAPI({client,token});
    let contacts=[],projects=[],project=null,tab='projects',layout='board',filter='',groupFilter='',returnFocus=null;
    const content=$('#content'),status=$('#status'),dialog=$('#editor'),dialogBody=$('#dialog-body');
    function el(tag,text,className){const node=document.createElement(tag);if(text!=null)node.textContent=String(text);if(className)node.className=className;return node;}
    function append(parent,...children){children.filter(Boolean).forEach(child=>parent.append(child));return parent;}
    const ICONS={
      close:'M18 6 6 18M6 6l12 12',download:'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3',
      play:'m8 5 12 7-12 7V5Z',hide:'m3 3 18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 4.2A11 11 0 0 1 21 12a16 16 0 0 1-3 4M6.6 6.6A16 16 0 0 0 3 12s3 8 9 8a10 10 0 0 0 5-1.4',
      plus:'M12 5v14M5 12h14',edit:'m16 3 5 5-12 12-6 1 1-6L16 3ZM14 5l5 5',
      clock:'M12 8v4l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',stop:'M6 6h12v12H6Z',
      attach:'m21 11-9 9a6 6 0 0 1-8.5-8.5l10-10a4 4 0 0 1 5.7 5.7L9.1 17.3a2 2 0 0 1-2.8-2.8L15.5 5',
      message:'M21 15a3 3 0 0 1-3 3H8l-5 4V6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3v9Z',
      share:'M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM8.5 10.5l7-4M8.5 13.5l7 4',
      lock:'M6 11h12v10H6V11ZM8 11V7a4 4 0 0 1 8 0v4',
      settings:'M4 7h16M4 17h16M8 4v6M16 14v6',back:'m12 5-7 7 7 7M5 12h14',
      list:'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',board:'M3 4h5v16H3V4ZM10 4h5v10h-5V4ZM17 4h4v13h-4V4Z',
      copy:'M9 9h12v12H9V9ZM15 5V3H3v12h2',save:'m5 12 4 4L19 6',refresh:'M20 7v5h-5M4 17v-5h5M5.5 7a8 8 0 0 1 13-2L20 7M4 17l1.5 2a8 8 0 0 0 13-2',
      busy:'M12 3a9 9 0 1 1-9 9'
    };
    const ACTION_ICONS={
      'Cerrar ventana':'close','Cancelar':'close','Descargar':'download','Ver video':'play','Ocultar video':'hide',
      'Configurar':'settings','Compartir':'share','Compartir con cliente':'share','Hacer interno':'lock',
      'Nueva tarea':'plus','Nuevo proyecto':'plus','Nuevo contacto':'plus','Añadir grupo':'plus',
      'Editar tarea':'edit','Renombrar':'edit','Registrar tiempo':'clock','Iniciar cronómetro':'clock','Detener cronómetro':'stop','Detener y registrar':'stop',
      'Adjuntar':'attach','Adjuntar documento':'attach','Subir documento':'attach','Escribir':'message','Publicar mensaje':'message',
      '← Todos los proyectos':'back','Volver a la tarea':'back','Vista de lista':'list','Vista de tablero':'board',
      'Copiar enlace':'copy','Crear enlace de acceso':'share','Crear enlace':'share','Revocar':'lock','Revocar este acceso':'lock','Revocar enlace':'lock','Ver accesos':'share',
      'Guardar cambios':'save','Iniciar':'clock','Volver a intentar':'refresh','Recargar datos (descarta este formulario)':'refresh','Guardando…':'busy'
    };
    function labelAction(node,label){
      const name=ACTION_ICONS[label];if(!name){node.textContent=label;return;}
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),path=document.createElementNS('http://www.w3.org/2000/svg','path');
      for(const [key,value] of Object.entries({viewBox:'0 0 24 24',width:20,height:20,fill:'none',stroke:'currentColor','stroke-width':1.8,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',focusable:'false'}))svg.setAttribute(key,value);
      path.setAttribute('d',ICONS[name]);svg.append(path);node.replaceChildren(svg);node.setAttribute('aria-label',label);node.title=label;
      if(!String(node.className||'').split(' ').includes('icon-button'))node.className=`${node.className||''} icon-button`.trim();
      node.setAttribute('aria-busy',name==='busy'?'true':'false');
    }
    function button(text,handler,className='',icon=true){const b=el('button',null,className);if(icon)labelAction(b,text);else b.textContent=text;b.type='button';b.addEventListener('click',()=>Promise.resolve().then(handler).catch(showError));return b;}
    function message(text,error=false){status.textContent=text;status.className=`status${error?' error':''}`;}
    function showError(error){const text=error.message||'No se pudo completar la acción.';message(text,true);if(dialog.open){let note=dialogBody.querySelector('.action-error');if(!note){note=el('p','','form-error action-error');note.setAttribute('role','alert');dialogBody.prepend(note);}note.textContent=text;note.tabIndex=-1;note.focus();}}
    function empty(text){return el('div',text,'empty');}
    function badge(text,state=''){return el('span',text,`badge ${state}`);}
    function date(value){if(!value)return 'Sin actualización';const d=new Date(value);return Number.isNaN(d.getTime())?'Fecha no disponible':d.toLocaleString('es',{dateStyle:'medium',timeStyle:'short'});}
    function heading(title,description,actions){const h=el('div',null,'heading'),left=el('div');append(left,el('h1',title),description?el('p',description):null);append(h,left,actions);return h;}
    function actions(...items){return append(el('div',null,'actions'),...items);}
    function section(title,extra){const s=el('section',null,'section');append(s,append(el('div',null,'section-heading'),el('h2',title),extra));return s;}
    const mediaViews=new Set();
    function releaseMedia(root){for(const view of [...mediaViews])if(!root||root.contains(view.parent))view.release();}
    globalThis.addEventListener?.('pagehide',()=>releaseMedia());
    function open(title){releaseMedia(dialogBody);if(!dialog.open)returnFocus=document.activeElement;$('#dialog-title').textContent=title;dialogBody.replaceChildren();if(!dialog.open)dialog.showModal();dialog.scrollTop=0;}
    function close(){dialog.close();}
    labelAction($('#dialog-close'),'Cerrar ventana');
    $('#dialog-close').addEventListener('click',close);
    dialog.addEventListener('close',()=>{releaseMedia(dialogBody);dialogBody.replaceChildren();if(returnFocus?.isConnected)returnFocus.focus();else $('#workspace').focus();});
    $('#owner-nav').hidden=client;$('#client-label').hidden=!client;
    if(client){$('#home-link').removeAttribute('href');$('#privacy-note').textContent='Vista privada del proyecto. El acceso puede revocarse.';}
    function form(title,fields,onSave,{submit='Guardar cambios',hint='',back=null}={}){
      open(title);const f=el('form'),grid=el('div',null,'form-grid');
      if(hint)f.append(el('p',hint,'hint'));
      for(const field of fields){
        if(field.heading){const h=el('h3',field.heading,'wide');h.style.gridColumn='1 / -1';grid.append(h);continue;}
        const wrap=el('div',null,`field${field.wide?' wide':''}${field.type==='checkbox'?' check':''}`);
        const label=el('label',field.label);const control=el(field.options?'select':field.type==='textarea'?'textarea':'input');
        control.id=`field-${field.name}`;control.name=field.name;label.htmlFor=control.id;
        if(field.options){for(const [value,text] of field.options) {const option=el('option',text);option.value=value;control.append(option);}}
        else if(field.type!=='textarea')control.type=field.type||'text';
        if(field.type==='checkbox')control.checked=Boolean(field.value);else if(field.type!=='file')control.value=field.value??'';
        if(field.required)control.required=true;if(field.min!=null)control.min=field.min;if(field.type==='number')control.step='1';
        if(field.accept)control.accept=field.accept;
        append(wrap,field.type==='checkbox'?control:label,field.type==='checkbox'?label:control);
        if(field.hint)wrap.append(el('small',field.hint,'hint'));grid.append(wrap);
      }
      const error=el('p','','form-error');error.setAttribute('role','alert');const save=el('button',null,'primary');labelAction(save,submit);save.type='submit';
      const cancel=button(back?'Volver a la tarea':'Cancelar',back||close);
      append(f,grid,error,append(el('div',null,'form-footer'),cancel,save));dialogBody.append(f);
      f.addEventListener('submit',async event=>{
        event.preventDefault();if(save.disabled)return;error.textContent='';save.disabled=true;labelAction(save,'Guardando…');
        try{const values={};for(const field of fields){if(field.heading)continue;const input=f.elements.namedItem(field.name);values[field.name]=field.type==='checkbox'?input.checked:field.type==='file'?input.files[0]:input.value;}await onSave(normalize(values));}
        catch(e){error.textContent=e.message;error.tabIndex=-1;error.focus();if(e.status===409){const reload=button('Recargar datos (descarta este formulario)',async()=>{close();await loadProject(project.id);});error.append(document.createElement('br'),reload);}}
        finally{save.disabled=false;labelAction(save,submit);}
      });
      queueMicrotask(()=>f.querySelector('input,select,textarea')?.focus());return f;
    }
    const options=record=>Object.entries(record);
    const field=(name,label,value='',type='text',extra={})=>({name,label,value,type,...extra});
    const choose=(name,label,value,choices,extra={})=>({name,label,value,options:choices,...extra});
    const check=(name,label,value=false)=>field(name,label,value,'checkbox',{wide:true});
    const text=(name,label,value='',extra={})=>field(name,label,value,'textarea',{wide:true,...extra});
    function contactChoices(){return [['','Sin cliente vinculado'],...contacts.map(c=>[c.id,`${c.name}${c.company?' · '+c.company:''}${c.status==='archived'?' (archivado)':''}`])];}
    async function command(action,data){const result=await api.command(action,data,project);if(result.project){project=result.project;renderProject();}message('Cambios guardados.');return result;}
    async function loadIndex(){message('Cargando proyectos y contactos…');const result=await api.index();contacts=result.contacts||[];projects=result.projects||[];project=null;renderIndex();message('');}
    async function loadProject(projectId){message('Cargando proyecto…');const result=await api.project(projectId);if(project?.id!==result.project?.id){filter='';groupFilter='';}project=result.project;renderProject();message('');}
    function renderIndex(){
      releaseMedia(content);content.replaceChildren();const tabs=el('div',null,'tabs');
      for(const [value,label] of [['projects','Proyectos'],['contacts','Contactos y clientes']]){const b=button(label,()=>{tab=value;renderIndex();});b.setAttribute('aria-pressed',tab===value);tabs.append(b);}
      content.append(heading(tab==='projects'?'Tu trabajo, en perspectiva.':'Relaciones con contexto.',tab==='projects'?'Propuestas, avances y decisiones. Un espacio para saber qué sigue.':'Del primer contacto al proyecto compartido.',actions(button(tab==='projects'?'Nuevo proyecto':'Nuevo contacto',()=>tab==='projects'?projectForm():contactForm(),'primary'))),tabs);
      if(tab==='contacts'){renderContacts();return;}
      if(!projects.length){content.append(empty('Aún no hay proyectos. Crea el primero, con o sin cliente vinculado.'));return;}
      const list=el('div',null,'project-list');
      for(const p of projects){const row=el('article',null,'project-row');append(row,badge(PROJECT_STATES[p.status]||p.status),button(p.name,()=>loadProject(p.id),'project-open',false));
        const clientContact=contacts.find(c=>c.id===p.client_id);append(row,el('p',clientContact?.name||'Sin cliente vinculado','hint'),append(el('div',null,'row-meta'),el('span',`${p.done_count||0} de ${p.task_count||0} tareas`),el('span',(p.time_entry_count||p.tracked_minutes)>0?duration(p.tracked_minutes||0):'Sin horas registradas'),el('span',`Actualizado ${date(p.updated_at)}`)));list.append(row);}
      content.append(list);
    }
    function renderContacts(){
      if(!contacts.length){content.append(empty('Agrega un contacto para comenzar. Los proyectos también pueden existir sin cliente.'));return;}
      const grid=el('div',null,'contact-grid');for(const [state,label] of options(CONTACT_STATES)){const col=el('section',null,'contact-group');col.append(el('h2',label));const items=contacts.filter(c=>c.status===state);if(!items.length)col.append(el('p','Sin contactos en esta etapa.','hint'));
        for(const c of items){const row=el('article',null,'contact');append(row,button(c.name,()=>contactForm(c),'link',false),el('p',c.company||'Sin empresa','hint'),el('p',[c.email,c.phone].filter(Boolean).join(' · '),'hint'),c.notes?el('p',c.notes,'hint'):null);col.append(row);}grid.append(col);}content.append(grid);
    }
    function contactForm(c={}){form(c.id?'Editar contacto':'Nuevo contacto',[
      field('name','Nombre',c.name,'text',{required:true}),choose('status','Etapa',c.status||'contact',options(CONTACT_STATES)),field('email','Correo electrónico',c.email,'email'),field('phone','Teléfono',c.phone,'tel'),field('company','Empresa',c.company),text('notes','Notas privadas',c.notes)
    ],async data=>{if(c.id){data.contact_id=c.id;data.revision=c.revision;}await api.command(c.id?'update_contact':'create_contact',data,null);close();await loadIndex();message('Contacto guardado.');});}
    function projectForm(p=null){const current=p||{};form(p?'Configuración del proyecto':'Nuevo proyecto',[
      field('name','Nombre del proyecto',current.name,'text',{required:true,wide:true}),text('description','Descripción',current.description),choose('client_id','Cliente vinculado',current.client_id||'',contactChoices()),choose('status','Estado del proyecto',current.status||'draft',options(PROJECT_STATES)),...(p?[text('internal_notes','Notas internas · nunca se comparten',p.internal_notes)]:[])
    ],async data=>{const result=await api.command(p?'update_project':'create_project',data,p);close();project=result.project;renderProject();message('Proyecto guardado.');},{hint:'Vincular un cliente no concede acceso. Compartir es una acción independiente.'});}
    function metrics(p){const s=summary(p),row=el('section',null,'overview');row.setAttribute('aria-label','Resumen del proyecto');
      const values=[[`${s.percent}%`,'Avance por tareas terminadas'],[s.entryCount?duration(s.minutes):'Sin horas registradas','Tiempo registrado'],[s.estimate===null?'Sin estimar':duration(s.estimate),'Estimación total'],[`${s.blocked} / ${s.questions}`,'Bloqueos / preguntas pendientes']];
      values.forEach(([value,label],i)=>{const m=el('div',null,'metric');append(m,el('strong',value),el('span',label));if(!i){const progress=el('progress');progress.max=100;progress.value=s.percent;progress.setAttribute('aria-label',`${s.done} de ${s.total} tareas terminadas`);m.append(progress,el('small',`${s.done} de ${s.total} tareas terminadas`,'hint'));}row.append(m);});return row;
    }
    function renderProject(){
      releaseMedia(content);content.replaceChildren();if(!project){content.append(empty('El proyecto no está disponible.'));return;}
      if(!client)content.append(button('← Todos los proyectos',loadIndex,'link'));
      const controls=client?null:actions(button('Configurar',()=>projectForm(project)),button('Compartir',sharePanel),button('Nueva tarea',()=>taskForm(),'primary'));
      content.append(heading(project.name,project.description,controls));
      const meta=actions(badge(PROJECT_STATES[project.status]||project.status),el('span',`Última actualización · ${date(project.updated_at)}`,'hint'));
      if(!client){const contact=contacts.find(c=>c.id===project.client_id);meta.append(el('span',contact?`Cliente: ${contact.name}`:'Sin cliente vinculado','hint'));}content.append(meta,metrics(project));
      const tasksSection=section('Trabajo del proyecto',!client?button('Añadir grupo',()=>groupForm()):null);
      const toolbar=el('div',null,'toolbar'),search=el('input');search.type='search';search.value=filter;search.placeholder='Buscar tarea, código o propuesta';search.setAttribute('aria-label','Buscar tareas');
      const groups=el('select');groups.setAttribute('aria-label','Filtrar por grupo');for(const [value,label] of [['','Todos los grupos'],...(project.groups||[]).map(g=>[g.id,g.name])]){const op=el('option',label);op.value=value;groups.append(op);}groups.value=groupFilter;
      const toggle=button(layout==='board'?'Vista de lista':'Vista de tablero',()=>{layout=layout==='board'?'list':'board';renderProject();});
      const taskArea=el('div');search.addEventListener('input',()=>{filter=search.value;renderTasks(taskArea);});groups.addEventListener('change',()=>{groupFilter=groups.value;renderTasks(taskArea);});append(toolbar,search,groups,toggle);append(tasksSection,toolbar,taskArea);content.append(tasksSection);renderTasks(taskArea);
      const columns=el('div',null,'columns');const groupSection=section('Grupos de trabajo');
      if(!(project.groups||[]).length)groupSection.append(el('p','Todavía no hay grupos.','hint'));
      for(const g of project.groups||[]){const tasks=(project.tasks||[]).filter(t=>t.group_id===g.id),s=summary({tasks}),row=el('div',null,'group-row'),body=el('div');append(body,el('h3',g.name),el('small',`${s.done}/${s.total} terminadas · ${s.entryCount?duration(s.minutes):'Sin horas registradas'}`,'hint'));const pg=el('progress');pg.max=100;pg.value=s.percent;pg.setAttribute('aria-label',`${g.name}: ${s.percent}%`);body.append(pg);append(row,body,!client?button('Renombrar',()=>groupForm(g),'link'):null);groupSection.append(row);}
      const docs=section('Documentos privados',!client?button('Adjuntar',()=>documentForm()):null);renderDocuments(docs,project.documents||[]);append(columns,groupSection,docs);content.append(columns);
      const delivered=section('Entregables recientes');const done=(project.tasks||[]).filter(t=>t.status==='done');if(!done.length)delivered.append(el('p','Las tareas terminadas aparecerán aquí con su resolución y evidencia.','hint'));for(const t of done.slice(-5).reverse())delivered.append(button(`${t.code?t.code+' · ':''}${t.title}`,()=>taskDetails(t.id),'link',false));content.append(delivered);
      if(!client&&project.internal_notes){const notes=section('Notas internas');notes.append(el('p',project.internal_notes,'private'));content.append(notes);}
    }
    function renderTasks(area){
      area.replaceChildren();const query=filter.toLocaleLowerCase();const tasks=(project.tasks||[]).filter(t=>(!groupFilter||t.group_id===groupFilter)&&[t.title,t.code,t.proposal].some(v=>String(v||'').toLocaleLowerCase().includes(query)));
      if(!tasks.length){area.append(empty((project.tasks||[]).length?'No hay tareas con estos filtros.':'Aún no hay tareas. Las propuestas y su ejecución se verán aquí.'));return;}
      function taskNode(t){const b=button('',()=>taskDetails(t.id),'task');append(b,el('span',t.code||'Tarea','code'),el('strong',t.title),badge(STATES[t.status]||t.status,t.status),el('small',`${PRIORITIES[t.priority]||'Normal'} · ${APPROVALS[t.approval]||'Pendiente'}`));if(layout==='board'){const group=(project.groups||[]).find(g=>g.id===t.group_id);if(group)b.append(el('small',group.name));if(t.status==='blocked')b.append(el('small',t.next_step||'Revisar bloqueo'));}return b;}
      if(layout==='list'){const list=el('div',null,'task-list');tasks.forEach(t=>list.append(taskNode(t)));area.append(list);return;}
      const board=el('div',null,'board');for(const [state,label] of options(STATES)){const lane=el('section',null,'lane');lane.dataset.state=state;const items=tasks.filter(t=>t.status===state);append(lane,append(el('h3',label),el('span',items.length)));items.forEach(t=>lane.append(taskNode(t)));if(!items.length)lane.append(el('p','Sin tareas','hint'));board.append(lane);}area.append(board);
    }
    function groupForm(group={}){form(group.id?'Renombrar grupo':'Nuevo grupo',[field('name','Nombre del grupo',group.name,'text',{required:true,wide:true})],async data=>{if(group.id)data.group_id=group.id;await command(group.id?'update_group':'add_group',data);close();});}
    function taskForm(task={}){const fields=[
      field('title','Título',task.title,'text',{required:true,wide:true}),field('code','Código de referencia',task.code),choose('group_id','Grupo',task.group_id||'',[['','Sin grupo'],...(project.groups||[]).map(g=>[g.id,g.name])]),
      choose('status','Estado',task.status||'todo',options(STATES)),choose('priority','Prioridad',task.priority||'normal',options(PRIORITIES)),choose('approval','Aprobación',task.approval||'pending',options(APPROVALS)),field('estimated_minutes','Estimación (minutos)',task.estimated_minutes??'','number',{min:0,hint:'Déjalo vacío si se desconoce. No crea horas históricas.'}),
      {heading:'Propuesta original'},text('why','Por qué se necesita',task.why),text('benefit','Beneficio esperado',task.benefit),text('proposal','Solución propuesta',task.proposal),
      {heading:'Ejecución y resultado real'},text('resolution','Resolución real · obligatoria al terminar',task.resolution),text('evidence','Evidencia · obligatoria al terminar',task.evidence),
      {heading:'Dependencias y bloqueos'},text('blocker','Qué bloquea el avance',task.blocker),field('blocker_owner','Responsable del desbloqueo',task.blocker_owner,'text',{wide:true}),text('next_step','Siguiente paso · obligatorio si está bloqueado',task.next_step),
      {heading:'Privacidad'},check('visible','Visible para el cliente',task.visible),text('internal_notes','Notas internas · nunca se comparten',task.internal_notes)
    ];form(task.id?'Editar tarea':'Nueva tarea',fields,async data=>{validateTask(data);if(task.id)data.task_id=task.id;await command(task.id?'update_task':'add_task',data);close();if(task.id)taskDetails(task.id);},{hint:'La propuesta describe lo esperado. La resolución y la evidencia documentan lo que realmente se hizo.',back:task.id?()=>taskDetails(task.id):null});}
    function detail(parent,title,value,full=false,privateNote=false){const block=el('div',null,`detail-block${full?' full':''}${privateNote?' private':''}`);append(block,el('h3',title),el('p',value||'Sin registrar',''));parent.append(block);}
    function taskDetails(taskId){
      const t=(project.tasks||[]).find(task=>task.id===taskId);if(!t)return;
      open(`${t.code?t.code+' · ':''}${t.title}`);dialogBody.append(actions(badge(STATES[t.status]||t.status,t.status),badge(`Prioridad ${PRIORITIES[t.priority]||'normal'}`),badge(APPROVALS[t.approval]||'Pendiente')));
      if(!client)dialogBody.append(actions(button('Editar tarea',()=>taskForm(t)),button('Registrar tiempo',()=>timeForm(t)),button(t.timer?'Detener cronómetro':'Iniciar cronómetro',()=>timerForm(t)),button('Adjuntar documento',()=>documentForm(t.id))));
      if(t.timer)dialogBody.append(el('p',`Cronómetro activo desde ${date(t.timer.started_at)}. El servidor registra el tiempo real al detenerlo.`,'timer'));
      const details=el('div',null,'details');detail(details,'Por qué',t.why);detail(details,'Beneficio esperado',t.benefit);detail(details,'Solución propuesta',t.proposal,true);detail(details,'Resolución real',t.resolution,true);detail(details,'Evidencia',t.evidence,true);
      if(t.blocker||t.status==='blocked'){detail(details,'Bloqueo',t.blocker);detail(details,'Responsable',t.blocker_owner);detail(details,'Siguiente paso',t.next_step,true);}
      detail(details,'Estimación',duration(t.estimated_minutes));detail(details,'Tiempo registrado',(t.time_entries||[]).length?duration(t.time_entries.reduce((n,e)=>n+e.minutes,0)):'Sin horas registradas');
      if(!client){detail(details,'Visibilidad',t.visible?'Compartida con el cliente':'Solo interna');if(t.internal_notes)detail(details,'Notas internas',t.internal_notes,true,true);}dialogBody.append(details);
      const time=section('Registro de tiempo');for(const e of t.time_entries||[]){const row=el('div',null,'time-row');append(row,el('p',`${e.date} · ${duration(e.minutes)}${e.note?' · '+e.note:''}${!client?(e.visible?' · Compartido':' · Interno'):''}`),!client?visibilityToggle('time',e,t.id):null);time.append(row);}if(!(t.time_entries||[]).length)time.append(el('p','Aún no hay horas registradas.','hint'));dialogBody.append(time);
      const docs=section('Documentos de la tarea');renderDocuments(docs,(project.documents||[]).filter(d=>d.task_id===t.id),t.id);dialogBody.append(docs);
      const comments=section('Conversación y preguntas',button('Escribir',()=>commentForm(t)));for(const c of t.comments||[]){const item=el('article',null,'comment');append(item,badge(KINDS[c.kind]||'Comentario'),el('p',c.text),el('small',`${date(c.created_at)}${!client?(c.visible?' · Compartido':' · Interno'):''}`));if(!client)item.append(visibilityToggle('comment',c,t.id));comments.append(item);}if(!(t.comments||[]).length)comments.append(el('p','Usa una pregunta para registrar decisiones pendientes.','hint'));dialogBody.append(comments);
    }
    function commentForm(t){form('Escribir en la tarea',[
      choose('kind','Tipo','comment',options(KINDS)),text('text','Mensaje','',{required:true}),...(!client?[check('visible','Visible para el cliente',false)]:[])
    ],async data=>{if(client){await api.comment(t.id,data.text,data.kind);const result=await api.project();project=result.project;renderProject();}else await command('add_comment',{task_id:t.id,...data});taskDetails(t.id);message('Mensaje guardado.');},{submit:'Publicar mensaje',back:()=>taskDetails(t.id),hint:client?'El mensaje será visible en este proyecto compartido.':'La visibilidad del mensaje es independiente de la tarea.'});}
    function timeForm(t){const today=new Date();const localDate=`${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
      form('Registrar tiempo real',[field('date','Fecha de trabajo',localDate,'date',{required:true}),field('minutes','Minutos trabajados','','number',{required:true,min:1}),text('note','Descripción del trabajo'),check('visible','Compartir este registro con el cliente')],async data=>{await command('add_time',{task_id:t.id,...data});taskDetails(t.id);},{hint:'Registra únicamente trabajo realizado. Una estimación no es un registro de tiempo.',back:()=>taskDetails(t.id)});}
    function timerForm(t){form(t.timer?'Detener cronómetro':'Iniciar cronómetro',t.timer?[]:[text('note','Trabajo que vas a realizar')],async data=>{await command(t.timer?'stop_timer':'start_timer',{task_id:t.id,...data});taskDetails(t.id);},{submit:t.timer?'Detener y registrar':'Iniciar cronómetro',hint:t.timer?'Se registrará el tiempo real transcurrido desde el inicio.':'Solo puede haber un cronómetro activo por actor. El tiempo se calcula en el servidor.',back:()=>taskDetails(t.id)});}
    function visibilityToggle(kind,item,taskId){return button(item.visible?'Hacer interno':'Compartir con cliente',async()=>{await command('set_visibility',{kind,id:item.id,visible:!item.visible});if(taskId)taskDetails(taskId);},'link');}
    function renderDocuments(parent,documents,taskId=null){
      if(!documents.length){parent.append(el('p','No hay documentos disponibles en esta vista.','hint'));return;}
      for(const d of documents){
        const row=el('div',null,'document-row'),body=el('div',null,'document-content');
        append(body,el('h3',d.name),el('small',`${Math.ceil((d.size||0)/1024)} KB${!client?(d.visible?' · Compartido':' · Interno'):''}`,'hint'));
        const controls=actions(button('Descargar',async()=>{const blob=await api.download(project.id,d.id);const url=URL.createObjectURL(blob);const a=el('a');a.href=url;a.download=d.name||'documento';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}),!client?visibilityToggle('document',d,taskId):null);
        // Only passive video formats get a player; other attachments remain download-only.
        if(['video/mp4','video/webm','video/ogg'].includes(d.media_type)){
          const player=el('div',null,'document-preview'),notice=el('p','','hint');notice.setAttribute('role','status');
          let view=null;
          const toggle=button('Ver video',async()=>{
            if(view){view.release();return;}
            toggle.disabled=true;notice.textContent='Cargando video…';
            const current={parent:body,cancelled:false,url:null,video:null,release(){
              this.cancelled=true;if(this.video){this.video.pause();this.video.removeAttribute('src');this.video.load();}
              if(this.url)URL.revokeObjectURL(this.url);mediaViews.delete(this);if(view===this)view=null;
              player.replaceChildren();notice.textContent='';labelAction(toggle,'Ver video');toggle.disabled=false;toggle.setAttribute('aria-expanded','false');
            }};view=current;mediaViews.add(current);
            try{
              // Reuse the authorized download: bearer stays in the header, never in a media URL.
              const blob=await api.download(project.id,d.id);if(current.cancelled)return;
              current.url=URL.createObjectURL(new Blob([blob],{type:d.media_type}));
              const video=el('video',null,'document-video');current.video=video;video.controls=true;video.preload='metadata';video.playsInline=true;video.src=current.url;video.setAttribute('aria-label',`Video: ${d.name}`);
              video.addEventListener('error',()=>{notice.textContent='No se pudo reproducir este video. Usa Descargar para abrirlo en tu equipo.';});
              player.append(video);notice.textContent='';labelAction(toggle,'Ocultar video');toggle.setAttribute('aria-expanded','true');
            }catch(error){if(!current.cancelled){current.release();notice.textContent=error.message||'No se pudo cargar el video. Puedes volver a intentarlo.';}}
            finally{toggle.disabled=false;}
          });toggle.setAttribute('aria-expanded','false');controls.prepend(toggle);append(body,player,notice);
        }
        append(row,body,controls);parent.append(row);
      }
    }
    function documentForm(taskId=''){
      form('Adjuntar documento privado',[field('file','Archivo',null,'file',{required:true,wide:true}),choose('task_id','Vincular a tarea',taskId,[['','Documento del proyecto'],...(project.tasks||[]).map(t=>[t.id,`${t.code?t.code+' · ':''}${t.title}`])]),check('visible','Permitir descarga al cliente')],async data=>{
        const file=data.file;if(!file)throw Error('Selecciona un archivo.');if(file.size>10*1024*1024)throw Error('Selecciona un archivo de hasta 10 MB.');
        const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
        await command('add_document',{task_id:data.task_id,name:file.name,media_type:file.type||'application/octet-stream',content_base64:btoa(binary),visible:data.visible});close();if(taskId)taskDetails(taskId);
      },{submit:'Subir documento',hint:'Los archivos se almacenan de forma privada. Para compartir un archivo vinculado a una tarea, la tarea también debe ser visible.',back:taskId?()=>taskDetails(taskId):null});
    }
    function sharePanel(){
      open('Acceso del cliente');dialogBody.append(el('p','Cada enlace concede acceso solo a los elementos marcados como visibles. No comparte contactos ni notas internas. Puedes revocarlo en cualquier momento.','hint'),button('Crear enlace de acceso',()=>shareForm(),'primary'));
      for(const share of project.shares||[]){const row=el('div',null,'share-row');append(row,append(el('div'),el('h3',share.label||'Acceso al proyecto'),el('small',share.revoked_at?`Revocado ${date(share.revoked_at)}`:`Creado ${date(share.created_at)}`,'hint')),!share.revoked_at?button('Revocar',()=>revokeForm(share),'danger'):badge('Revocado'));dialogBody.append(row);}
      if(!(project.shares||[]).length)dialogBody.append(el('p','Todavía no has compartido este proyecto.','hint'));
    }
    function shareForm(){form('Crear acceso compartido',[field('label','Nombre para identificar el acceso','','text',{required:true,wide:true})],async data=>{const result=await command('create_share',data);if(!result.share?.token){open('El enlace ya fue creado');dialogBody.append(el('p','El guardado se recuperó tras un reintento, pero el secreto solo se entrega una vez. No es posible recuperar este enlace: revócalo y crea uno nuevo.','hint'),button('Revocar este acceso',()=>revokeForm({id:result.share?.id,label:data.label}),'danger'),button('Ver accesos',sharePanel));return;}open('Enlace creado · cópialo ahora');const url=`${location.origin}/project#${result.share.token}`;const input=el('input');input.value=url;input.readOnly=true;input.className='share-link';input.setAttribute('aria-label','Enlace de acceso al cliente');const feedback=el('p','','hint');dialogBody.append(el('p','El enlace solo se muestra una vez. Trátalo como una contraseña. Si lo pierdes, revócalo y crea uno nuevo.','hint'),input,button('Copiar enlace',async()=>{try{await navigator.clipboard.writeText(url);feedback.textContent='Enlace copiado.';}catch{input.focus();input.select();feedback.textContent='Selecciona y copia el enlace manualmente.';}}),feedback);input.focus();input.select();},{submit:'Crear enlace'});}
    function revokeForm(share){form('Revocar acceso',[],async()=>{await command('revoke_share',{share_id:share.id});sharePanel();},{submit:'Revocar enlace',hint:`El acceso «${share.label||'Acceso al proyecto'}» dejará de funcionar de inmediato. Esta acción no elimina el proyecto.`});}
    async function start(){
      try{
        if(client){if(!token)throw Error('Falta el enlace privado. Abre el enlace completo que te compartieron.');await loadProject();}
        else{const me=await api.request('/api/me');api.csrf=me.csrf;await loadIndex();}
      }catch(e){showError(e);content.replaceChildren(empty(client?'No se puede abrir este proyecto compartido.':'No se pudo cargar el espacio de proyectos.'));if(!client&&e.status===401){const a=el('a','Iniciar sesión','button primary');a.href='/';content.append(a);}else content.append(button('Volver a intentar',start));}
    }
    start();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
