// Packaging gate for the upgraded runtime. This only reads staged resources.
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {verifyDshStartup} from './patch-dsh-startup.mjs';
import {patchAuthErrorSource} from './patch-dsh-auth-error.mjs';
import {patchCookieCleanupSource} from './patch-dsh-cookies.mjs';
import {patchMcpTimeoutSource,verifyMcpTimeoutSdk} from './patch-dsh-mcp-timeout.mjs';
import {patchCompanyPiAdapterSource,patchCompanyRetrySource,patchCompanyChatSource,patchCompanyTrajectorySource} from './patch-dsh-company-errors.mjs';

const desktop=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
export function verifyRuntime(runtime=path.join(desktop,'dsh-runtime')) {
  const expected=JSON.parse(readFileSync(path.join(desktop,'package.json'))).devDependencies['@deepseek-ai/dsh'];
  const modules=path.join(runtime,'node_modules');
  const require=createRequire(path.join(modules,'@deepseek-ai/dsh/package.json'));
  for(const name of ['dsh','dsh-agent-loop','dsh-web-app','dsh-llm-pi-ai','dsh-client-connection']) {
    assert.equal(JSON.parse(readFileSync(path.join(modules,'@deepseek-ai',name,'package.json'))).version,expected,`Mismatched runtime: ${name}`);
  }
  const file=name=>require.resolve(`@deepseek-ai/${name}`);
  const source=name=>readFileSync(file(name),'utf8');
  const assertPatched=(name,patch)=>{const text=source(name);assert.equal(patch(text),text,`Missing or outdated patch: ${name}`);};
  verifyDshStartup(runtime);
  assertPatched('dsh-client-connection',patchAuthErrorSource);
  assertPatched('dsh-client-connection',patchCookieCleanupSource);
  assertPatched('dsh-mcp-client',patchMcpTimeoutSource);
  verifyMcpTimeoutSdk(file('dsh-mcp-client'));
  assertPatched('dsh-llm-pi-ai',patchCompanyPiAdapterSource);
  assertPatched('dsh-llm-retry',patchCompanyRetrySource);
  const client=name=>readFileSync(path.join(path.dirname(file(name)),'client.js'),'utf8');
  for(const [name,patch] of [['dsh-client-ui-chat',patchCompanyChatSource],['dsh-client-ui-trajectory',patchCompanyTrajectorySource]]) {
    const text=client(name);assert.equal(patch(text),text,`Missing company error UI: ${name}`);
  }
  const agent=source('dsh-agent-loop');
  assert.match(agent,/OMAAX_AUTO_CONTINUE_V1/);
  assert.match(agent,/kind: ["']omaax-output-continuation["']/);
  assert.doesNotMatch(agent,/BIODSH_STEP_GUARD/);
  const compaction=source('dsh-compaction-basic');
  for(const marker of ['OMAAX_COMPACTION_CONTINUE_V1','OMAAX_COMPACTION_TRANSPORT_V1'])assert.ok(compaction.includes(marker),marker);
  assert.ok(client('dsh-client-ui-chat').includes('OMAAX_APPROVAL_DETAILS_V1'));
  assert.ok(client('dsh-client-ui-approval').includes('OMAAX_APPROVAL_REASON_V1'));
  for(const preset of ['standard-preset.patch.yml','legacy-preset.patch.yml'])assert.ok(existsSync(path.join(modules,'@biodsh',preset)),`Missing historical preset: ${preset}`);
  for(const name of ['auth-guard','workflow-guard','skill-router','step-guard']) {
    for(const part of ['index.js','package.json']) {
      const relative=path.join(name,part);
      assert.deepEqual(readFileSync(path.join(modules,'@biodsh',relative)),readFileSync(path.join(desktop,'dsh-plugins',relative)),`Stale OMaaX plugin: ${relative}`);
    }
  }
  return {runtime:expected,startup:true,authentication:true,cookieCleanup:true,continuation:true,compaction:true,companyErrors:true,mcpTimeout:true,approvalDetails:true,legacyPresets:true,plugins:true};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))console.log(JSON.stringify(verifyRuntime(process.argv[2])));
