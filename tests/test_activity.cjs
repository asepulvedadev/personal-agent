const test=require('node:test');
const assert=require('node:assert/strict');
const Activity=require('../app/static/activity.js');

test('reconoce las dos formas SSE y solo el nombre permitido de una skill',()=>{
 const t=new Activity.Tracker();
 t.event('tool.started',{tool_name:'skill_view',args:{name:'research',file_path:'/secret/file'},preview:'sk-DO-NOT-SHOW'});
 assert.equal(t.items[0].skill,'research');assert.equal(t.items[0].status,'running');
 t.event('tool.completed',{tool_name:'skill_view'});
 assert.equal(t.items[0].status,'completed');
 t.event('tool.started',{tool:'terminal',tool_call_id:'c2',args:{command:'print secret'},preview:'private'});
 t.event('tool.completed',{tool:'terminal',tool_call_id:'c2',error:true});
 assert.equal(t.items[1].status,'failed');
 assert.doesNotMatch(JSON.stringify(t.items),/secret|private|DO-NOT-SHOW/);
});

test('no confunde listar skills con cargarlas ni muestra razonamiento o previews',()=>{
 const t=new Activity.Tracker();
 t.event('tool.started',{tool_name:'skills_list',args:{name:'not-loaded'}});
 t.event('reasoning.available',{text:'private thought'});
 t.event('tool.progress',{tool_name:'_thinking',delta:'private thought'});
 assert.equal(t.items.length,1);assert.equal(t.items[0].skill,null);
 t.event('tool.started',{tool_name:'skill_view',args:{name:'sk-private-credential'}});
 assert.equal(t.items[1].skill,null);
 t.event('tool.started',{tool_name:'<img onerror=alert(1)>',preview:'private'});
 assert.equal(t.items.length,2);
});

test('historial correlaciona herramientas y conserva incertidumbre sin mostrar resultados',()=>{
 const t=new Activity.Tracker();
 t.history({role:'assistant',tool_calls:[{id:'a',function:{name:'skill_view',arguments:'{"name":"research","secret":"private"}'}},{id:'b',function:{name:'terminal',arguments:'{"command":"private"}'}}]});
 t.history({role:'tool',tool_call_id:'a',tool_name:'skill_view',content:'secret output'});
 assert.equal(t.items[0].status,'recorded');assert.equal(t.items[0].skill,'research');
 t.finish('history');assert.equal(t.items[1].status,'unconfirmed');
 assert.doesNotMatch(JSON.stringify(t.items),/secret|private/);
});

test('herramientas simultáneas sin identificador no se asignan arbitrariamente',()=>{
 const t=new Activity.Tracker();
 t.event('tool.started',{tool_name:'skill_view',args:{name:'research'}});
 t.event('tool.started',{tool_name:'skill_view',args:{name:'coding'}});
 t.event('tool.failed',{tool_name:'skill_view'});
 assert.equal(t.items[0].status,'unconfirmed');assert.equal(t.items[1].status,'unconfirmed');
 assert.equal(t.items[2].status,'failed');assert.equal(t.items[2].skill,null);
});

test('cancelación y desconexión no se presentan como ejecución correcta',()=>{
 const t=new Activity.Tracker();t.event('tool.started',{tool_name:'terminal'});t.finish('cancelled');
 assert.equal(t.items[0].status,'stopped');
 const x=new Activity.Tracker();x.event('tool.started',{tool_name:'web_search'});x.finish('disconnected');
 assert.equal(x.items[0].status,'unconfirmed');
});

test('la vista usa texto, muestra la actividad y nunca inserta argumentos o resultados',()=>{
 class Element{
  constructor(tag){this.tagName=tag;this.children=[];this.parentNode=null;this.dataset={};this.textContent='';}
  append(...nodes){for(const node of nodes){node.parentNode=this;this.children.push(node);}}
  insertBefore(node,before){node.parentNode=this;const i=this.children.indexOf(before);if(i<0)this.children.push(node);else this.children.splice(i,0,node);}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  setAttribute(name,value){this[name]=value;}
 }
 const old=global.document;global.document={createElement:tag=>new Element(tag)};
 try{
  const container=new Element('div');const answer=new Element('div');container.append(answer);
  const v=Activity.view(container,answer);
  v.event('tool.started',{tool_name:'skill_view',args:{name:'research',file_path:'private.env'},preview:'private secret'});
  assert.equal(container.children[0].tagName,'details');assert.equal(container.children[0].open,true);
  const text=el=>el.textContent+' '+el.children.map(text).join(' ');
  assert.match(text(container),/Skill · research/);assert.match(text(container),/En curso/);
  v.finish('disconnected');assert.match(text(container),/Sin confirmación/);
  assert.doesNotMatch(text(container),/private|secret/);
 }finally{global.document=old;}
});
