const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const Renderer=require('../app/static/chat-renderer.js');
class Element{
 constructor(tag,ownerDocument){this.tagName=tag;this.ownerDocument=ownerDocument;this.children=[];this.attributes={};this._text='';this.dataset={};}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text+this.children.map(node=>node.textContent).join('');}
 append(...nodes){for(const node of nodes){node.remove();node.parentElement=this;this.children.push(node);}}
 remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(node=>node!==this);this.parentElement=null;}
 replaceChildren(...nodes){this._text='';this.children=[...nodes];}
 setAttribute(name,value){this.attributes[name]=String(value);}
 set innerHTML(value){throw Error('Untrusted HTML must never be parsed');}
}
const doc={createElement(tag){return new Element(tag,this);}};
const target=()=>doc.createElement('div');
function walk(node){return [node,...node.children.flatMap(walk)];}
function tree(node){return {tag:node.tagName,text:node._text,attributes:node.attributes,children:node.children.map(tree)};}

test('assistant prose has semantic headings, paragraphs, lists, emphasis and code',()=>{
 const el=target();Renderer.render(el,'## Veredicto\n\n**Útil con ajustes.** Revisa `settings.py`.\n\n- Primera razón\n- Segunda razón\n\n3. Inspeccionar\n4. Adaptar\n\n```python\nprint("ejemplo")\n```');
 const nodes=walk(el),tags=nodes.map(node=>node.tagName);
 for(const tag of ['h3','p','strong','code','ul','ol','li','pre'])assert.ok(tags.includes(tag),tag);
 assert.equal(nodes.find(node=>node.tagName==='ol').attributes.start,'3');
 assert.equal(nodes.find(node=>node.tagName==='pre').attributes.tabindex,'0');
 assert.equal(nodes.find(node=>node.tagName==='pre').textContent,'print("ejemplo")');
 assert.equal(nodes.find(node=>node.tagName==='strong').textContent,'Útil con ajustes.');
});

test('short emoji section labels and Unicode bullets have calm semantic structure',()=>{
 const el=target(),raw='🧩 Qué hace\nResume documentos.\n\n⚠️ Qué debes revisar\n• Necesita acceso a Internet.\n• No se instaló.';
 Renderer.render(el,raw);
 assert.deepEqual(walk(el).filter(node=>node.tagName==='h3').map(node=>node.textContent),['🧩 Qué hace','⚠️ Qué debes revisar']);
 assert.equal(walk(el).filter(node=>node.tagName==='li').length,2);assert.equal(Renderer.source(el),raw);
});

test('HTML, SVG, images and unsafe links remain harmless visible text',()=>{
 const raw='<script>alert(1)</script>\n<img src="https://tracker.test/pixel" onerror="alert(1)">\n<svg/onload=alert(1)>\n\n![oculta](https://tracker.test/pixel) [ejecutar](javascript:alert(1)) [datos](data:text/html,boom) [archivo](file:///etc/passwd)';
 const el=target();Renderer.render(el,raw);
 assert.equal(Renderer.source(el),raw);
 assert.match(el.textContent,/<script>alert\(1\)<\/script>/);
 assert.match(el.textContent,/!\[oculta\]/);
 assert.match(el.textContent,/javascript:alert/);
 assert.ok(walk(el).every(node=>!['img','script','svg','iframe','a'].includes(node.tagName)));
 assert.ok(walk(el).every(node=>Object.keys(node.attributes).every(name=>!/^on|src$/i.test(name))));
});

test('HTTP(S), credential URLs and all other links stay visible without creating controls',()=>{
 const el=target();Renderer.render(el,'[Documentación](https://example.test/docs?q=1&ok=2) https://plain.test [correo](mailto:owner@example.test) [clave](https://user:secret@example.test)');
 const links=walk(el).filter(node=>node.tagName==='a');
 assert.equal(links.length,0);
 assert.match(el.textContent,/\[Documentación\]\(https:\/\/example.test\/docs\?q=1&ok=2\)/);
 assert.match(el.textContent,/https:\/\/plain.test/);
});

test('only private generated-image registry URLs render images and download controls',()=>{
 const id='a'.repeat(32),url='/api/images/'+id,el=target();
 const raw='![Una taza]( '+url+' )';
 Renderer.render(el,'![Una taza]('+url+')');
 const image=walk(el).find(node=>node.tagName==='img'),link=walk(el).find(node=>node.tagName==='a');
 assert.ok(image);assert.equal(image.attributes.src,url);assert.equal(image.attributes.alt,'Una taza');
 assert.equal(image.attributes.referrerpolicy,'no-referrer');
 assert.equal(link.attributes.href,url);assert.equal(link.attributes.download,'imagen-'+id+'.png');
 const original=image;
 Renderer.render(el,'![Una taza]('+url+')\n\nLista.');
 assert.equal(walk(el).find(node=>node.tagName==='img'),original,'streaming reuses the image resource');
 const reloaded=target();Renderer.render(reloaded,Renderer.source(el));assert.deepEqual(tree(el),tree(reloaded));
 for(const unsafe of ['https://evil.test'+url,'//evil.test'+url,url+'?token=secret',url+'/../vault',url+'#fragment','/api/images/'+'A'.repeat(32),'/api/images/short','data:image/png;base64,aaa']){
  Renderer.render(el,'![No cargar]('+unsafe+')');assert.ok(walk(el).every(node=>!['img','a'].includes(node.tagName)),unsafe);
 }
 Renderer.render(el,'```\n![Código]('+url+')\n```');assert.ok(walk(el).every(node=>node.tagName!=='img'));
 Renderer.render(el,raw);assert.ok(walk(el).every(node=>node.tagName!=='img'));
});

test('every streamed prefix and reload keep exactly the original raw source',()=>{
 const raw='## Resultado\r\n\r\n**Todavía parcial** y `valor`.\n\n- Uno\n- Dos\n\n```js\nconst html = "<img src=x>";\n```\n\n[Más](https://example.test)';
 const streamed=target(),saved=target();let accumulated='';
 for(const character of raw){accumulated+=character;Renderer.render(streamed,accumulated);assert.equal(Renderer.source(streamed),accumulated);}
 Renderer.render(saved,raw);assert.deepEqual(tree(streamed),tree(saved));
 // Rendering is derived; changes made to the DOM never become canonical source.
 streamed.textContent='tampered display';assert.equal(Renderer.source(streamed),raw);
 Renderer.render(streamed,raw);assert.deepEqual(tree(streamed),tree(saved));
});

test('open fences, incomplete markers and terminal explanations preserve their source',()=>{
 for(const raw of ['', '**incompleto', '`parcial', '```bash\necho "sin ejecutar"', '## Parcial\n\nTexto\n\nLa respuesta fue detenida.']){
  const el=target();Renderer.render(el,raw);assert.equal(Renderer.source(el),raw);
  Renderer.render(el,raw);assert.equal(Renderer.source(el),raw);
 }
 const el=target();Renderer.render(el,'```\n<img src=x>\n');
 assert.ok(walk(el).some(node=>node.tagName==='pre'));
 assert.ok(walk(el).every(node=>node.tagName!=='img'));
});

test('application uses source accumulation instead of rendered DOM for live and history replies',()=>{
 const code=fs.readFileSync(require('node:path').join(__dirname,'../app/static/app.js'),'utf8');
 assert.doesNotMatch(code,/output\.textContent\s*\+?=/);
 assert.doesNotMatch(code,/const partial=output\.textContent/);
 const messageSource=code.slice(code.indexOf('function message('),code.indexOf('let historyOffset='));
 const helper=code.slice(code.indexOf('function renderAssistant('),code.indexOf('async function readChatEvents('));
 const messages=target(),ctx={document:doc,$:()=>messages,HermesChatRenderer:Renderer};vm.createContext(ctx);vm.runInContext(helper+messageSource,ctx);
 const raw='## Saved\n\n**Safe** <img src=x>';
 const reply=ctx.message('assistant',raw),user=ctx.message('user',raw);
 assert.equal(Renderer.source(reply),raw);assert.ok(walk(reply).some(node=>node.tagName==='strong'));
 assert.equal(user.textContent,raw);assert.ok(user.children.length===0);
 const html=fs.readFileSync(require('node:path').join(__dirname,'../app/static/index.html'),'utf8');
 assert.ok(html.includes('/static/chat-renderer.js'));
 assert.ok(html.indexOf('/static/chat-renderer.js')<html.indexOf('/static/app.js'));
});

function chatHarness(raw,terminal,persisted){
 const code=fs.readFileSync(require('node:path').join(__dirname,'../app/static/app.js'),'utf8');
 const nodes=new Map(),node=id=>{if(!nodes.has(id))nodes.set(id,target());return nodes.get(id);};
 const frames=[...raw].map(delta=>'data: '+JSON.stringify({type:'assistant.delta',delta})+'\n\n');
 frames.push('data: '+JSON.stringify({type:terminal})+'\n\n');
 const ctx={document:{...doc,body:{scrollHeight:1}},$:node,HermesChatRenderer:Renderer,
  HermesQuarantineChat:require('../app/static/quarantine-chat.js'),HermesActivity:{view(){return {history(){},render(){},finish(){},event(){}};}},
  TextDecoder,JSON,Set,localStorage:{setItem(){}},window:{scrollTo(){}},sid:'owner_'+'a'.repeat(32),
  messageOffset:0,running:false,mediaBusy:false,voice:null,attachments:[],runId:'',historyBusy:false,deleting:false,
  skillPicker:null,quarantineHistory:[],quarantineShown:new Set(),welcomeMarkup:'',switching(){},clearNotice(){},notice(){},
  navigationState(){},renderAttachments(){},unavailable(){return false;},api:async path=>{
   if(path==='/api/chat')return {body:{getReader(){return {async read(){return frames.length?{done:false,value:new TextEncoder().encode(frames.shift())}:{done:true};},async cancel(){}};}}};
   return {async json(){return path.startsWith('/api/quarantine/chat/')?{messages:[]}:{data:persisted};}};
  }};
 vm.createContext(ctx);
 vm.runInContext(code.slice(code.indexOf('function message('),code.indexOf('let historyOffset=')),ctx);
 vm.runInContext(code.slice(code.indexOf('function chatFailure('),code.indexOf('async function conversations(')),ctx);
 vm.runInContext(code.slice(code.indexOf("$('chat-form').onsubmit="),code.indexOf('function renderAttachments(')),ctx);
 return {ctx,node,async submit(){node('message').value='Revisa esto';await node('chat-form').onsubmit({preventDefault(){}});return node('messages');}};
}

test('real SSE and persisted history use identical rendering without duplicating Markdown source',async()=>{
 const raw='## Resumen\n\n**Con ajustes.** Revisa `tool.py`.\n\n• Uno\n• Dos';
 const h=chatHarness(raw,'run.completed',[{role:'user',content:'Revisa esto'},{role:'assistant',content:raw}]);
 const messages=await h.submit(),answers=walk(messages).filter(node=>node.className==='assistant-content');
 assert.equal(answers.length,1);assert.equal(Renderer.source(answers[0]),raw);
 const direct=target();Renderer.render(direct,raw);assert.deepEqual(tree(answers[0]),tree(direct));
 assert.equal(messages.textContent.split('Con ajustes.').length-1,1);
 await h.ctx.loadConversation(h.ctx.sid);
 const reloaded=walk(h.node('messages')).filter(node=>node.className==='assistant-content');
 assert.equal(reloaded.length,1);assert.equal(Renderer.source(reloaded[0]),raw);
});

test('cancelled or failed unfinished code keeps raw text and a separate readable outcome',async()=>{
 const raw='```python\nprint("partial")';
 for(const terminal of ['run.cancelled','run.failed']){
  const h=chatHarness(raw,terminal,[{role:'user',content:'Revisa esto'}]);
  const messages=await h.submit(),answers=walk(messages).filter(node=>node.className==='assistant-content');
  assert.ok(answers.some(node=>Renderer.source(node)===raw));
  const note=walk(messages).find(node=>node.className==='message-explanation');
  assert.ok(note);assert.equal(note.parentElement.tagName,'div');
  assert.match(note.textContent,/detenida|no pudo completar/);
  assert.equal(messages.textContent.split('print("partial")').length-1,1);
 }
});
