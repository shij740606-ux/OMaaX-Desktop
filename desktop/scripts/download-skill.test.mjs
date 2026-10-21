import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { downloadSkill, safeParts, sourceFetch } from '../resources/scripts/download-skill.mjs';
import {PassThrough} from 'node:stream';
import {EventEmitter} from 'node:events';
import {createHash} from 'node:crypto';

test('temporary source failures retry before using the API fallback', async () => {
 const dir=await mkdtemp(path.join(tmpdir(),'omaax-retry-'));let attempts=0;
 const data='# Example',sha=createHash('sha1').update(`blob ${Buffer.byteLength(data)}\0`).update(data).digest('hex');
 try {
  await downloadSkill({origin:{url:'https://github.com/example/tools',path:'skills/selected',revision:'a'.repeat(40),manifest:{files:[{path:'SKILL.md',sha}],licenses:[]}}},dir,async url=>{
   assert.ok(!url.includes('api.github.com'));if(++attempts===1)throw Error('temporary disconnect');return new Response(data);
  });
  assert.equal(attempts,2);
 } finally {await rm(dir,{recursive:true,force:true});}
});

test('interrupted response bodies restart the file without writing partial bytes',async()=>{
 const dir=await mkdtemp(path.join(tmpdir(),'omaax-stream-retry-'));let calls=0;
 const body='# Complete',sha=createHash('sha1').update(`blob ${Buffer.byteLength(body)}\0`).update(body).digest('hex');
 try{
  await downloadSkill({origin:{url:'https://github.com/example/tools',path:'skills/selected',revision:'a'.repeat(40),manifest:{files:[{path:'SKILL.md',sha}],licenses:[]}}},dir,async()=>{
   if(++calls===1)return new Response(new ReadableStream({start(c){c.enqueue(Buffer.from('# Partial'));c.error(Error('aborted'));}}));
   return new Response(body);
  });
  assert.equal(calls,2);assert.equal(await readFile(path.join(dir,'SKILL.md'),'utf8'),body);
 }finally{await rm(dir,{recursive:true,force:true});}
});

test('pinned manifest downloads without GitHub API even when API is rate limited', async () => {
  const dir=await mkdtemp(path.join(tmpdir(),'omaax-manifest-'));
  const blob=s=>createHash('sha1').update(`blob ${Buffer.byteLength(s)}\0`).update(s).digest('hex');
  const manifest={files:[{path:'SKILL.md',sha:blob('# Example')}],licenses:[{path:'LICENSE',sha:blob('MIT')}]};
  const urls=[];
  try {
    await downloadSkill({origin:{url:'https://github.com/example/tools',path:'skills/selected',revision:'a'.repeat(40),manifest}},dir,async url=>{
      urls.push(url);
      if(url.startsWith('https://api.github.com')) return new Response('rate limited',{status:403});
      return new Response(url.endsWith('/LICENSE')?'MIT':'# Example');
    });
    assert.equal(urls.length,2);assert.ok(urls.every(u=>u.startsWith('https://raw.githubusercontent.com/')));
    assert.equal(await readFile(path.join(dir,'SKILL.md'),'utf8'),'# Example');
  } finally {await rm(dir,{recursive:true,force:true});}
});

test('manifest rejects corrupt content, traversal and duplicate case-insensitive paths', async () => {
  const origin={url:'https://github.com/example/tools',path:'skills/selected',revision:'a'.repeat(40)};
  const dir=await mkdtemp(path.join(tmpdir(),'omaax-manifest-invalid-'));
  try {
    for(const files of [[{path:'../SKILL.md',sha:'b'.repeat(40)}],[{path:'SKILL.md',sha:'b'.repeat(40)},{path:'skill.md',sha:'b'.repeat(40)}]]) {
      await assert.rejects(downloadSkill({origin:{...origin,manifest:{files,licenses:[]}}},dir,async()=>{throw Error('must not fetch');}),/路径|清单/);
    }
    await assert.rejects(downloadSkill({origin:{...origin,manifest:{files:[{path:'SKILL.md',sha:'b'.repeat(40)}],licenses:[]}}},dir,async()=>new Response('wrong bytes')),/校验失败/);
    assert.deepEqual(await readdir(dir),[]);
  } finally {await rm(dir,{recursive:true,force:true});}
});

const sha = 'a'.repeat(40);
const skill = { origin: { url: 'https://github.com/example/tools', path: 'skills/selected', license: 'MIT' } };
const base = 'https://api.github.com/repos/example/tools';
test('raw-file transport tries multiple addresses, streams bytes and does not follow redirects', async () => {
  for(const statusCode of [200,302]) {
    const response=await sourceFetch('https://raw.githubusercontent.com/example/tools/'+sha+'/README.md',{},(url,options,ready)=>{
      assert.equal(options.autoSelectFamily,true);assert.equal(options.agent,false);
      assert.equal(options.autoSelectFamilyAttemptTimeout,300);
      const incoming=new PassThrough();incoming.statusCode=statusCode;
      ready(incoming);queueMicrotask(()=>incoming.end('source bytes'));
      return new EventEmitter();
    });
    assert.equal(response.status,statusCode);assert.equal(await response.text(),'source bytes');
    assert.equal(response.ok,statusCode===200);
  }
  await assert.rejects(sourceFetch('https://raw.githubusercontent.com/example/tools/'+sha+'/README.md',{},()=>{
    const request=new EventEmitter();queueMicrotask(()=>request.emit('error',new Error('offline')));return request;
  }),/offline/);
});
function mock(extraRows = []) {
  const urls = [];
  return { urls, fetch: async url => {
    urls.push(url);
    const bodies = {
      [`${base}/commits/HEAD`]: { sha },
      [`${base}/contents/skills/selected?ref=${sha}`]: [{type:'file',path:'skills/selected/SKILL.md'}, {type:'dir',path:'skills/selected/scripts'}, ...extraRows],
      [`${base}/contents/skills/selected/scripts?ref=${sha}`]: [{type:'file',path:'skills/selected/scripts/run.py'}],
      [`${base}/contents?ref=${sha}`]: [{type:'file',name:'LICENSE',path:'LICENSE'}, {type:'dir',name:'other-tools',path:'other-tools'}],
      [`https://raw.githubusercontent.com/example/tools/${sha}/skills/selected/SKILL.md`]: '# Selected tool',
      [`https://raw.githubusercontent.com/example/tools/${sha}/skills/selected/scripts/run.py`]: 'print("selected")',
      [`https://raw.githubusercontent.com/example/tools/${sha}/LICENSE`]: 'MIT License',
    };
    assert.ok(url in bodies, `Unexpected download: ${url}`);
    return new Response(typeof bodies[url] === 'string' ? bodies[url] : JSON.stringify(bodies[url]));
  }};
}
test('downloads only the chosen tool and preserves licensing at one commit', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'omaax-download-test-'));
  try {
    const m = mock(); const result = await downloadSkill(skill, dir, m.fetch);
    assert.equal(result.files, 3);
    assert.equal(await readFile(path.join(dir, '.omaax-source/LICENSE'), 'utf8'), 'MIT License');
    assert.equal(JSON.parse(await readFile(path.join(dir, '.omaax-source.json'), 'utf8')).commit, sha);
    assert.ok(!m.urls.some(url => url.includes('other-tools')));
    assert.deepEqual((await readdir(dir)).sort(), ['.omaax-source','.omaax-source.json','SKILL.md','scripts']);
  } finally { await rm(dir, {recursive:true,force:true}); }
});
test('rejects traversal, Windows device names and untrusted origin URLs', async () => {
  for (const value of ['../escape','a/../../escape','a\\b','CON','C:temp','a/.','a/aux.txt']) assert.throws(() => safeParts(value));
  await assert.rejects(downloadSkill({origin:{url:'https://example.com/repo',path:'x'}}, 'unused'), /不支持/);
});

test('enables model use only for opted-in catalog entries and keeps original frontmatter', async () => {
  const original='---\nname: selected\ndescription: Example\ndisable-model-invocation: true\n---\nBody\ndisable-model-invocation: true\n';
  for (const enabled of [false,true]) {
    const dir=await mkdtemp(path.join(tmpdir(),'omaax-invocation-test-'));
    const m=mock();
    try {
      await downloadSkill({...skill,modelInvocable:enabled},dir,async url=>
        url.endsWith('/skills/selected/SKILL.md') ? new Response(original) : m.fetch(url));
      const installed=await readFile(path.join(dir,'SKILL.md'),'utf8');
      assert.equal(installed,enabled ? original.replace('disable-model-invocation: true','disable-model-invocation: false') : original);
      const provenance=JSON.parse(await readFile(path.join(dir,'.omaax-source.json'),'utf8'));
      assert.equal(provenance.modelInvocationAdapted,enabled);
      if(enabled)assert.equal(await readFile(path.join(dir,'.omaax-source/SKILL.original.md'),'utf8'),original);
    } finally {await rm(dir,{recursive:true,force:true});}
  }
});
test('rejects links and files outside the selected directory', async () => {
  for (const row of [{type:'file',path:'skills/other/secret'}, {type:'symlink',path:'skills/selected/link'}]) {
    const dir=await mkdtemp(path.join(tmpdir(),'omaax-download-test-'));
    try { await assert.rejects(downloadSkill(skill,dir,mock([row]).fetch)); }
    finally { await rm(dir,{recursive:true,force:true}); }
  }
});
test('reports rate limiting without claiming success', async () => {
  await assert.rejects(downloadSkill(skill,'unused',async()=>new Response('',{status:429})), /限流/);
});
test('uses pinned API content when raw-file host is unreachable', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'omaax-download-test-'));
  const m = mock();
  try {
    await downloadSkill(skill, dir, async url => {
      if (url.includes('raw.githubusercontent.com')) throw new Error('offline');
      const files = {'skills/selected/SKILL.md':'# Selected tool','skills/selected/scripts/run.py':'print(1)','LICENSE':'MIT License'};
      const remote = url.startsWith(`${base}/contents/`) ? url.slice(`${base}/contents/`.length).split('?')[0] : '';
      if (remote in files) return new Response(JSON.stringify({type:'file',path:remote,encoding:'base64',content:Buffer.from(files[remote]).toString('base64')}));
      return m.fetch(url);
    });
    assert.equal(await readFile(path.join(dir, 'SKILL.md'), 'utf8'), '# Selected tool');
  } finally { await rm(dir, {recursive:true,force:true}); }
});

test('builds a model-callable source guide from only pinned documents and licensing', async () => {
  const dir=await mkdtemp(path.join(tmpdir(),'omaax-guide-test-'));
  const urls=[];
  const guide={...skill,id:'plant-guide',name:'Plant guide',description:'Analyze selected plant data',
    sourceGuide:{revision:sha,files:['README.md'],licenseFiles:['LICENSE'],instructions:'Use the selected runtime only.',requirements:'R'}};
  try {
    const result=await downloadSkill(guide,dir,async url=>{
      urls.push(url);
      if(url===`https://raw.githubusercontent.com/example/tools/${sha}/README.md`)return new Response('# Upstream usage');
      if(url===`https://raw.githubusercontent.com/example/tools/${sha}/LICENSE`)return new Response('MIT License');
      throw Error('Unexpected request: '+url);
    });
    assert.equal(result.files,2);
    assert.equal(await readFile(path.join(dir,'references/README.md'),'utf8'),'# Upstream usage');
    assert.equal(await readFile(path.join(dir,'.omaax-source/LICENSE'),'utf8'),'MIT License');
    const md=await readFile(path.join(dir,'SKILL.md'),'utf8');
    assert.ok(md.includes('name: "plant-guide"'));
    assert.ok(md.includes('disable-model-invocation: false'));
    assert.ok(md.includes('Use the selected runtime only.'));
    const provenance=JSON.parse(await readFile(path.join(dir,'.omaax-source.json'),'utf8'));
    assert.equal(provenance.kind,'source-guide');assert.equal(provenance.commit,sha);
    assert.deepEqual(provenance.sourceFiles,['README.md']);
    assert.ok(urls.every(url=>url.includes('/'+sha+'/')));
    assert.ok(!urls.some(url=>url.includes('api.github.com')||url.includes('archive')));
  } finally {await rm(dir,{recursive:true,force:true});}
});

test('rejects invalid source-guide references before making network requests', async () => {
  const guide={revision:sha,files:['README.md'],licenseFiles:['LICENSE'],instructions:'Use this guide'};
  for(const sourceGuide of [
    {...guide,files:['../README.md']},{...guide,files:['run.py']},{...guide,revision:'HEAD'},
    {...guide,files:Array(17).fill('README.md')},{...guide,licenseFiles:['../LICENSE']},
    {...guide,instructions:''},{...guide,licenseFiles:['README.md']},
  ]) await assert.rejects(downloadSkill({...skill,id:'plant-guide',sourceGuide},'unused',()=>{throw Error('Network must not run')}),/无效|不安全|只允许/);
});

test('verified revision avoids HEAD drift and preserves exact source provenance', async()=>{
 const dir=await mkdtemp(path.join(tmpdir(),'omaax-pin-test-'));const m=mock();
 try{await downloadSkill({...skill,origin:{...skill.origin,revision:sha}},dir,m.fetch);
 assert.ok(!m.urls.some(u=>u.includes('/commits/HEAD')));
 assert.equal(JSON.parse(await readFile(path.join(dir,'.omaax-source.json'),'utf8')).commit,sha);
 }finally{await rm(dir,{recursive:true,force:true});}
});
test('known unavailable tools fail before any download or writes',async()=>{
 await assert.rejects(downloadSkill({...skill,unavailableReason:'上游已移除'},'unused',()=>{throw Error('must not fetch');}),/上游已移除/);
});
