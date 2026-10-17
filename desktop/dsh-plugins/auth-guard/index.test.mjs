import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createGuard} from './index.js';
test('expired, absent and failing authorization cannot execute',async()=>{
 for(const check of [()=>({ok:false}),()=>null,()=>{throw Error('offline')}])assert.match(await createGuard(check)({name:'mcp__biodsh_ssh__workflow_prepare'}),/账号/);
});
test('autonomous environment repair remains allowed, deletion approvals and bypasses do not',async()=>{
 const guard=createGuard(()=>({ok:true}));
 for(const n of ['workflow_readiness','workflow_prepare','workflow_prepare_status','workflow_environment','server_delete','workflow_result'])assert.equal(await guard({name:'mcp__biodsh_ssh__'+n}),undefined);
 for(const name of ['terminal','read_file','mcp__other__run','mcp__biodsh_ssh__server_delete_confirm','mcp__biodsh_ssh__server_write_confirm'])assert.match(await guard({name}),/保护/);
});
