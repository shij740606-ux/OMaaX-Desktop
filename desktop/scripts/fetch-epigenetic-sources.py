"""Pin official, licensed workflow source snapshots; never download sequencing data."""
import hashlib,io,json,tarfile,urllib.request
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'desktop/workflow-extensions/upstream_sources';OUT.mkdir(parents=True,exist_ok=True)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
headers={'User-Agent':'OMaaX-public-workflow-source-import'}
pins={'atacseq':'2.1.2','chipseq':'2.1.0','cutandrun':'3.2.2','methylseq':'4.2.0'}
records=[]
for name,tag in pins.items():
 target=OUT/('nf-core_'+name+'_'+tag)
 if target.exists():raise ValueError('Source snapshot already exists: '+str(target))
 api='https://api.github.com/repos/nf-core/'+name+'/commits/'+tag
 with opener.open(urllib.request.Request(api,headers=headers),timeout=45) as response:commit=json.load(response)['sha']
 url='https://codeload.github.com/nf-core/'+name+'/tar.gz/'+commit
 with opener.open(urllib.request.Request(url,headers=headers),timeout=90) as response:data=response.read(32*1048576+1)
 if len(data)>32*1048576:raise ValueError('Source archive exceeds expected size')
 target.mkdir();files={}
 with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
  for member in archive.getmembers():
   parts=PurePosixPath(member.name).parts[1:]
   if not member.isfile() or not parts or '..' in parts:continue
   if parts[0] in ('.git','.github','.devcontainer','docs','tests','testdata','images'):continue
   if member.size>8*1048576:raise ValueError('Unexpected large source file: '+member.name)
   rel=Path(*parts);raw=archive.extractfile(member).read();path=target/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
   files[rel.as_posix()]=hashlib.sha256(raw).hexdigest()
 assert (target/'main.nf').is_file() and (target/'LICENSE').is_file() and (target/'nextflow_schema.json').is_file()
 records.append({'repo':'nf-core/'+name,'tag':tag,'commit':commit,'url':url,'archiveSha256':hashlib.sha256(data).hexdigest(),'fileCount':len(files),'files':files,'directory':target.name,'license':'MIT'})
 print(json.dumps({k:v for k,v in records[-1].items() if k!='files'}))
(OUT.parent/'upstream-manifest.json').write_text(json.dumps(records,indent=2))
