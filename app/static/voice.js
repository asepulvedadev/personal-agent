'use strict';
/* Server-authenticated, same-origin dictation. No API credential enters this module. */
window.WorkspaceVoice = class {
  constructor(options) {
    this.options=options;this.items=new Map();this.closed=false;this.stopping=false;
  }
  async start() {
    try {
      if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) throw new Error('unsupported');
      this.options.onState('connecting');
      this.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});
      if(this.closed){this.stream.getTracks().forEach(track=>track.stop());return;}
      this.context=new AudioContext({sampleRate:24000});
      await this.context.audioWorklet.addModule('/static/pcm-worklet.js');
      await this.context.resume();
      this.node=new AudioWorkletNode(this.context,'microphone-pcm');
      this.source=this.context.createMediaStreamSource(this.stream);
      this.node.port.onmessage=event=>{
        if(event.data.flushed){this.flushDone?.();return;}
        if(event.data.pcm && this.socket?.readyState===WebSocket.OPEN && !this.closed){
          if(this.socket.bufferedAmount>96000){this.fail('La conexión no alcanza para el dictado. Intenta de nuevo.');return;}
          this.socket.send(event.data.pcm);
        }
      };
      this.socket=new WebSocket(location.origin.replace(/^http/,'ws')+'/api/audio/live');
      this.socket.onopen=()=>this.socket.send(JSON.stringify({csrf:this.options.csrf}));
      this.socket.onmessage=event=>{
        let data;try{data=JSON.parse(event.data);}catch{return;}
        if(data.type==='ready'){
          this.source.connect(this.node);this.node.connect(this.context.destination);
          this.options.onState('recording');
          this.limit=setTimeout(()=>this.stop(),175000);
        }else if(data.type==='delta'||data.type==='completed'){
          const previous=this.items.get(data.id)||'';
          this.items.set(data.id,data.type==='completed'?data.text:previous+data.text);
          this.options.onText([...this.items.values()].join(' '));
        }else if(data.type==='error')this.fail(data.text);
      };
      this.socket.onerror=()=>this.fail('No se pudo conectar el dictado. Revisa Estado o adjunta un audio.');
      this.socket.onclose=()=>{if(!this.closed&&!this.stopping)this.options.onError('El dictado se desconectó. Revisa el texto antes de enviarlo.');this.close();};
      this.connectionLimit=setTimeout(()=>{if(!this.closed&&!this.limit)this.fail('El dictado tardó demasiado en conectarse.');},20000);
      this.stream.getTracks().forEach(track=>track.onended=()=>this.stop());
    }catch(error){
      this.fail(error.name==='NotAllowedError'?'Permite el micrófono para dictar. Puedes seguir escribiendo.':
        error.name==='NotFoundError'?'No se encontró un micrófono. Puedes adjuntar un audio.':'No se pudo iniciar el micrófono en este navegador.');
    }
  }
  async stop() {
    if(this.closed||this.stopping)return;
    this.stopping=true;clearTimeout(this.limit);clearTimeout(this.connectionLimit);
    this.options.onState('finishing');
    this.stream?.getTracks().forEach(track=>{track.onended=null;track.stop();});
    if(this.node){
      await Promise.race([new Promise(resolve=>{this.flushDone=resolve;this.node.port.postMessage('flush');}),new Promise(resolve=>setTimeout(resolve,400))]);
    }
    this.source?.disconnect();this.node?.disconnect();
    if(this.context?.state!=='closed')await this.context?.close();
    if(this.socket?.readyState===WebSocket.OPEN){
      this.socket.send(JSON.stringify({type:'stop'}));
      this.finishLimit=setTimeout(()=>this.fail('El dictado terminó sin confirmar el texto completo. Revisa lo transcrito.'),22000);
    }else this.close();
  }
  fail(text){if(!this.closed)this.options.onError(text);this.close();}
  close(){
    if(this.closed)return;this.closed=true;
    clearTimeout(this.limit);clearTimeout(this.connectionLimit);clearTimeout(this.finishLimit);
    this.stream?.getTracks().forEach(track=>{track.onended=null;track.stop();});
    this.source?.disconnect();this.node?.disconnect();
    if(this.context?.state!=='closed')this.context?.close().catch(()=>{});
    if(this.socket?.readyState===WebSocket.OPEN||this.socket?.readyState===WebSocket.CONNECTING)this.socket.close();
    this.options.onState('closed');
  }
};
