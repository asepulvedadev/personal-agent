/* A deliberately small Markdown view. Source is data, never HTML or application state. */
(function(root){
  'use strict';
  const sources=new WeakMap();
  function text(parent,value,document){
    const node=document.createElement('span');node.textContent=value;parent.append(node);
  }
  function inline(parent,value,document){
    // Images and links are matched whole and retained as text, never resources or controls.
    const tokens=/`([^`\n]+)`|\*\*([^*\n]+)\*\*|(!?)\[([^\]\n]*)\]\(([^\s)\n]+)\)/g;
    let offset=0,match;
    while((match=tokens.exec(value))){
      if(match.index>offset)text(parent,value.slice(offset,match.index),document);
      let node=null;
      if(match[1]!==undefined){node=document.createElement('code');node.textContent=match[1];}
      else if(match[2]!==undefined){node=document.createElement('strong');node.textContent=match[2];}
      if(node)parent.append(node);else text(parent,match[0],document);
      offset=tokens.lastIndex;
    }
    if(offset<value.length)text(parent,value.slice(offset),document);
  }
  const heading=/^ {0,3}(#{1,6})\s+(.+)$/;
  const labelHeading=/^\p{Extended_Pictographic}\uFE0F? +[\p{L}\p{N}][\p{L}\p{M}\p{N} :,-]{0,78}$/u;
  const bullet=/^ {0,3}([-*+•]|\d+[.)])\s+(.+)$/;
  const fence=/^ {0,3}(`{3,}|~{3,})([^`~]*)$/;
  function render(target,source){
    if(typeof source!=='string')source='';
    sources.set(target,source);
    const document=target.ownerDocument||root.document;
    const lines=source.replace(/\r\n?/g,'\n').split('\n'),nodes=[];
    let index=0;
    while(index<lines.length){
      const line=lines[index];
      if(!line.trim()){index++;continue;}
      const opening=line.match(fence);
      if(opening){
        const body=[];index++;
        const closing=new RegExp('^ {0,3}'+opening[1][0]+'{'+opening[1].length+',}\\s*$');
        while(index<lines.length&&!closing.test(lines[index]))body.push(lines[index++]);
        if(index<lines.length)index++;
        const pre=document.createElement('pre'),code=document.createElement('code');
        pre.setAttribute('tabindex','0');pre.setAttribute('aria-label','Bloque de código');
        code.textContent=body.join('\n');pre.append(code);nodes.push(pre);continue;
      }
      const title=line.match(heading);
      if(title||labelHeading.test(line)){
        const node=document.createElement('h'+(title?Math.min(6,title[1].length+1):3));
        inline(node,title?title[2]:line,document);nodes.push(node);index++;continue;
      }
      const item=line.match(bullet);
      if(item){
        const ordered=/^\d/.test(item[1]),list=document.createElement(ordered?'ol':'ul');
        if(ordered&&parseInt(item[1],10)!==1)list.setAttribute('start',String(parseInt(item[1],10)));
        while(index<lines.length){
          const current=lines[index].match(bullet);
          if(!current||/^\d/.test(current[1])!==ordered)break;
          const entry=document.createElement('li');inline(entry,current[2],document);list.append(entry);index++;
        }
        nodes.push(list);continue;
      }
      const paragraph=[];
      while(index<lines.length&&lines[index].trim()&&!heading.test(lines[index])&&!labelHeading.test(lines[index])&&!bullet.test(lines[index])&&!fence.test(lines[index]))paragraph.push(lines[index++]);
      const node=document.createElement('p');inline(node,paragraph.join('\n'),document);nodes.push(node);
    }
    target.replaceChildren(...nodes);
    return target;
  }
  const api={render,source:target=>sources.get(target)??''};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.HermesChatRenderer=api;
})(typeof globalThis==='undefined'?this:globalThis);
