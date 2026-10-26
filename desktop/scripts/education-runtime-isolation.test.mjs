import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {runtimeEnvironment}=require('../resources/ssh/education-local.cjs');

test('education preparation excludes developer Python, mirrors, proxies and provider settings',()=>{
  const inherited={PATH:'system-path',SYSTEMROOT:'C:/Windows',UV_PYTHON:'C:/developer/python.exe',UV_PYTHON_PREFERENCE:'only-system',UV_CACHE_DIR:'C:/shared-cache',UV_PYTHON_INSTALL_DIR:'C:/shared-python',UV_PROJECT_ENVIRONMENT:'C:/other',UV_OFFLINE:'true',UV_INDEX_URL:'https://private.invalid',PIP_CONFIG_FILE:'C:/private.ini',PIP_INDEX_URL:'https://private.invalid',PYTHONPATH:'C:/developer/libs',PYTHONHOME:'C:/developer',VIRTUAL_ENV:'C:/developer/env',HTTP_PROXY:'http://proxy.invalid',https_proxy:'http://proxy.invalid',ALL_PROXY:'socks5://proxy.invalid',NODE_OPTIONS:'--require C:/developer.js',REVIEW_LLM_API_KEY:'private',DOCGEN_LLM_API_KEY:'private'};
  const original={...inherited};
  const state=path.resolve('fresh-education');
  const env=runtimeEnvironment(state,inherited);
  assert.deepEqual(inherited,original,'The caller environment must remain unchanged');
  for(const key of ['UV_PYTHON','UV_PYTHON_PREFERENCE','UV_PROJECT_ENVIRONMENT','UV_OFFLINE','UV_INDEX_URL','PIP_CONFIG_FILE','PIP_INDEX_URL','PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','HTTP_PROXY','https_proxy','ALL_PROXY','NODE_OPTIONS','REVIEW_LLM_API_KEY','DOCGEN_LLM_API_KEY'])assert.ok(!(key in env),key);
  assert.equal(env.UV_CACHE_DIR,path.join(state,'uv-cache'));
  assert.equal(env.UV_PYTHON_INSTALL_DIR,path.join(state,'python'));
  assert.equal(env.UV_LINK_MODE,'copy');
  assert.equal(env.PYTHONNOUSERSITE,'1');
  assert.equal(env.SYSTEMROOT,inherited.SYSTEMROOT);
});
