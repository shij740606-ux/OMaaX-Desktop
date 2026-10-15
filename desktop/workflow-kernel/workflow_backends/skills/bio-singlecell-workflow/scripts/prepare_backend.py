"""Verify and extract the unchanged workflow archive into a new execution directory."""
from pathlib import Path
import argparse,hashlib,json,zipfile
p=argparse.ArgumentParser(__doc__);p.add_argument('--destination',type=Path,required=True);a=p.parse_args()
root=Path(__file__).resolve().parents[1];m=json.loads((root/'assets/backend.json').read_text());z=root/'assets/backend.zip'
assert hashlib.sha256(z.read_bytes()).hexdigest()==m['sha256'],'Archive hash mismatch'
assert not a.destination.exists(),'Destination must not exist; preserve prior runs'
with zipfile.ZipFile(z) as archive:
    for entry in archive.infolist():
        name=Path(entry.filename)
        assert not name.is_absolute() and '..' not in name.parts and '\\' not in entry.filename and ':' not in entry.filename,'Unsafe archive path'
    archive.extractall(a.destination)
print(a.destination.resolve()/m['package'])
