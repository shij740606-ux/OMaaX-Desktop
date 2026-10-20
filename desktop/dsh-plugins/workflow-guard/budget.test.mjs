import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,readFileSync,renameSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createBudgets,replaceBudgetFile} from './index.js';

test('temporary destination locks retry without deleting the old budget; persistent failures stay closed',()=>{
 const dir=mkdtempSync(path.join(os.tmpdir(),'omaax-budget-')),source=path.join(dir,'next'),target=path.join(dir,'budget');
 writeFileSync(source,'new');writeFileSync(target,'old');let tries=0;const waits=[];
 replaceBudgetFile(source,target,(a,b)=>{assert.equal(readFileSync(target,'utf8'),'old');if(tries++<2)throw Object.assign(Error('locked'),{code:'EPERM'});renameSync(a,b);},ms=>waits.push(ms));
 assert.equal(readFileSync(target,'utf8'),'new');assert.deepEqual(waits.slice(0,2),[25,50]);
 assert.ok(waits.every((ms,index)=>ms===25*2**index));assert.ok(waits.length<=5);
 writeFileSync(source,'newer');tries=0;
 assert.throws(()=>replaceBudgetFile(source,target,()=>{tries++;throw Object.assign(Error('locked'),{code:'EACCES'});},()=>{}),/locked/);
 assert.equal(tries,6);assert.equal(readFileSync(target,'utf8'),'new');assert.equal(readFileSync(source,'utf8'),'newer');
 tries=0;assert.throws(()=>replaceBudgetFile(source,target,()=>{tries++;throw Object.assign(Error('disk full'),{code:'ENOSPC'});},()=>{}),/disk full/);assert.equal(tries,1);
});
test('waiting and duplicate failure polls never consume extra retry attempts',()=>{
 const b=createBudgets(mkdtempSync(path.join(os.tmpdir(),'omaax-budget-'))),exec={agent:{id:'s'},name:'mcp__biodsh_ssh__task_list',arguments:{serverId:'a'}};
 for(const id of ['job','job2','job3'])b.result({...exec,name:'mcp__biodsh_ssh__workspace_analysis'},{value:{id,state:'queued'}});
 for(let i=0;i<20;i++)b.result(exec,{value:[{id:'job',state:'running'},{id:'other-user',state:'failed'}]});assert.equal(b.read('s').failures,0);
 for(let i=0;i<20;i++)b.result(exec,{value:[{id:'job',state:'failed'}]});assert.equal(b.read('s').failures,1);
 b.result(exec,{value:[{id:'job2',state:'failed'},{id:'job3',state:'failed'}]});assert.equal(b.read('s').failures,3);
});
test('usage is deduplicated and survives restart without pausing at former call or token limits',()=>{
 const dir=mkdtempSync(path.join(os.tmpdir(),'omaax-budget-')),b=createBudgets(dir);
 b.usage('s',1,{inputTokens:100,outputTokens:50,totalTokens:150});b.usage('s',1,{totalTokens:150});assert.equal(createBudgets(dir).read('s').tokens,150);
 for(let i=0;i<100;i++)assert.equal(b.admit('s'),true);
 assert.equal(createBudgets(dir).read('s').calls,100);
 b.usage('t',2,{totalTokens:250000});assert.equal(b.admit('t'),true);
 assert.equal(b.read('t').tokens,250000);
 writeFileSync(path.join(dir,'budget-old.json'),JSON.stringify({calls:80,tokens:200000,paused:true}));
 assert.equal(createBudgets(dir).admit('old'),true);
 assert.equal(b.read('old').paused,false);assert.equal(b.read('old').calls,81);
 writeFileSync(path.join(dir,'budget-s.json'),'corrupt');assert.throws(()=>b.admit('s'));
});
