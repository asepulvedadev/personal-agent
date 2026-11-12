'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ui = require('../app/static/projects.js');
test('progress derives exclusively from supplied tasks; estimates stay unknown', () => {
 const p={tasks:[{id:'1',status:'done',estimated_minutes:60,time_entries:[{minutes:20}],comments:[]},{id:'2',status:'blocked',estimated_minutes:null,comments:[{kind:'question'}]}]};
 assert.deepEqual(ui.summary(p),{total:2,done:1,percent:50,entryCount:1,minutes:20,estimate:null,blocked:1,questions:1});
 assert.equal(ui.summary({tasks:[]}).percent,0);
});
test('questions are unresolved until a later answer on the same task',()=>{
 assert.equal(ui.summary({tasks:[{comments:[{kind:'question'},{kind:'answer'},{kind:'question'}]}]}).questions,1);
});
test('task payload normalizes numeric and nullable fields without inventing hours',()=>{
 assert.deepEqual(ui.normalize({group_id:'',estimated_minutes:'',visible:false}),{group_id:null,estimated_minutes:null,visible:false});
 assert.equal(ui.normalize({minutes:'15'}).minutes,15);
 assert.throws(()=>ui.validateTask({status:'done',resolution:'',evidence:'x'}),/resolución/);
 assert.throws(()=>ui.validateTask({status:'blocked',blocker:'x',next_step:''}),/paso/);
});
test('client requests use only bearer and never owner CSRF or cookies',async()=>{
 const calls=[]; const api=new ui.ProjectAPI({client:true,token:'secret',csrf:'owner',fetcher:async(...args)=>{calls.push(args);return {ok:true,json:async()=>({project:{}})};}});
 await api.project(); await api.comment('t','Pregunta','question','0'.repeat(32));
 assert.equal(calls[0][0],'/api/project-view');
 assert.equal(calls[0][1].headers.Authorization,'Bearer secret');
 assert.equal(calls[0][1].credentials,'omit');
 assert.equal(calls[1][1].headers['X-CSRF-Token'],undefined);
 assert.equal(JSON.parse(calls[1][1].body).operation_id,'0'.repeat(32));
 await assert.rejects(api.command('update_task',{},{}),/cliente/);
});
test('owner write carries CSRF/revision and network retry reuses exact operation',async()=>{
 const calls=[];const api=new ui.ProjectAPI({csrf:'csrf',fetcher:async(...args)=>{calls.push(args);if(calls.length===1)throw Error('offline');return {ok:true,json:async()=>({project:{id:'p'}})};}});
 await api.command('update_project',{name:'Name'},{id:'p',revision:7},'a'.repeat(32));
 assert.equal(calls.length,2);assert.equal(calls[0][1].body,calls[1][1].body);
 const body=JSON.parse(calls[0][1].body); assert.equal(body.revision,7);assert.equal(body.project_id,'p');assert.equal(calls[0][1].headers['X-CSRF-Token'],'csrf');
});
test('errors stay safe and stale writes produce actionable conflict',async()=>{
 const api=new ui.ProjectAPI({fetcher:async()=>({ok:false,status:409,json:async()=>({error:'SQL secret'})})});
 await assert.rejects(api.project('p'),e=>e.status===409&&e.message.includes('recarga')&&!e.message.includes('SQL'));
});
test('shell and code retain security and accessibility boundaries',()=>{
 const js=fs.readFileSync('app/static/projects.js','utf8');const html=fs.readFileSync('app/static/projects.html','utf8');
 assert.ok(!js.includes('innerHTML'));assert.ok(!js.includes('localStorage'));assert.ok(!js.includes('sessionStorage'));
 assert.ok(!js.includes('history.replaceState'));assert.match(js,/location.hash.slice/);assert.match(html,/name="referrer" content="no-referrer"/);assert.match(html,/aria-live="polite"/);assert.match(html,/<dialog/);
 assert.match(js,/createObjectURL/);assert.match(js,/revokeObjectURL/);
});

const vm=require('node:vm');
function mount({client=false,project={},contacts=[],projects=[],failure=null,responder=null}={}){
 class Element{
  constructor(tag,doc){this.tagName=tag;this.doc=doc;this.children=[];this.listeners={};this.attrs={};this.dataset={};this.style={};this.value='';this._text='';this.open=false;this.isConnected=true;}
  set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text+this.children.map(x=>x.textContent||'').join('');}
  append(...nodes){this.children.push(...nodes);}prepend(node){this.children.unshift(node);}
  contains(node){return node===this||this.all().includes(node);}
  click(){this.clicked=true;}remove(){this.isConnected=false;}pause(){this.paused=true;}load(){}
  replaceChildren(...nodes){this.children=nodes;this._text='';}
  setAttribute(k,v){this.attrs[k]=v;}removeAttribute(k){delete this.attrs[k];}
  addEventListener(k,v){(this.listeners[k]??=[]).push(v);}
  async dispatch(k){for(const fn of this.listeners[k]||[])await fn({preventDefault(){}});await new Promise(r=>setImmediate(r));}
  focus(){this.doc.activeElement=this;}select(){}showModal(){this.open=true;}close(){this.open=false;this.dispatch('close');}
  all(){return this.children.flatMap(n=>[n,...(n.all?n.all():[])]);}
  querySelector(selector){return this.all().find(n=>selector==='input,select,textarea'?['input','select','textarea'].includes(n.tagName):selector.startsWith('.')?String(n.className).split(' ').includes(selector.slice(1)):n.tagName===selector);}
  get elements(){return {namedItem:name=>this.all().find(e=>e.name===name)};}
 }
 const nodes=new Map();const doc={readyState:'complete',createElement:tag=>new Element(tag,doc),createElementNS:(_,tag)=>new Element(tag,doc),querySelector:selector=>{if(!nodes.has(selector))nodes.set(selector,new Element('div',doc));return nodes.get(selector);}};
 const urls=[],revoked=[];doc.body=new Element('body',doc);
 const calls=[],history=[];doc.activeElement=doc.querySelector('#content');
 const context={document:doc,location:{pathname:client?'/project':'/projects',hash:client?'#bearer-secret':'',origin:'https://private.example'},history:{replaceState:(...a)=>history.push(a)},crypto:require('node:crypto').webcrypto,queueMicrotask,setTimeout,URL:{createObjectURL:blob=>{const url=URL.createObjectURL(blob);urls.push({url,blob});return url;},revokeObjectURL:url=>{revoked.push(url);URL.revokeObjectURL(url);}},Blob,Uint8Array,console,
 fetch:async(path,options)=>{calls.push({path,options});if(responder){const response=responder(path,options);if(response)return response;}if(failure)return {ok:false,status:failure};return {ok:true,json:async()=>path==='/api/me'?{csrf:'owner-csrf'}:path==='/api/projects'?{contacts,projects}:path==='/api/projects/command'?{contact:{id:'new'}}:{project}};}};
 vm.runInNewContext(fs.readFileSync('app/static/projects.js','utf8'),context);
 return {nodes,calls,history,doc,urls,revoked,settle:()=>new Promise(r=>setImmediate(r)),find:(root,label)=>root.all().find(e=>e.tagName==='button'&&(e.attrs['aria-label']||e.textContent)===label)};
}
test('client bootstrap retains fragment for reload, makes no owner requests and renders read-only controls',async()=>{
 const f=mount({client:true,project:{name:'<script>unsafe</script>',status:'active',tasks:[{id:'t',title:'Visible task',status:'todo',priority:'normal',approval:'pending',comments:[],time_entries:[]}],groups:[],documents:[]}});await f.settle();
 assert.deepEqual(f.calls.map(c=>c.path),['/api/project-view']);assert.equal(f.history.length,0);
 assert.equal(f.calls[0].options.headers.Authorization,'Bearer bearer-secret');
 const content=f.nodes.get('#content');assert.ok(content.textContent.includes('<script>unsafe</script>'));assert.equal(content.all().filter(e=>e.tagName==='script').length,0);
 assert.equal(f.find(content,'Nueva tarea'),undefined);assert.equal(f.find(content,'Compartir'),undefined);assert.equal(f.find(content,'Configurar'),undefined);
 const task=content.all().find(e=>e.tagName==='button'&&e.textContent.includes('Visible task'));await task.dispatch('click');
 assert.equal(f.nodes.get('#editor').open,true);assert.equal(f.find(f.nodes.get('#dialog-body'),'Editar tarea'),undefined);assert.equal(f.find(f.nodes.get('#dialog-body'),'Registrar tiempo'),undefined);assert.ok(f.find(f.nodes.get('#dialog-body'),'Escribir'));
});
test('owner creates contacts through labeled form and authenticated command',async()=>{
 const f=mount();await f.settle();assert.deepEqual(f.calls.map(c=>c.path),['/api/me','/api/projects']);
 await f.find(f.nodes.get('#content'),'Contactos y clientes').dispatch('click');await f.find(f.nodes.get('#content'),'Nuevo contacto').dispatch('click');
 const form=f.nodes.get('#dialog-body').querySelector('form');form.elements.namedItem('name').value='Real contact';form.elements.namedItem('status').value='contact';
 await form.dispatch('submit');const write=f.calls.find(c=>c.path==='/api/projects/command');assert.ok(write);const body=JSON.parse(write.options.body);assert.equal(body.action,'create_contact');assert.equal(body.data.name,'Real contact');assert.match(body.operation_id,/^[a-f0-9]{32}$/);assert.equal(write.options.headers['X-CSRF-Token'],'owner-csrf');
});
test('owner unauthorized and revoked client bootstraps are explicit, not blank',async()=>{
 const owner=mount({failure:401});await owner.settle();assert.ok(owner.nodes.get('#status').textContent.includes('Inicia sesión'));assert.ok(owner.nodes.get('#content').all().some(e=>e.tagName==='a'&&e.href==='/'));
 const client=mount({client:true,failure:401});await client.settle();assert.ok(client.nodes.get('#status').textContent.includes('revocado'));assert.equal(client.calls.some(c=>c.path==='/api/me'),false);
});

test('task form requires real completion evidence and keeps draft on revision conflict',async()=>{
 const project={id:'p',revision:4,name:'Work',status:'active',groups:[],tasks:[],documents:[]};
 const f=mount({project,projects:[{...project,task_count:0}],responder:path=>path==='/api/projects/command'?{ok:false,status:409}:null});await f.settle();
 await f.find(f.nodes.get('#content'),'Work').dispatch('click');await f.find(f.nodes.get('#content'),'Nueva tarea').dispatch('click');
 const form=f.nodes.get('#dialog-body').querySelector('form');form.elements.namedItem('title').value='Actual work';form.elements.namedItem('status').value='done';
 await form.dispatch('submit');assert.equal(f.calls.some(c=>c.path==='/api/projects/command'),false);assert.match(form.textContent,/resolución real/);
 form.elements.namedItem('resolution').value='Implemented';form.elements.namedItem('evidence').value='Verified';
 await form.dispatch('submit');const body=JSON.parse(f.calls.find(c=>c.path==='/api/projects/command').options.body);
 assert.equal(body.action,'add_task');assert.equal(body.revision,4);assert.equal(body.data.group_id,null);assert.equal(body.data.estimated_minutes,null);assert.equal(body.data.visible,false);
 assert.equal(form.elements.namedItem('title').value,'Actual work');assert.match(form.textContent,/Otra sesión/);assert.ok(f.find(form,'Recargar datos (descarta este formulario)'));
});
test('share retry without once-only secret presents revoke action, never an undefined token link',async()=>{
 const project={id:'p',revision:1,name:'Work',status:'active',groups:[],tasks:[],documents:[],shares:[]};
 const f=mount({project,projects:[project],responder:path=>path==='/api/projects/command'?{ok:true,json:async()=>({project:{...project,revision:2,shares:[{id:'s',label:'Client'}]},share:{id:'s'}})}:null});await f.settle();
 await f.find(f.nodes.get('#content'),'Work').dispatch('click');await f.find(f.nodes.get('#content'),'Compartir').dispatch('click');await f.find(f.nodes.get('#dialog-body'),'Crear enlace de acceso').dispatch('click');
 const form=f.nodes.get('#dialog-body').querySelector('form');form.elements.namedItem('label').value='Client';await form.dispatch('submit');
 const body=f.nodes.get('#dialog-body');assert.match(body.textContent,/revócalo y crea uno nuevo/);assert.ok(f.find(body,'Revocar este acceso'));assert.equal(body.all().some(e=>String(e.value).includes('#undefined')),false);
});

test('owner toggles existing document, comment and timer-created time visibility with current revision',async()=>{
 let project={id:'p',revision:1,name:'Work',status:'active',groups:[],documents:[{id:'d',name:'Private.pdf',visible:false}],tasks:[{id:'t',title:'Task',status:'doing',priority:'normal',approval:'pending',comments:[{id:'c',text:'Private note',kind:'comment',visible:false}],time_entries:[{id:'time',minutes:5,date:'2026-11-11',note:'Timer entry',visible:false}]}]};
 const f=mount({project,projects:[project],responder:(path,opts)=>{
   if(path!=='/api/projects/command')return null;
   const body=JSON.parse(opts.body);assert.equal(body.action,'set_visibility');assert.equal(body.revision,project.revision);
   const {kind,id,visible}=body.data;project=structuredClone(project);
   const target=kind==='document'?project.documents.find(e=>e.id===id):project.tasks[0][kind==='comment'?'comments':'time_entries'].find(e=>e.id===id);target.visible=visible;project.revision++;
   return {ok:true,json:async()=>({project})};
 }});await f.settle();await f.find(f.nodes.get('#content'),'Work').dispatch('click');
 const docRow=f.nodes.get('#content').all().find(e=>e.className==='document-row');await f.find(docRow,'Compartir con cliente').dispatch('click');
 const task=f.nodes.get('#content').all().find(e=>e.tagName==='button'&&e.className==='task');await task.dispatch('click');
 const comment=f.nodes.get('#dialog-body').all().find(e=>e.className==='comment');await f.find(comment,'Compartir con cliente').dispatch('click');
 const time=f.nodes.get('#dialog-body').all().find(e=>e.className==='time-row');await f.find(time,'Compartir con cliente').dispatch('click');
 const writes=f.calls.filter(c=>c.path==='/api/projects/command').map(c=>JSON.parse(c.options.body));
 assert.deepEqual(writes.map(c=>c.data),[{kind:'document',id:'d',visible:true},{kind:'comment',id:'c',visible:true},{kind:'time',id:'time',visible:true}]);assert.equal(project.revision,4);
 assert.ok(f.find(f.nodes.get('#dialog-body'),'Hacer interno'));
});
test('client cannot see document/comment/time visibility controls',async()=>{
 const project={name:'Work',status:'active',groups:[],documents:[{id:'d',name:'Shared.pdf',visible:true}],tasks:[{id:'t',title:'Task',status:'todo',comments:[{id:'c',kind:'comment',text:'Shared',visible:true}],time_entries:[{id:'time',minutes:5,date:'2026-11-11',visible:true}]}]};
 const f=mount({client:true,project});await f.settle();assert.equal(f.find(f.nodes.get('#content'),'Hacer interno'),undefined);assert.equal(f.find(f.nodes.get('#content'),'Compartir con cliente'),undefined);
 const task=f.nodes.get('#content').all().find(e=>e.tagName==='button'&&e.className==='task');await task.dispatch('click');assert.equal(f.find(f.nodes.get('#dialog-body'),'Hacer interno'),undefined);assert.equal(f.find(f.nodes.get('#dialog-body'),'Compartir con cliente'),undefined);
 assert.deepEqual(f.calls.map(c=>c.path),['/api/project-view']);
});

test('browser fetch receives global receiver on initial call and idempotent retry',async()=>{
 let attempts=0;const api=new ui.ProjectAPI({fetcher:async function(){assert.equal(this,globalThis);attempts++;if(attempts===1)throw Error('temporary network failure');return {ok:true,json:async()=>({project:{id:'p'}})};}});
 const result=await api.command('update_project',{name:'Work'},{id:'p',revision:1},'b'.repeat(32));assert.equal(result.project.id,'p');assert.equal(attempts,2);
});
test('task states preserve the exact four requested Spanish labels',()=>{
 assert.deepEqual(ui.STATES,{todo:'Por ejecutar',doing:'En ejecución',blocked:'Bloqueada',done:'Finalizada'});
});

test('empty time ledgers are not presented as historical zero-hour work',async()=>{
 const f=mount({projects:[{id:'p',name:'Work',status:'active',tracked_minutes:0,time_entry_count:0,task_count:1,done_count:0}]});await f.settle();assert.ok(f.nodes.get('#content').textContent.includes('Sin horas registradas'));
});

test('client skip navigation preserves the bearer fragment and focuses content',async()=>{
 const f=mount({client:true,project:{name:'Work',tasks:[],groups:[],documents:[]}});await f.settle();
 await f.nodes.get('#skip-link').dispatch('click');
 assert.equal(f.doc.activeElement,f.nodes.get('#workspace'));assert.equal(f.history.length,0);
});

test('mobile task filters remain readable rather than squeezing into thirds',()=>{
 const css=fs.readFileSync('app/static/projects.css','utf8');assert.match(css,/@media\(max-width:600px\)[\s\S]*\.toolbar\s*\{\s*flex-direction:column;/);
});

function videoProject(){return {id:'p',name:'Video work',status:'active',groups:[],documents:[{id:'v',task_id:'t',name:'G1.mp4',media_type:'video/mp4',size:20,visible:true},{id:'h',name:'Unsafe.html',media_type:'text/html'}],tasks:[{id:'t',title:'Verified task',status:'done',resolution:'Verified',evidence:'Recording',comments:[],time_entries:[]}]};}
test('client task video loads lazily through scoped bearer; closing releases media and download stays available',async()=>{
 const f=mount({client:true,project:videoProject(),responder:path=>path.includes('/documents/')?{ok:true,blob:async()=>new Blob(['video bytes'],{type:'application/octet-stream'})}:null});await f.settle();
 assert.equal(f.calls.some(c=>c.path.includes('/documents/')),false);
 const task=f.nodes.get('#content').all().find(e=>e.className==='task');await task.dispatch('click');
 const body=f.nodes.get('#dialog-body');await f.find(body,'Ver video').dispatch('click');
 const request=f.calls.find(c=>c.path.includes('/documents/'));assert.equal(request.path,'/api/project-view/documents/v');assert.equal(request.options.headers.Authorization,'Bearer bearer-secret');assert.equal(request.options.credentials,'omit');
 const video=body.querySelector('video');assert.ok(video);assert.equal(video.controls,true);assert.equal(video.preload,'metadata');assert.equal(video.autoplay,undefined);assert.equal(f.urls[0].blob.type,'video/mp4');assert.equal(video.src,f.urls[0].url);assert.ok(f.find(body,'Descargar'));
 await f.find(body,'Ocultar video').dispatch('click');assert.ok(f.revoked.includes(video.src));
 await f.find(body,'Ver video').dispatch('click');const second=f.urls.at(-1).url;
 await f.nodes.get('#dialog-close').dispatch('click');assert.ok(f.revoked.includes(second));
 const rows=f.nodes.get('#content').all().filter(e=>e.className==='document-row');assert.equal(f.find(rows.find(r=>r.textContent.includes('Unsafe.html')),'Ver video'),undefined);
});
test('owner preview preserves authenticated download behavior and safely reports playback failures',async()=>{
 const project=videoProject();let denied=true;
 const f=mount({project,projects:[project],responder:path=>path.includes('/documents/')?denied?{ok:false,status:401}:{ok:true,blob:async()=>new Blob(['bytes'])}:null});await f.settle();await f.find(f.nodes.get('#content'),'Video work').dispatch('click');
 const row=f.nodes.get('#content').all().find(e=>e.className==='document-row');await f.find(row,'Ver video').dispatch('click');assert.match(row.textContent,/Inicia sesión/);assert.equal(f.urls.length,0);assert.equal(f.find(row,'Ver video').disabled,false);
 denied=false;await f.find(row,'Ver video').dispatch('click');const request=f.calls.find(c=>c.path.includes('/documents/'));assert.equal(request.path,'/api/projects/p/documents/v');assert.equal(request.options.credentials,'same-origin');assert.equal(request.options.headers.Authorization,undefined);
 await row.querySelector('video').dispatch('error');assert.match(row.textContent,/Descargar/);assert.ok(f.find(row,'Descargar'));await f.find(row,'Ocultar video').dispatch('click');
 await f.find(row,'Descargar').dispatch('click');assert.equal(f.doc.body.children.at(-1).download,'G1.mp4');
});
test('closing task during video fetch cancels preview without leaking object URLs',async()=>{
 let finish;const pending=new Promise(resolve=>finish=resolve);
 const f=mount({client:true,project:videoProject(),responder:path=>path.includes('/documents/')?{ok:true,blob:()=>pending}:null});await f.settle();await f.nodes.get('#content').all().find(e=>e.className==='task').dispatch('click');
 const loading=f.find(f.nodes.get('#dialog-body'),'Ver video').dispatch('click');await f.settle();await f.nodes.get('#dialog-close').dispatch('click');finish(new Blob(['video']));await loading;await f.settle();assert.equal(f.urls.length,0);
});

test('project actions use only SVG icons with accessible names and titles; video label tracks state',async()=>{
 const f=mount({client:true,project:videoProject(),responder:path=>path.includes('/documents/')?{ok:true,blob:async()=>new Blob(['bytes'])}:null});await f.settle();
 const download=f.find(f.nodes.get('#content'),'Descargar');assert.equal(download.textContent,'');assert.equal(download.title,'Descargar');assert.equal(download.attrs['aria-label'],'Descargar');assert.equal(download.querySelector('svg').attrs['aria-hidden'],'true');
 const close=f.nodes.get('#dialog-close');assert.equal(close.textContent,'');assert.equal(close.attrs['aria-label'],'Cerrar ventana');
 await f.nodes.get('#content').all().find(e=>e.className==='task').dispatch('click');const body=f.nodes.get('#dialog-body');const toggle=f.find(body,'Ver video');assert.equal(toggle.textContent,'');await toggle.dispatch('click');assert.equal(toggle.textContent,'');assert.equal(toggle.attrs['aria-label'],'Ocultar video');assert.equal(toggle.title,'Ocultar video');await toggle.dispatch('click');assert.equal(toggle.attrs['aria-label'],'Ver video');assert.ok(f.find(body,'Descargar'));
});
test('form save remains icon-only through loading and uses an accessible busy state',async()=>{
 let finish;const pending=new Promise(resolve=>finish=resolve);
 const f=mount({responder:path=>path==='/api/projects/command'?{ok:true,json:()=>pending}:null});await f.settle();await f.find(f.nodes.get('#content'),'Contactos y clientes').dispatch('click');await f.find(f.nodes.get('#content'),'Nuevo contacto').dispatch('click');
 const form=f.nodes.get('#dialog-body').querySelector('form');const save=f.find(form,'Guardar cambios');assert.ok(save);assert.equal(save.textContent,'');assert.ok(save.querySelector('svg'));assert.equal(f.find(form,'Cancelar').textContent,'');
 form.elements.namedItem('name').value='Test';form.elements.namedItem('status').value='contact';const submit=form.dispatch('submit');await f.settle();assert.equal(save.textContent,'');assert.equal(save.attrs['aria-label'],'Guardando…');assert.equal(save.attrs['aria-busy'],'true');assert.equal(save.disabled,true);finish({contact:{id:'new'}});await submit;assert.equal(save.attrs['aria-label'],'Guardar cambios');assert.equal(save.attrs['aria-busy'],'false');
});

test('project names that match actions remain readable content, not icon buttons',async()=>{
 const f=mount({projects:[{id:'p',name:'Descargar',status:'active'}]});await f.settle();const project=f.nodes.get('#content').all().find(e=>e.className==='project-open');assert.equal(project.textContent,'Descargar');assert.equal(project.querySelector('svg'),undefined);
});

test('recent deliverable task titles are content even when matching an action',async()=>{
 const project=videoProject();project.tasks[0].title='Descargar';project.tasks[0].code='';
 const f=mount({client:true,project});await f.settle();const recent=f.nodes.get('#content').all().find(e=>e.tagName==='section'&&e.textContent.includes('Entregables recientes'));const entry=recent.all().find(e=>e.tagName==='button');assert.equal(entry.textContent,'Descargar');assert.equal(entry.querySelector('svg'),undefined);
});
