"""Refresh the curated CC0 collection from a pinned Bioicons tree. Never run at app startup."""
import hashlib, json, pathlib, re, urllib.request, urllib.parse, time, argparse, concurrent.futures, xml.etree.ElementTree as ET
ROOT = pathlib.Path(__file__).resolve().parents[1]
REVISION = 'd29e766ea7580b8063c4f47b29e872db40a4d979'
parser=argparse.ArgumentParser();parser.add_argument('--tree');args=parser.parse_args()
tree = json.loads(pathlib.Path(args.tree).read_text(encoding='utf8')) if args.tree else json.loads(urllib.request.urlopen('https://api.github.com/repos/duerrsimon/bioicons/git/trees/'+REVISION+'?recursive=1',timeout=40).read())
assert tree['sha']==REVISION and not tree.get('truncated'), 'Wrong or incomplete source revision'
choices = {
 'simple_cell1.svg': ('cell', '细胞', '细胞与组织'),
 'cell_group.svg': ('cell-group', '细胞群', '细胞与组织'),
 'redbloodcell.svg': ('red-cell', '红细胞', '细胞与组织'),
 'organoid.svg': ('organoid', '类器官', '细胞与组织'),
 'mitochondria.svg': ('mitochondrion', '线粒体', '细胞器'),
 'ribosome.svg': ('ribosome', '核糖体', '细胞器'),
 'Endoplasmic_Reticulum.svg': ('er', '内质网', '细胞器'),
 'proteasome.svg': ('proteasome', '蛋白酶体', '蛋白与膜'),
 'receptor.svg': ('receptor', '膜受体', '蛋白与膜'),
 'Phospholipid.svg': ('lipid', '磷脂', '蛋白与膜'),
 'histone_complex.svg': ('histone', '组蛋白复合体', '蛋白与膜'),
 'DNA_double_helix.svg': ('dna', 'DNA 双螺旋', '核酸'),
 'tRNA_secondary_structure.svg': ('trna', 'tRNA', '核酸'),
 'plasmid-3.svg': ('plasmid', '质粒', '核酸'),
 'simple_e_coli.svg': ('bacteria', '大肠杆菌', '细胞与组织'),
 'Pipette.svg': ('pipette', '移液器', '实验器材'),
 'microscope-cartoon.svg': ('microscope', '显微镜', '实验器材'),
 '6_well_plate.svg': ('plate', '六孔板', '实验器材'),
}
for category,names in json.loads((ROOT/'scripts/drawing-assets-extra.json').read_text(encoding='utf8')).items():
 for name,label in names.items():
  assert name not in choices,name
  choices[name]=('bio-'+hashlib.sha256(name.encode()).hexdigest()[:12],label,category)
allowed = set('text tspan svg g path rect circle ellipse line polyline polygon defs linearGradient radialGradient stop clipPath mask title desc'.split())
ET.register_namespace('', 'http://www.w3.org/2000/svg')
viewboxes=json.loads((ROOT/'scripts/drawing-assets-viewboxes.json').read_text(encoding='utf8'))
categories=['植物与藻类','动物与模式生物','细胞与组织','细胞器','器官与生理','微生物与病毒','蛋白与膜','核酸','实验器材','氨基酸']
cache=ROOT.parent/'validation-local/drawing-assets-cache';cache.mkdir(parents=True,exist_ok=True)
entries=[e for e in tree['tree'] if e['path'].startswith('static/icons/cc-0/') and e['path'].rsplit('/',1)[-1] in choices]
assert len(entries)==len(choices), 'Missing or duplicate selected filenames'
def fetch(entry):
 path=entry['path']; name=path.rsplit('/',1)[-1]
 ident,label,category=choices[name]
 url='https://raw.githubusercontent.com/duerrsimon/bioicons/'+tree['sha']+'/'+urllib.parse.quote(path)
 cached=cache/(entry['sha']+'.svg')
 raw=cached.read_bytes() if cached.exists() else None
 if raw is None:
  for attempt in range(3):
   try:
    raw=urllib.request.urlopen(url,timeout=30).read();break
   except Exception:
    if attempt==2:raise
    time.sleep(attempt+1)
 assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==entry['sha'], 'Source hash mismatch: '+name
 cached.write_bytes(raw)
 root=ET.fromstring(raw)
 rules=[]
 for style in root.iter():
  if style.tag.split('}')[-1]=='style':
   for selectors, declarations in re.findall(r'([^{}]+)\{([^{}]+)\}',style.text or ''):
    for selector in selectors.split(','): rules.append((selector.strip(),declarations.strip()))
 def clean(el):
  classes=el.get('class','').split()
  declarations=[value for selector,value in rules if selector in ['.'+c for c in classes] or selector==el.tag.split('}')[-1]]
  if declarations: el.set('style',';'.join(declarations+[el.get('style','')]))
  for child in list(el):
   if child.tag.split('}')[-1] not in allowed: el.remove(child)
   else: clean(child)
  for key,val in list(el.attrib.items()):
   local=key.split('}')[-1]
   if key.startswith('{') or local.lower().startswith('on') or local in ('href','class') or re.search(r'(https?:|javascript:|data:|@import|expression\s*\(|url\(\s*[\"\']?//)',val,re.I): del el.attrib[key]
 clean(root)
 if 'viewBox' not in root.attrib:
  root.set('viewBox','0 0 '+re.sub('[^0-9.]','',root.get('width','100'))+' '+re.sub('[^0-9.]','',root.get('height','100')))
 root.attrib.pop('width',None);root.attrib.pop('height',None)
 if ident in viewboxes:root.set('viewBox',viewboxes[ident])
 return dict(id=ident,name=label,category=category,keywords=name[:-4].replace('_',' ')+' '+label+' '+category,author=path.split('/')[-2],license='CC0-1.0',source=url,sha256=hashlib.sha256(raw).hexdigest(),svg=ET.tostring(root,encoding='unicode'))
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:items=list(pool.map(fetch,entries))
items.sort(key=lambda a:categories.index(a['category']))
out=ROOT/'src/renderer/src/views/drawing-assets.json'
out.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8')
print('Saved',len(items),'CC0 assets;',out.stat().st_size,'bytes; revision',tree['sha'])
