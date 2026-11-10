const test=require('node:test'), assert=require('node:assert/strict');
const Hub=require('../app/static/settings-hub.js');
const asset=(id,content,revision='v1')=>({id,name:id,kind:'skill',status:'active',editable:true,content,revision});

test('resource editor preserves dirty draft, binds saves and exposes conflict',async()=>{
  const calls=[]; const state=Hub.createEditor({read:async key=>asset(key.id,'original'),write:async(key,data)=>{calls.push({key,data});throw Object.assign(new Error('Conflict'),{status:409});}});
  await state.load({id:'one',kind:'skill',profile:'default'});
  state.edit('updated'); assert.equal(state.get().dirty,true);
  assert.equal(await state.load({id:'two'}),false);
  await state.save(); assert.equal(state.get().phase,'conflict');
  assert.equal(state.get().content,'updated'); assert.equal(calls[0].key.id,'one');
  assert.equal(calls[0].data.revision,'v1');
});

test('out-of-order read cannot replace new selection and save blocks loads',async()=>{
  let finish; const delayed=new Promise(resolve=>finish=resolve);
  const state=Hub.createEditor({read:key=>key.id==='old'?delayed:Promise.resolve(asset(key.id,'new')),write:async()=>asset('new','saved','v2')});
  const old=state.load({id:'old'}); await state.load({id:'new'}); finish(asset('old','stale')); await old;
  assert.equal(state.get().item.id,'new'); assert.equal(state.get().content,'new');
  state.edit('changed'); await state.save(); assert.equal(state.get().dirty,false);
});

test('read-only installed asset cannot save or accept edits; close invalidates in-flight load',async()=>{
  let writes=0, finish;
  const state=Hub.createEditor({read:async()=>({...asset('builtin','x'),editable:false}),write:async()=>{writes++;}});
  await state.load({id:'builtin'}); state.edit('danger'); await state.save(); assert.equal(writes,0); assert.equal(state.get().content,'x');
  const pending=Hub.createEditor({read:()=>new Promise(resolve=>finish=resolve),write:async()=>{}});
  const load=pending.load({id:'old'}); pending.clear(); finish(asset('old','x'));await load;assert.equal(pending.get().item,null);
});

test('shared picker maps to explicit skill scope, never a synthetic native profile',()=>{
  assert.deepEqual(Hub.assetSelection('@shared','skill'),{scope:'shared',kind:'skill'});
  assert.deepEqual(Hub.assetSelection('carpediev','skill'),{profile:'carpediev',kind:'skill'});
  assert.deepEqual(Hub.assetSelection('carpediev','script'),{profile:'carpediev',kind:'script'});
});
