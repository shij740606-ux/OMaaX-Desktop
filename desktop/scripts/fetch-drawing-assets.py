"""Refresh the curated CC0 collection from a pinned Bioicons tree. Never run at app startup."""
import hashlib, json, pathlib, re, urllib.request, xml.etree.ElementTree as ET
ROOT = pathlib.Path(__file__).resolve().parents[1]
REVISION = 'd29e766ea7580b8063c4f47b29e872db40a4d979'
tree = json.loads(urllib.request.urlopen('https://api.github.com/repos/duerrsimon/bioicons/git/trees/'+REVISION+'?recursive=1',timeout=40).read())
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
allowed = set('svg g path rect circle ellipse line polyline polygon defs linearGradient radialGradient stop clipPath mask title desc'.split())
ET.register_namespace('', 'http://www.w3.org/2000/svg')
items=[]
for entry in tree['tree']:
 path=entry['path']; name=path.rsplit('/',1)[-1]
 if not path.startswith('static/icons/cc-0/') or name not in choices: continue
 ident,label,category=choices.pop(name)
 url='https://raw.githubusercontent.com/duerrsimon/bioicons/'+tree['sha']+'/'+urllib.parse.quote(path)
 raw=urllib.request.urlopen(url,timeout=40).read()
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
   if key.startswith('{') or local.lower().startswith('on') or local in ('href','class') or re.search(r'(https?:|javascript:|data:|@import|expression\s*\()',val,re.I): del el.attrib[key]
 clean(root)
 if 'viewBox' not in root.attrib:
  root.set('viewBox','0 0 '+re.sub('[^0-9.]','',root.get('width','100'))+' '+re.sub('[^0-9.]','',root.get('height','100')))
 root.attrib.pop('width',None);root.attrib.pop('height',None)
 items.append(dict(id=ident,name=label,category=category,keywords=name[:-4].replace('_',' '),author=path.split('/')[-2],license='CC0-1.0',source=url,sha256=hashlib.sha256(raw).hexdigest(),svg=ET.tostring(root,encoding='unicode')))
if choices: raise RuntimeError('Missing curated assets: '+str(choices))
out=ROOT/'src/renderer/src/views/drawing-assets.json'
out.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8')
print('Saved',len(items),'CC0 assets;',out.stat().st_size,'bytes; revision',tree['sha'])
