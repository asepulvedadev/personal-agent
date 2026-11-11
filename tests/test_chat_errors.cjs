const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),test=require('node:test');
const src=fs.readFileSync(require('node:path').join(__dirname,'../app/static/app.js'),'utf8');
class Element{
 constructor(){this.children=[];this._text='';this.value='';this.hidden=false;this.disabled=false;this.dataset={};}
 get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}
 set textContent(value){this._text=String(value);this.children=[];}
 append(...nodes){for(const node of nodes){node.remove();node.parentElement=this;this.children.push(node);}}
 replaceChildren(...nodes){this._text='';this.children=[];this.append(...nodes);}
 remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(x=>x!==this);}
 setAttribute(){} focus(){}
}
function harness({frames=[],persisted=[{role:'user',content:'hello'}],error=null,readError=null}={}){
 const nodes=new Map(),node=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
 let reads=0;
 const ctx={$:node,document:{createElement:()=>new Element(),body:{scrollHeight:1}},HermesQuarantineChat:require('../app/static/quarantine-chat.js'),HermesActivity:{view(){return {history(){},render(){},finish(){},event(){}};}},TextDecoder,JSON,URLSearchParams,Set,localStorage:{setItem(){}},window:{scrollTo(){}},sid:'owner_'+'a'.repeat(32),messageOffset:0,running:false,mediaBusy:false,voice:null,attachments:[],runId:'',historyBusy:false,deleting:false,skillPicker:null,quarantineHistory:[],quarantineShown:new Set(),welcomeMarkup:'welcome',switching(){},clearNotice(){node('notice').textContent='';},notice(text){node('notice').textContent=text;},navigationState(){},renderAttachments(){},unavailable(){return false;},api:async(path)=>{
  if(path==='/api/chat'){
   if(error)throw error;
   return {body:{getReader(){return {async read(){if(reads<frames.length)return {done:false,value:typeof frames[reads]==='string'?new TextEncoder().encode(frames[reads++]):frames[reads++]};if(readError)throw readError;return {done:true};},async cancel(){}};}}};
  }
  return {async json(){return path.startsWith('/api/quarantine/chat/')?{messages:[]}:{data:persisted};}};
 }};
 vm.createContext(ctx);
 vm.runInContext(src.slice(src.indexOf('function message('),src.indexOf('let historyOffset=')),ctx);
 const helpers=src.indexOf('function chatFailure(');
 vm.runInContext(src.slice(helpers<0?src.indexOf('function renderMessages('):helpers,src.indexOf('async function conversations(')),ctx);
 vm.runInContext(src.slice(src.indexOf("$('chat-form').onsubmit="),src.indexOf('function renderAttachments(')),ctx);
 return {ctx,node,async submit(){node('message').value='hello';await node('chat-form').onsubmit({preventDefault(){}});return node('messages').textContent;}};
}
const frame=(type,data={})=>`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
const secret='sk-secret-never-show /private/config.env';
test('failed run explains the unconfirmed cause inline and after history reconciliation without echoing errors',async()=>{
 const h=harness({frames:[frame('run.failed',{error:secret,message:secret})]});const text=await h.submit();
 assert.match(text,/no pudo completar/i);assert.match(text,/causa/i);assert.doesNotMatch(text,/sk-secret|\/private|OpenAI/);assert.equal(h.ctx.running,false);
});
test('cancellation remains visible after reload rather than leaving an empty assistant bubble',async()=>{
 assert.match(await harness({frames:[frame('run.cancelled')]}).submit(),/detenida/i);
});
for(const content of ['', '   \n'])test(`empty completion (${JSON.stringify(content)}) is explained inline`,async()=>{
 assert.match(await harness({frames:[frame('assistant.delta',{delta:content}),frame('run.completed')]}).submit(),/sin una respuesta visible/i);
});
for(const end of ['eof','error','read'])test(`partial answer is kept with an honest inline ${end} explanation`,async()=>{
 const frames=[frame('assistant.delta',{delta:'Partial answer'})];if(end==='error')frames.push(frame('error',{message:secret}),'event: done\ndata: {}\n\n');
 const h=harness({frames,readError:end==='read'?new Error(secret):null});const text=await h.submit();
 assert.match(text,/Partial answer/);assert.match(text,/conexión|no pudo completar/i);assert.doesNotMatch(text,/sk-secret|\/private/);
});
test('SSE error code is allowlisted and never interpolates upstream details',async()=>{
 const text=await harness({frames:[frame('error',{code:'timeout',message:secret})]}).submit();
 assert.match(text,/tiempo de espera/i);assert.doesNotMatch(text,/sk-secret/);
});
test('malformed SSE cannot masquerade as a successful answer',async()=>{
 const text=await harness({frames:['event: assistant.delta\ndata: not-json\n\n',frame('run.completed')]}).submit();
 assert.match(text,/interpretar/i);
});
test('SSE handles multiline data, split UTF-8, and a final frame without newline',async()=>{
 const bytes=new TextEncoder().encode('event: assistant.delta\r\ndata: {"delta":\r\ndata: "Respuesta válida"}\r\n\r\n');
 const split=bytes.indexOf(0xc3)+1;
 const text=await harness({frames:[bytes.slice(0,split),bytes.slice(split),'event: run.completed\ndata: {}'],persisted:[{role:'user',content:'hello'},{role:'assistant',content:'Respuesta válida'}]}).submit();
 assert.match(text,/Respuesta válida/);assert.doesNotMatch(text,/sin una respuesta|no pudo|confirmado/);
});
test('HTTP and network failures are safe inline explanations, not raw exception text',async()=>{
 for(const status of [undefined,401,403,409,502,504]){
  const text=await harness({error:Object.assign(new Error(secret),{status})}).submit();
  assert.match(text,/Estado|sesión|conexión|tiempo de espera/i);assert.doesNotMatch(text,/sk-secret|\/private/);
 }
});
test('rejected inputs and limits are not misreported as disconnected streams',async()=>{
 for(const [status,expected] of [[400,/solicitud.*rechazada/i],[413,/tamaño permitido/i],[422,/solicitud.*rechazada/i],[429,/solicitudes/i]]){
  const text=await harness({error:Object.assign(new Error(secret),{status})}).submit();
  assert.match(text,expected);assert.doesNotMatch(text,/conexión terminó|sk-secret|\/private/);
 }
});
test('reopening native history with missing final answer reports uncertainty without inventing a saved cause',async()=>{
 for(const tail of [[],[{role:'assistant',content:'  '}],[{role:'assistant',content:'',tool_calls:[{id:'t'}]},{role:'tool',content:secret}]]){
  const h=harness({persisted:[{role:'user',content:'hello'},...tail]});await h.ctx.loadConversation(h.ctx.sid);
  assert.match(h.node('messages').textContent,/No hay una respuesta final guardada/);assert.match(h.node('messages').textContent,/no está confirmado/);assert.doesNotMatch(h.node('messages').textContent,/sk-secret/);
 }
});
test('pagination does not insert missing-outcome warnings into older pages',()=>{
 const h=harness();h.ctx.renderMessages([{role:'assistant',content:'Saved answer'}]);h.ctx.renderMessages([{role:'user',content:'older'}],true);
 assert.equal(h.node('messages').textContent,'TúolderHermesSaved answer');
});
test('saved partial answer is not duplicated when a failed run is reconciled',async()=>{
 const text=await harness({frames:[frame('assistant.delta',{delta:'Partial answer'}),frame('run.failed')],persisted:[{role:'user',content:'hello'},{role:'assistant',content:'Partial answer'}]}).submit();
 assert.equal(text.split('Partial answer').length-1,1);assert.match(text,/no pudo completar/i);
});
test('a successful answer is rendered once without an error fallback',async()=>{
 const text=await harness({frames:[frame('assistant.delta',{delta:'Saved answer'}),frame('run.completed')],persisted:[{role:'user',content:'hello'},{role:'assistant',content:'Saved answer'}]}).submit();
 assert.equal(text,'TúhelloHermesSaved answer');
});

test('reopening history keeps a missing-response explanation between later turns',()=>{
 const h=harness();h.ctx.renderMessages([{role:'user',content:'unfinished'},{role:'assistant',content:''},{role:'user',content:'next'},{role:'assistant',content:'answer'}]);
 assert.match(h.node('messages').textContent,/unfinishedHermesNo hay una respuesta final guardada.*Túnext/);
});
test('completed text survives reconciliation when native history has not saved it',async()=>{
 const text=await harness({frames:[frame('assistant.delta',{delta:'Received answer'}),frame('run.completed')]}).submit();
 assert.match(text,/Received answer/);assert.match(text,/No hay una respuesta final guardada/);
});

test('payload-type SSE preserves the quarantine stream shape without event names',async()=>{
 const text=await harness({frames:['data: {"type":"assistant.delta","delta":"Saved answer"}\n\n','data: {"type":"run.completed"}\n\n'],persisted:[{role:'user',content:'hello'},{role:'assistant',content:'Saved answer'}]}).submit();
 assert.equal(text,'TúhelloHermesSaved answer');
});
