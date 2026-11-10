const test=require('node:test'),assert=require('node:assert/strict');
const Picker=require('../app/static/skills.js');

test('$ y prefijos activan el selector, pero precios y correos no',()=>{
 assert.deepEqual(Picker.token('Usa $web',8),{start:4,end:8,query:'web'});
 assert.equal(Picker.token('$',1).query,'');
 assert.equal(Picker.token('precio $500',11),null);
 assert.equal(Picker.token('email$name',10),null);
 assert.equal(Picker.token('$$HOME',6),null);
 assert.equal(Picker.token('texto $web algo',10).query,'web');
});

test('la inserción respeta el texto a ambos lados y deja el cursor tras el nombre',()=>{
 const value='Antes $web Después';const token=Picker.token(value,10);
 const inserted=Picker.insert(value,token,'web-design');
 assert.equal(inserted.value,'Antes $web-design Después');
 assert.equal(inserted.cursor,18);
 assert.equal(Picker.insert('$',Picker.token('$',1),'research').value,'$research ');
});

test('filtra por nombre, descripción y categoría sin interpretar HTML',()=>{
 const rows=[{name:'web-design',description:'Build websites',category:'web-development'},
             {name:'research',description:'Search sources',category:'research'}];
 assert.deepEqual(Picker.filter(rows,'web').map(x=>x.name),['web-design']);
 assert.deepEqual(Picker.filter(rows,'sources').map(x=>x.name),['research']);
 assert.deepEqual(Picker.filter(rows,'<img>'),[]);
});

function setup(fetchSkills){
 class Element{
  constructor(doc){this.ownerDocument=doc;this.children=[];this.listeners={};this.attrs={};this.value='';this.selectionStart=0;this.selectionEnd=0;this.hidden=true;this.disabled=false;this.maxLength=20000;this.textContent='';}
  addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
  dispatchEvent(event){for(const fn of this.listeners[event.type]||[])fn(event);}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=[...items];}
  setAttribute(name,value){this.attrs[name]=value;}
  removeAttribute(name){delete this.attrs[name];}
  contains(target){return this===target||this.children.some(child=>child.contains(target));}
  focus(){}
  setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}
  scrollIntoView(){}
 }
 const doc={addEventListener(){},createElement(){return new Element(doc);}};
 const textarea=new Element(doc),panel=new Element(doc),list=new Element(doc),status=new Element(doc);panel.append(list,status);
 const state=Picker.mount({textarea,panel,list,status,fetchSkills});
 function value(text,caret=text.length){textarea.value=text;textarea.setSelectionRange(caret,caret);}
 function key(key,extras={}){const event={key,prevented:false,preventDefault(){this.prevented=true;},...extras};const handled=state.keydown(event);return {handled,event};}
 return {textarea,panel,list,status,state,value,key};
}

test('teclado selecciona sin enviar, Esc conserva el borrador y Shift+Enter no se consume',async()=>{
 const f=setup(async()=>[{name:'alpha',category:'research'},{name:'beta',category:'creative'}]);
 f.value('Usa $');await f.state.update();assert.equal(f.panel.hidden,false);
 assert.equal(f.key('ArrowDown').handled,true);
 assert.equal(f.key('Enter').handled,true);assert.equal(f.textarea.value,'Usa $beta ');assert.equal(f.panel.hidden,true);
 f.value('Borrador $a');await f.state.update();f.key('Escape');assert.equal(f.textarea.value,'Borrador $a');
 await f.state.update();assert.equal(f.panel.hidden,true);
 f.value('$b');await f.state.update();assert.equal(f.key('Enter',{shiftKey:true}).handled,false);
 f.value('$a');await f.state.update();assert.equal(f.key('Tab').handled,true);assert.equal(f.textarea.value,'$alpha ');
});

test('una consulta lenta no reabre el selector después de cerrarlo o dejar de escribir $',async()=>{
 let resolve;const promise=new Promise(done=>resolve=done);const f=setup(()=>promise);
 f.value('$');const update=f.state.update();f.value('Ya no hay una mención');f.state.close();
 resolve([{name:'research'}]);await update;assert.equal(f.panel.hidden,true);assert.equal(f.list.children.length,0);
});

test('clic inserta nombres como texto y un error o lista vacía no envía nada',async()=>{
 const f=setup(async()=>[{name:'research',description:'<img onerror=private()>',category:'<script>'},{name:'<script>'}]);
 f.value('Usa $r ahora',6);await f.state.update();assert.equal(f.list.children.length,1);
 assert.equal(f.list.children[0].children[1].textContent,'Skill instalada');
 f.list.children[0].dispatchEvent({type:'click'});assert.equal(f.textarea.value,'Usa $research ahora');
 const x=setup(async()=>{throw new Error('private upstream error');});x.value('$');await x.state.update();
 assert.match(x.status.textContent,/No se pudieron cargar/);assert.doesNotMatch(x.status.textContent,/private/);
 assert.equal(x.key('Enter').handled,true);assert.equal(x.textarea.value,'$');
});

test('Esc no bloquea una nueva entrada idéntica después de cambiar el borrador',async()=>{
 const f=setup(async()=>[{name:'popular-web-designs'}]);
 f.value('$popular');await f.state.update();f.key('Escape');assert.equal(f.panel.hidden,true);
 f.value('');f.textarea.dispatchEvent({type:'input'});
 f.value('$popular');f.textarea.dispatchEvent({type:'input'});await f.state.update();
 assert.equal(f.panel.hidden,false);assert.equal(f.key('Tab').handled,true);
 assert.equal(f.textarea.value,'$popular-web-designs ');
});
