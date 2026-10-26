// Real, local fixture verification; no model credentials or user files are used.
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const runner = path.join(repo, 'desktop/resources/documents/cli.mjs');
const output = path.join(repo, 'validation-local', `document-smoke-${new Date().toISOString().replace(/[:.]/g, '-')}`);
mkdirSync(output, { recursive: true });
const env = { ...process.env,
  OMAAX_DOCUMENT_HOME: process.env.OMAAX_DOCUMENT_HOME ?? path.join(repo, 'validation-local/documents-runtime-test'),
  OMAAX_DOCUMENT_UV: process.env.OMAAX_DOCUMENT_UV ?? path.join(repo, 'desktop/resources/bin', `${process.platform}-${process.arch}`, process.platform === 'win32' ? 'uv.exe' : 'uv'),
};
function cli(args, expectSuccess = true) {
  const result = spawnSync(process.execPath, [runner, ...args], { cwd: output, env, encoding: 'utf8', timeout: 600_000, windowsHide: true });
  if (result.error) throw result.error;
  if (expectSuccess) assert.equal(result.status, 0, `${args[0]}: ${result.stderr}`);
  else assert.notEqual(result.status, 0);
  return result;
}
function json(result) { return JSON.parse(result.stdout.trim().split(/\r?\n/).at(-1)); }
async function cliAsync(args) {
  return await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [runner, ...args], { cwd: output, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    child.stdout.on('data', data => { stdout += data; });
    child.stderr.on('data', data => { stderr += data; });
    child.once('error', reject);
    child.once('close', status => resolve({ status, stdout, stderr }));
  });
}
const runtime = json(cli(['prepare']));
let recovery;
if (process.argv.includes('--check-recovery')) {
  const validationRoot = path.join(repo, 'validation-local') + path.sep;
  assert(path.resolve(runtime.runtimeHome).startsWith(validationRoot), 'Recovery injection is restricted to the isolated validation-local directory.');
  const venv = path.resolve(path.dirname(runtime.python), '..');
  const site = process.platform === 'win32' ? 'Lib/site-packages' : 'lib/python3.12/site-packages';
  const metadata = path.join(venv, site, 'python_docx-1.2.0.dist-info/METADATA');
  assert.equal(statSync(metadata).nlink, 1, 'Document dependencies must be private copies, not hard links to the wheel cache.');
  const before = readdirSync(runtime.runtimeHome).filter(name => name.includes('.incomplete-')).length;
  writeFileSync(metadata, readFileSync(metadata, 'utf8').replace(/^Version: 1\.2\.0$/m, 'Version: 0.0.0'));
  const parallel = await Promise.all([1, 2].map(() => new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [runner, 'prepare'], { cwd: output, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    child.stdout.on('data', data => { stdout += data; });
    child.stderr.on('data', data => { stderr += data; });
    child.on('error', reject);
    child.on('close', code => code === 0 ? resolve({ stdout, stderr }) : reject(new Error(stderr)));
  })));
  assert.equal(readdirSync(runtime.runtimeHome).filter(name => name.includes('.incomplete-')).length, before + 1);
  for (const result of parallel) assert.equal(json(result).packages['python-docx'], '1.2.0');
  assert.equal(parallel.filter(result => result.stderr.includes('Preparing the dedicated')).length, 1);
  recovery = { concurrentPreparations: 2, rebuilds: 1, incompleteEnvironmentPreserved: true, cacheIsolated: true };
}
const build = path.join(output, 'build_report.py');
writeFileSync(build, `from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt
from docx.oxml.ns import qn
from pptx import Presentation
from pptx.util import Inches as PInches, Pt as PPt
from PIL import Image, ImageDraw
import csv, sys, json

root = Path(sys.argv[1])
image = Image.new('RGB', (900, 450), 'white')
draw = ImageDraw.Draw(image)
for i, value in enumerate([36, 52, 79]):
    draw.rectangle((100+i*230, 390-value*4, 230+i*230, 390), fill=['#2582a5','#29a39c','#6854aa'][i])
draw.line((60,40,60,390,840,390), fill='#34465c', width=3)
image.save(root/'synthetic-results.png')
with (root/'results.csv').open('w', newline='', encoding='utf-8') as f:
    writer=csv.writer(f); writer.writerow(['样本','数值']); writer.writerows([['A',36],['B',52],['C',79]])
doc=Document()
for name in ['Normal','Title','Heading 1','Heading 2']:
    style=doc.styles[name]; style.font.name='Microsoft YaHei'
    style.element.rPr.rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
doc.styles['Normal'].font.size=Pt(10)
doc.add_heading('研究结果汇总 / Research results', 0)
doc.add_paragraph('这是一份合成测试数据报告，用于验证中文、图片和表格导出，不代表真实研究结论。')
table=doc.add_table(rows=1, cols=2); table.style='Light Shading Accent 1'
table.rows[0].cells[0].text='样本'; table.rows[0].cells[1].text='数值'
for name,value in [('A',36),('B',52),('C',79)]:
    cells=table.add_row().cells; cells[0].text=name; cells[1].text=str(value)
doc.add_picture(str(root/'synthetic-results.png'), width=Inches(5.7))
doc.add_paragraph('图 1. 合成样本数据。来源：项目内 results.csv。')
for p in list(doc.paragraphs)+[p for row in table.rows for cell in row.cells for p in cell.paragraphs]:
    for r in p.runs:
        r.font.name='Microsoft YaHei'; r._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
doc.save(root/'report-v1.docx')
revised=Document(root/'report-v1.docx')
revised.add_paragraph('修订说明：已按后续对话要求补充局限性和数据来源。')
revised.save(root/'report-v2.docx')
prs=Presentation(); prs.slide_width=PInches(13.333); prs.slide_height=PInches(7.5)
slide=prs.slides.add_slide(prs.slide_layouts[6])
def text(slide, text, x,y,w,h,size):
    box=slide.shapes.add_textbox(PInches(x),PInches(y),PInches(w),PInches(h))
    p=box.text_frame.paragraphs[0]; p.text=text
    for r in p.runs: r.font.name='Microsoft YaHei'; r.font.size=PPt(size)
    return box
text(slide,'研究结果汇报 / Research results',.6,.4,12,1,28)
text(slide,'合成测试：中文、图片、表格均保持可编辑。',.6,1.4,12,.6,20)
slide.shapes.add_picture(str(root/'synthetic-results.png'),PInches(.7),PInches(2.3),width=PInches(7.4))
table=slide.shapes.add_table(4,2,PInches(8.7),PInches(2.5),PInches(3.7),PInches(2.8)).table
for row,values in enumerate([['样本','数值'],['A','36'],['B','52'],['C','79']]):
    for col,value in enumerate(values):
        cell=table.cell(row,col); cell.text=value
        for p in cell.text_frame.paragraphs:
            for r in p.runs:r.font.name='Microsoft YaHei';r.font.size=PPt(18)
prs.save(root/'presentation-v1.pptx')
revised=Presentation(root/'presentation-v1.pptx')
slide=revised.slides.add_slide(revised.slide_layouts[6])
text(slide,'后续对话修订 / Revision',.6,.6,12,1,30)
text(slide,'已增加局限性：合成数据仅用于导出验收。',.8,2,11,1.3,24)
text(slide,'数据来源：项目内 results.csv',.8,4,11,1,22)
revised.save(root/'presentation-v2.pptx')
print(json.dumps({'wordRevisions':2,'pptRevisions':2,'fixture':'synthetic'}))
`, 'utf8');
cli(['run', build, output]);
const originalHashes = Object.fromEntries(['report-v1.docx', 'presentation-v1.pptx'].map(file => [file, createHash('sha256').update(readFileSync(path.join(output, file))).digest('hex')]));
const wordPdf = json(cli(['pdf', '--input', path.join(output, 'report-v2.docx'), '--output', path.join(output, 'report-v2.pdf')]));
const pptPdf = json(cli(['pdf', '--input', path.join(output, 'presentation-v2.pptx'), '--output', path.join(output, 'presentation-v2.pdf')]));
const wordPreview = json(cli(['preview', '--input', path.join(output, 'report-v2.docx'), '--output-dir', path.join(output, 'word-preview')]));
const pptPreview = json(cli(['preview', '--input', path.join(output, 'presentation-v2.pptx'), '--output-dir', path.join(output, 'ppt-preview')]));
assert.equal(pptPreview.pageCount, 2);
assert.equal(pptPreview.images.length, 2);
assert(wordPreview.images.length >= 1);
const concurrentPdf = path.join(output, 'concurrent-report.pdf');
const pdfContenders = await Promise.all([1, 2].map(version => cliAsync(['pdf', '--input', path.join(output, `report-v${version}.docx`), '--output', concurrentPdf])));
assert.equal(pdfContenders.filter(result => result.status === 0).length, 1, JSON.stringify(pdfContenders));
const pdfWinner = pdfContenders.findIndex(result => result.status === 0) + 1;
assert.equal(statSync(concurrentPdf).size, json(pdfContenders[pdfWinner - 1]).bytes);
const concurrentPreview = path.join(output, 'concurrent-preview');
const previewContenders = await Promise.all([1, 2].map(version => cliAsync(['preview', '--input', path.join(output, `presentation-v${version}.pptx`), '--output-dir', concurrentPreview])));
assert.equal(previewContenders.filter(result => result.status === 0).length, 1, JSON.stringify(previewContenders));
const previewWinner = previewContenders.findIndex(result => result.status === 0) + 1;
const manifest = JSON.parse(readFileSync(path.join(concurrentPreview, 'manifest.json'), 'utf8'));
assert.equal(manifest.pageCount, previewWinner);
for (const image of manifest.images) {
  assert.equal(path.dirname(image.path), concurrentPreview);
  assert(statSync(image.path).size > 100);
}
const concurrentPublishing = { pdfWinners: 1, pdfWinnerRevision: pdfWinner, previewWinners: 1, previewWinnerRevision: previewWinner, imagesPublishedToProject: true };
for (const file of Object.keys(originalHashes)) assert.equal(createHash('sha256').update(readFileSync(path.join(output, file))).digest('hex'), originalHashes[file]);
const preservedPdf = createHash('sha256').update(readFileSync(wordPdf.output)).digest('hex');
cli(['pdf', '--input', path.join(output, 'report-v1.docx'), '--output', wordPdf.output], false);
assert.equal(createHash('sha256').update(readFileSync(wordPdf.output)).digest('hex'), preservedPdf);
const verify = path.join(output, 'verify_documents.py');
writeFileSync(verify, `import json,sys
from pathlib import Path
from pypdf import PdfReader
from docx import Document
from pptx import Presentation
root=Path(sys.argv[1])
word=PdfReader(root/'report-v2.pdf'); deck=PdfReader(root/'presentation-v2.pdf')
word_text='\\n'.join(p.extract_text() for p in word.pages)
deck_text='\\n'.join(p.extract_text() for p in deck.pages)
assert '研究结果汇总' in word_text,repr(word_text)
assert '修订说明' in word_text,repr(word_text)
assert '后续对话修订' in deck_text,repr(deck_text)
assert len(deck.pages)==2
assert len(Document(root/'report-v2.docx').tables)==1
assert len(Document(root/'report-v2.docx').inline_shapes)==1
assert len(Presentation(root/'presentation-v2.pptx').slides)==2
concurrent=PdfReader(root/'concurrent-report.pdf')
concurrent_text='\\n'.join(p.extract_text() for p in concurrent.pages)
assert len(concurrent.pages)==1
assert ('修订说明' in concurrent_text)==(sys.argv[2]=='2')
(root/'verification.json').write_text(json.dumps({'wordPages':len(word.pages),'pptPages':len(deck.pages),'chineseText':True,'editableTables':True,'embeddedImages':True,'revisions':True},ensure_ascii=False,indent=2),encoding='utf-8')
`, 'utf8');
cli(['run', verify, output, String(pdfWinner)]);
const report = { output, runtime, recovery, concurrentPublishing, wordPdf, pptPdf, wordPreview, pptPreview, originalHashes, verification: JSON.parse(readFileSync(path.join(output, 'verification.json'), 'utf8')) };
writeFileSync(path.join(output, 'result.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify({ passed: true, output, ...report.verification }));
