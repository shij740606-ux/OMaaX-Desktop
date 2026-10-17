import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createBudgets} from './index.js';
test('waiting and duplicate failure polls never consume extra retry attempts',()=>{
 const b=createBudgets(mkdtempSync(path.join(os.tmpdir(),'omaax-budget-'))),exec={agent:{id:'s'},name:'mcp__biodsh_ssh__task_list',arguments:{serverId:'a'}};
 for(const id of ['job','job2','job3'])b.result({...exec,name:'mcp__biodsh_ssh__workspace_analysis'},{value:{id,state:'queued'}});
 for(let i=0;i<20;i++)b.result(exec,{value:[{id:'job',state:'running'},{id:'other-user',state:'failed'}]});assert.equal(b.read('s').failures,0);
 for(let i=0;i<20;i++)b.result(exec,{value:[{id:'job',state:'failed'}]});assert.equal(b.read('s').failures,1);
 b.result(exec,{value:[{id:'job2',state:'failed'},{id:'job3',state:'failed'}]});assert.equal(b.read('s').failures,3);
});
test('usage is deduplicated, survives restart, and call budget works without token accounting',()=>{
 const dir=mkdtempSync(path.join(os.tmpdir(),'omaax-budget-')),b=createBudgets(dir);
 b.usage('s',1,{inputTokens:100,outputTokens:50,totalTokens:150});b.usage('s',1,{totalTokens:150});assert.equal(createBudgets(dir).read('s').tokens,150);
 for(let i=0;i<80;i++)assert.equal(b.admit('s'),true);assert.equal(b.admit('s'),false);
 b.usage('t',2,{totalTokens:200000});assert.equal(b.admit('t'),false);
 writeFileSync(path.join(dir,'budget-s.json'),'corrupt');assert.throws(()=>b.admit('s'));
});
