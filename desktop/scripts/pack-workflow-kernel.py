"""Package the editable source tree without external archive inputs."""
import hashlib, json, zipfile
from pathlib import Path

desktop = Path(__file__).resolve().parents[1]
source = desktop / 'workflow-kernel'
out = desktop / 'resources/workflows'
catalog_path = out / 'catalog.json'
catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
hashes = {}
paths = []
for path in sorted(source.rglob('*')):
    if not path.is_file() or path.is_symlink(): continue
    relative = path.relative_to(source)
    if any(p in {'.git','__pycache__','node_modules','.venv'} for p in relative.parts): continue
    name = relative.as_posix()
    if path.suffix == '.pyc' or name in {'omaax/catalog.json', 'omaax/source-hashes.json'}: continue
    hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    paths.append((name, path))
with zipfile.ZipFile(out / 'kernel.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for name, path in paths: z.write(path, name)
    z.writestr('omaax/catalog.json', json.dumps(catalog, ensure_ascii=False))
    z.writestr('omaax/source-hashes.json', json.dumps(hashes))
digest = hashlib.sha256((out / 'kernel.zip').read_bytes()).hexdigest()
catalog['bundleSha256'] = digest
catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
(out / 'bundle-manifest.json').write_text(json.dumps({'sha256': digest, 'fileCount': len(hashes), 'files': hashes}, indent=2)+'\n', encoding='utf-8')
print(json.dumps({'sourceFiles':len(hashes), 'bundleSha256':digest}))
