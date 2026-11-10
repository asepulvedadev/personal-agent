const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

test('el procesador convierte mono a PCM16 sin reproducir el micrófono',()=>{
  let Processor;const frames=[];
  class Base { constructor(){this.port={postMessage:item=>frames.push(item)};} }
  const context={AudioWorkletProcessor:Base,Int16Array,registerProcessor:(_,value)=>Processor=value};
  vm.runInNewContext(fs.readFileSync('app/static/pcm-worklet.js','utf8'),context);
  const processor=new Processor();const samples=new Float32Array(2400);samples[0]=-1;samples[1]=1;
  assert.equal(processor.process([[samples]]),true);
  const frame=new Int16Array(frames[0].pcm);
  assert.equal(frame.length,2400);assert.equal(frame[0],-32768);assert.equal(frame[1],32767);
});

test('el dictado sustituye el parcial por el final y libera el micrófono',async()=>{
  let socket;let stops=0;const states=[],texts=[];
  const track={stop:()=>stops++};
  class Socket {static OPEN=1;static CONNECTING=0;constructor(url){assert.equal(url,'wss://agent.example.test/api/audio/live');socket=this;this.readyState=1;this.sent=[];this.bufferedAmount=0;}send(item){this.sent.push(item);}close(){this.readyState=3;}}
  class Context {constructor(options){assert.equal(options.sampleRate,24000);this.state='running';this.audioWorklet={addModule:async()=>{}};this.destination={};}async resume(){}createMediaStreamSource(){return {numberOfOutputs:1,connect(){},disconnect(){}};}async close(){this.state='closed';}}
  class Worklet {constructor(){this.port={postMessage:()=>{}};}connect(){}disconnect(){}}
  const context={window:{AudioWorkletNode:Worklet},navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[track]})}},location:{origin:'https://agent.example.test'},AudioContext:Context,AudioWorkletNode:Worklet,WebSocket:Socket,setTimeout:()=>1,clearTimeout(){},Map,JSON,Error,Promise};
  vm.runInNewContext(fs.readFileSync('app/static/voice.js','utf8'),context);
  const voice=new context.window.WorkspaceVoice({csrf:'csrf-only',onState:x=>states.push(x),onText:x=>texts.push(x),onError:()=>assert.fail('sin error esperado')});
  await voice.start();socket.onopen();assert.deepEqual(JSON.parse(socket.sent[0]),{csrf:'csrf-only'});
  socket.onmessage({data:JSON.stringify({type:'ready'})});
  socket.onmessage({data:JSON.stringify({type:'delta',id:'one',text:'Hola'})});
  socket.onmessage({data:JSON.stringify({type:'completed',id:'one',text:'Hola mundo'})});
  assert.deepEqual(texts,['Hola','Hola mundo']);
  voice.close();assert.equal(stops,1);assert.equal(states.at(-1),'closed');assert.equal(socket.readyState,3);
});
