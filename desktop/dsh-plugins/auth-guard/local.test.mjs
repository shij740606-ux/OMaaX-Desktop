import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,access,rm} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createGuard} from './index.js';
import {createLocalDeleteTool,localExecutionGuard} from './local.js';

test('login permits local analysis and server tools together, but not forged remote deletion approval',()=>{
 const guard=createGuard(()=>({ok:true}));
 for(const name of ['pwsh','bash','write','edit','job_output','job_list','job_kill','todo_write','present','omaax_local_delete','mcp__biodsh_ssh__workspace_analysis']){
  assert.equal(guard({name,arguments:{command:'python analysis.py'}}),undefined);
  assert.match(createGuard(()=>({ok:false}))({name}),/未登录/);
 }
 assert.match(guard({name:'mcp__biodsh_ssh__server_delete_confirm'}),/保护/);
 for(const command of ['rm -rf inputs','Remove-Item data -Recurse','python -c "import os; os.remove(x)"'])assert.match(localExecutionGuard({name:'pwsh',arguments:{command}}),/删除/);
 assert.equal(localExecutionGuard({name:'pwsh',arguments:{command:'conda install pandas'}}),undefined);
});

test('local deletion shows scope and bytes, requires confirmation, and preserves recoverable contents',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'omaax-local-delete-'));
 const file=path.join(root,'result.txt'),exec={agent:{id:'test'},signal:new AbortController().signal};
 try{
  await writeFile(file,'abc');
  await assert.rejects(createLocalDeleteTool({request:async()=> 'rejected'}).execute({path:file,reason:'用户要求清理'},exec),/未确认/);
  assert.equal(await readFile(file,'utf8'),'abc');
  await assert.rejects(createLocalDeleteTool({request:async()=>{await writeFile(file,'changed');return 'allowed-once';}}).execute({path:file,reason:'清理'},exec),/范围已变化/);
  const tool=createLocalDeleteTool({request:async p=>{assert.ok(p.reason.includes(file));assert.match(p.reason,/7 字节/);assert.match(p.reason,/1 个文件/);return 'allowed-once';}});
  const result=await tool.execute({path:file,reason:'清理'},exec);
  assert.equal(await readFile(result.trashPath,'utf8'),'changed');await assert.rejects(access(file));
  await assert.rejects(tool.execute({path:path.parse(root).root,reason:'root'},exec),/根目录/);
 }finally{await rm(root,{recursive:true,force:true});}
});
