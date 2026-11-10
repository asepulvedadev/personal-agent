const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const test=require('node:test');
const src=fs.readFileSync(require('path').join(__dirname,'../app/static/app.js'),'utf8');
const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,disabled:false,innerHTML:'',textContent:'',focus(){},remove(){}});return nodes.get(id);};
const persisted=Array.from({length:400},(_,i)=>({role:'user',content:String(i+1)}));let rendered=[],offsets=[];
const ctx={HermesQuarantineChat:require('../app/static/quarantine-chat.js'),quarantineHistory:[],quarantineShown:new Set(),$:node,TextDecoder,JSON,URLSearchParams,Set,localStorage:{setItem(){}},window:{scrollTo(){}},sid:'',messageOffset:0,running:false,mediaBusy:false,voice:null,attachments:[],runId:'',historyBusy:false,deleting:false,skillPicker:null,switching(){},clearNotice(){},notice(){},navigationState(){},renderAttachments(){},unavailable(){return false;},renderMessages(list,prepend=false){rendered=prepend?[...list.map(x=>x.content),...rendered]:list.map(x=>x.content);},message(role,text){rendered.push(text);return {textContent:''};},api:async(path,options)=>{
 if(path.startsWith('/api/quarantine/chat/'))return {async json(){return {messages:[]};}};
 if(path==='/api/chat') {persisted.push({role:'user',content:'new user'},{role:'assistant',content:'new answer'});let read=false;return {body:{getReader(){return {async read(){if(read)return {done:true};read=true;return {done:false,value:new TextEncoder().encode('event: run.completed\ndata: {"type":"run.completed"}\n\n')};}};}}};}
 const offset=Number(path.match(/offset=(\d+)/)?.[1]||0);offsets.push(offset);return {async json(){return {data:persisted.slice(0,persisted.length-offset).slice(-200)};}};
}};
vm.createContext(ctx);
vm.runInContext(src.slice(src.indexOf('function chatFailure('),src.indexOf('function renderMessages(')),ctx);
vm.runInContext(src.slice(src.indexOf('async function loadConversation('),src.indexOf('async function conversations(')),ctx);
vm.runInContext(src.slice(src.indexOf("$('older-messages').onclick="),src.indexOf('function confirmDeletion(')),ctx);
vm.runInContext(src.slice(src.indexOf("$('chat-form').onsubmit="),src.indexOf('function renderAttachments(')),ctx);
test('la paginación se actualiza después de guardar un turno',async()=>{
 await ctx.loadConversation('owner_'+'a'.repeat(32));
 node('message').value='new user';await node('chat-form').onsubmit({preventDefault(){}});
 await node('older-messages').onclick();await node('older-messages').onclick();
 const dupes=rendered.filter((x,i)=>rendered.indexOf(x)!==i);
 assert.deepEqual(dupes,[]);
 assert.deepEqual(['1','2'].filter(x=>!rendered.includes(x)),[]);
 assert.deepEqual(offsets,[0,0,200,400]);
});
