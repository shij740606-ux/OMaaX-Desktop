import assert from 'node:assert/strict';
import { test } from 'node:test';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

test('community catalog is generated without downloading community resources', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'biodsh-catalog-test-'));
  for (const dir of ['desktop/scripts', 'desktop/store', 'desktop/resources/skills', 'biodsh-core/skills']) {
    mkdirSync(path.join(root, dir), { recursive: true });
  }
  const script = path.join(root, 'desktop/scripts/sync-skills.mjs');
  copyFileSync(new URL('./sync-skills.mjs', import.meta.url), script);
  writeFileSync(path.join(root, 'desktop/store/community-index.json'), JSON.stringify({ skills: [
    { id: 'example', name: 'Example', description: 'Test fixture', origin: { repo: 'example' } },
    { id: 'population-example', name: 'Population example', category: 'genomics', has_scripts: true, modelInvocable: true,
      origin: { repo: 'Example', url: 'https://github.com/example/tools', path: 'skills/population', license: 'MIT', revision:'a'.repeat(40), manifest:{files:[{path:'SKILL.md',sha:'b'.repeat(40)}],licenses:[]} } },
    {id:'plant-guide',name:'Plant guide',has_scripts:false,origin:{repo:'Example',url:'https://github.com/example/plant',path:'README.md',license:'MIT'},
      sourceGuide:{revision:'a'.repeat(40),files:['README.md'],licenseFiles:['LICENSE'],instructions:'Use only this tool',requirements:'R'}},
  ] }));
  writeFileSync(path.join(root, 'desktop/store/community-zh.json'), JSON.stringify({ items: {
    'population-example': { title_zh: '群体结构分析', summary_zh: '比较样本间的遗传差异。', domain: '群体遗传', subcategory: '群体结构与祖源' },
    'plant-guide':{title_zh:'植物性状定位',domain:'遗传育种与性状定位',subcategory:'关联分析与性状定位'},
  } }));
  const catalog = path.join(root, 'desktop/resources/skills/catalog.json');
  writeFileSync(catalog, 'existing catalog');
  const complete = spawnSync(process.execPath, [script], { encoding: 'utf8' });
  assert.equal(complete.status, 0, complete.stderr);
  const result = JSON.parse(readFileSync(catalog, 'utf8'));
  assert.equal(result.meta.community, 3);
  assert.equal(result.skills[0].id, 'example');
  assert.equal(result.skills[0].tier, 'community');
  const population = result.skills.find(s => s.id === 'population-example');
  assert.equal(population.domain_zh, '群体遗传');
  assert.equal(population.subcategory, '群体结构与祖源');
  assert.equal(population.name, '群体结构分析');
  assert.equal(population.origin.path, 'skills/population');
  assert.equal(population.origin.license, 'MIT');
  assert.equal(population.origin.revision, 'a'.repeat(40));
  assert.deepEqual(population.origin.manifest.files,[{path:'SKILL.md',sha:'b'.repeat(40)}]);
  assert.equal(population.has_scripts, true);
  assert.equal(population.modelInvocable, true);
  assert.equal(population.bundle, 'community-skills');
  const guide=result.skills.find(s=>s.id==='plant-guide');
  assert.equal(guide.domain_zh,'遗传育种与性状定位');assert.equal(guide.has_scripts,false);
  assert.equal(guide.sourceGuide.revision,'a'.repeat(40));
  assert.deepEqual(guide.sourceGuide.files,['README.md']);assert.equal(guide.sourceGuide.requirements,'R');
});
