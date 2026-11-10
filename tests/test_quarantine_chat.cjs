const test=require('node:test');
const assert=require('node:assert/strict');
const {page}=require('../app/static/quarantine-chat.js');
test('display-only reports merge once across reload and native pagination',()=>{
  const reports=[{id:'q1-user',role:'user',timestamp:1},{id:'q1-assistant',role:'assistant',timestamp:1},{id:'q2-user',timestamp:4}];
  const seen=new Set();
  assert.deepEqual(page([{id:'n2',timestamp:3}],reports,seen,false).map(r=>r.id),['n2','q2-user']);
  assert.deepEqual(page([{id:'n1',timestamp:0}],reports,seen,true).map(r=>r.id),['n1','q1-user','q1-assistant']);
  assert.equal(page([],reports,seen,true).length,0);
  assert.equal(page([],reports,new Set(),true).length,3);
});
