const {test}=require('node:test');const assert=require('node:assert/strict');const {PassThrough}=require('node:stream');
const check=require('./account-policy.cjs');const owner='a'.repeat(64)+':761f059b-1111-4111-8111-123456789abc';
function conn(code,text){return {exec(command,cb){assert.ok(command.includes('base64'));const stream=new PassThrough();stream.stderr=new PassThrough();cb(null,stream);queueMicrotask(()=>{stream.emit('data',Buffer.from(text));stream.emit('close',code);});}};}
test('server binding failure blocks use; exact approved identity is accepted',async()=>{
 await check(conn(0,'OMAAX_ACCOUNT_OK\n'),owner);
 await assert.rejects(check(conn(1,''),owner),/独立账号/);
 await assert.rejects(check(conn(0,'other'),owner),/独立账号/);
 await assert.rejects(check(conn(0,''),'injected'),/无效/);
 assert.ok(check.source(owner).includes('st_uid==0'));assert.ok(check.source(owner).includes('not s.st_mode&0o022'));
});
