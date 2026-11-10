/* Display-only quarantine history never enters the capable agent's context. */
(function(root){
  'use strict';
  function stamp(row){const value=row.timestamp??row.created_at??0;const number=Number(value);return Number.isFinite(number)?number:Date.parse(value)/1000||0;}
  function page(native,events,seen,complete){
    const cutoff=native.length?Math.min(...native.map(stamp)):Infinity;
    const extra=events.filter(row=>!seen.has(row.id)&&(complete||stamp(row)>=cutoff));
    extra.forEach(row=>seen.add(row.id));
    return [...native,...extra].sort((a,b)=>stamp(a)-stamp(b));
  }
  const api={page};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.HermesQuarantineChat=api;
})(typeof globalThis==='undefined'?this:globalThis);
