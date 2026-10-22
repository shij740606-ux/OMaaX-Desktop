"""Fixed execution contracts. Scientific choices belong to the user and source software."""
import base64,gzip,hashlib,json,math,os,re,shutil,stat,subprocess,time,uuid
from pathlib import Path
CATALOG={}
ENGINE_HASH=''
ADAPTER_SOURCE=''
PARAMETERS={'workflow','source','input','steps','prefix','parameters','configs','preferences','context','cpu','memoryMb','timeoutSeconds'}
BLOCKED={'argv','command','script','script_path','scripts_dir','environment','container','containerOptions','custom_config','custom_config_base','custom_config_version','beforeScript','afterScript','nextflow_options','docker_socket'}
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def digest(v):return hashlib.sha256(canonical(v)).hexdigest()
def file_hash(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        while b:=f.read(1048576):h.update(b)
    return h.hexdigest()
def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS workflow_plans(id TEXT PRIMARY KEY,digest TEXT NOT NULL,plan TEXT NOT NULL,confirmed INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL)');db.commit()
def identity(r):return {'uid':os.getuid(),'server':r.SERVER_KEY,'root':str(r.ROOT)}
def no_extra(v,keys):
    if not isinstance(v,dict):raise ValueError('参数须为对象')
    if set(v)-keys:raise ValueError('无法识别参数：'+', '.join(sorted(set(v)-keys))+'；允许参数：'+', '.join(sorted(keys)))
def flow_for(name):
    f=next((f for f in CATALOG['workflows'] if f['id']==name),None)
    if not f:raise ValueError('请选择已收录的一体化分析流程')
    return f
def relative(v,allow_dot=False):
    if not isinstance(v,str) or not v or '\\' in v or '\x00' in v or '\n' in v or Path(v).is_absolute() or '..' in Path(v).parts:raise ValueError('数据路径必须在登记目录内')
    if not allow_dot and v=='.':raise ValueError('请选择具体文件或目录')
    return v
def safe_values(v,depth=0):
    if depth>16:raise ValueError('参数嵌套过深')
    if isinstance(v,dict):
        for k,item in v.items():
            if not isinstance(k,str) or k in BLOCKED or k.endswith(('_bin','_executable','_script')):raise ValueError('运行方式由一体化分析流程决定，不能替换程序或脚本')
            safe_values(item,depth+1)
    elif isinstance(v,list):
        if len(v)>5000:raise ValueError('参数列表过长')
        for item in v:safe_values(item,depth+1)
    elif isinstance(v,str):
        if len(v)>16384 or any(x in v for x in ('\x00','\r','\n','`','$(',';','&','|','>','<')):raise ValueError('参数不能包含命令片段或多行脚本')
        if v.startswith(('@input:','@output:')):relative(v.split(':',1)[1])
        elif '://' in v:raise ValueError('文件参数请使用服务器上的路径，不直接读取远程地址')
    elif v is not None and (type(v) not in (bool,int,float) or isinstance(v,float) and not math.isfinite(v)):raise ValueError('参数类型不正确')
def typed(v,s):
    kind=s.get('type')
    if kind=='boolean' and type(v) is not bool:raise ValueError('开关参数须为布尔值')
    if kind=='integer' and type(v) is not int:raise ValueError('参数须为整数')
    if kind=='number' and type(v) not in (int,float):raise ValueError('参数须为数值')
    if kind=='string' and not isinstance(v,str):raise ValueError('参数须为文字')
    if kind=='array' and not isinstance(v,list):raise ValueError('参数须为列表')
    if kind=='object' and not isinstance(v,dict):raise ValueError('参数须为对象')
    if s.get('enum') and v not in s['enum']:raise ValueError('参数不在软件提供的选项中')
    if s.get('choices') and v not in s['choices']:raise ValueError('参数不在软件提供的选项中')
    if isinstance(v,(list,dict)) and kind not in ('array','object') and s.get('nargs') not in ('+','*'):raise ValueError('参数格式不匹配')
def select_steps(flow,requested):
    ids=requested if requested is not None else flow['defaultSteps']
    if not isinstance(ids,list) or not ids or len(ids)>100 or len(ids)!=len(set(ids)):raise ValueError('请选择需要运行的步骤')
    by_id={s['id']:s for s in flow['nodes']+flow['extraNodes']}
    if any(s not in by_id or not by_id[s]['entryPresent'] for s in ids):raise ValueError('所选步骤不属于此流程或暂不可用')
    return [by_id[s] for s in flow['order'] if s in ids]
def normalize_step_parameters(step,parameters,config):
    fields={a['key']:a for a in step['arguments'] if not a['managed']}
    aliases={k:k for k in fields}
    for key,arg in fields.items():
        for flag in arg.get('flags',[]):
            aliases[flag]=key;aliases[flag.lstrip('-')]=key
    config_keys=set(step.get('configSchema',{}).get('properties',{}))|set(step.get('configDefaults',{}))|set(step.get('contract',{}).get('required_config',[]))
    if step['kind']=='omics':config_keys|={'profile','expected_outputs'}
    result={};cfg={}
    for origin,source in [('parameters',parameters),('configs',config)]:
        if source is None:continue
        if not isinstance(source,dict):raise ValueError(step['id']+' 的 '+origin+' 须为对象')
        for key,value in source.items():
            if step['kind']=='omics' and key in config_keys and (origin=='configs' or key not in aliases):target,name=cfg,key
            elif key in aliases:target,name=result,aliases[key]
            elif step['kind']=='omics' and origin=='configs':target,name=cfg,key
            else:raise ValueError(step['id']+' 无法识别参数：'+key+'；允许参数：'+', '.join(sorted(set(fields)|config_keys)))
            if name in target and target[name]!=value:raise ValueError(step['id']+' 参数 '+name+' 重复且值不一致，请保留一个明确值')
            target[name]=value
    return result,cfg

def resolve_parameters(flow,steps,body):
    params=body.get('parameters',{});configs=body.get('configs',{})
    if not isinstance(params,dict) or not isinstance(configs,dict):raise ValueError('步骤参数须按步骤名称填写')
    selected={s['id'] for s in steps}
    if set(params)-selected or set(configs)-selected:raise ValueError('参数包含未选择的步骤')
    result={};effective={}
    for step in steps:
        sid=step['id'];given,step_config=normalize_step_parameters(step,params.get(sid,{}),configs.get(sid,{}))
        fields={a['key']:a for a in step['arguments'] if not a['managed']}
        no_extra(given,set(fields));safe_values(given)
        values={a['key']:a['default'] for a in fields.values() if 'default' in a and a['default'] is not None};values.update({k:v for k,v in step.get('parameterDefaults',{}).items() if k in fields});values.update({k:v for k,v in given.items() if v!='' or fields[k].get('required')})
        if sid=='gene_family_report' and not values.get('expected_stages'):values['expected_stages']=','.join(s['id'] for s in steps if s['id']!='gene_family_report')
        for k,v in values.items():typed(v,fields[k])
        missing=[k for k,a in fields.items() if a.get('required') and values.get(k) in (None,'')]
        if missing:raise ValueError(step['name']+'需要填写：'+', '.join(missing))
        result[sid]=values
        if step['kind']=='omics':
            cfg=dict(step.get('configDefaults',{}));cfg.update(step_config);cfg.pop('outdir',None);safe_values(cfg)
            if step.get('nextflowSource'):
                known=set(step.get('configSchema',{}).get('properties',{}))|set(step['contract']['required_config'])|{'profile','expected_outputs'}
                if set(cfg)-known:raise ValueError('上游参数不在固定软件的声明中')
            if step.get('extensionStage'):
                fields=step['configSchema']['properties'];no_extra(cfg,set(fields))
                for key,value in cfg.items():typed(value,fields[key])
                for key,value in cfg.items():
                    if value!='@auto':continue
                    auto=step.get('autoInputs',{}).get(key)
                    if not auto:raise ValueError(step['name']+'缺少输入。请选择前置步骤，或在对话中提供已有分析结果的文件路径。')
                    if not any(s in selected for s in auto['steps']):
                        if 'fallback' in auto:cfg[key]=auto['fallback']
                        else:raise ValueError(step['name']+'缺少输入。请选择前置步骤，或在对话中提供已有分析结果的文件路径。')
            missing=[k for k in step['contract']['required_config'] if cfg.get(k) in (None,'',[],{})]
            if missing:raise ValueError(step['name']+'需要填写：'+', '.join(missing))
            effective[sid]=cfg
    return result,effective
def signature(p):
    s=p.stat(follow_symlinks=False)
    if not stat.S_ISREG(s.st_mode):raise ValueError('输入必须是可读取的普通文件')
    return {'size':s.st_size,'mtimeNs':s.st_mtime_ns,'ctimeNs':s.st_ctime_ns,'device':s.st_dev,'inode':s.st_ino}
def input_path(r,base,name):
    import server_workspace as storage
    relative(name,allow_dot=True)
    try:p=(base/name).resolve(strict=True)
    except (OSError,RuntimeError) as error:raise ValueError('输入路径无法读取，链接可能失效或循环：'+name) from error
    if p==base or base in p.parents:return p
    return storage.read_path(r,str(p))

def input_files(r,base,allow_empty=False):
    rows={};visited=0
    def walk(folder,ancestors):
        nonlocal visited
        target=input_path(r,base,folder.relative_to(base).as_posix())
        if target in ancestors:raise ValueError('输入目录包含循环链接：'+folder.relative_to(base).as_posix())
        for p in sorted(folder.iterdir()):
            visited+=1
            if visited>20000:raise ValueError('输入目录条目过多，请选择更具体的数据目录')
            name=p.relative_to(base).as_posix();resolved=input_path(r,base,name)
            if resolved.is_dir():walk(p,ancestors|{target});continue
            s=signature(resolved)
            if resolved!=p:
                s.update(sourcePath=str(resolved),readPath='/input/linked/'+hashlib.sha256(str(resolved.parent).encode()).hexdigest()[:20]+'/'+resolved.name)
            if s['size']<=8*1048576:s['sha256']=file_hash(resolved)
            rows[name]=s
            if len(rows)>5000:raise ValueError('请选择更具体的输入目录，单次最多 5000 个文件')
    walk(base,set())
    if not rows and not allow_empty:raise ValueError('所选输入目录没有文件')
    return rows

def linked_mounts(files):
    return list({str(Path(s['sourcePath']).parent):{'source':str(Path(s['sourcePath']).parent),'target':str(Path(s['readPath']).parent),'inputDirectory':True} for s in files.values() if s.get('sourcePath')}.values())

def input_folder(r,db,body):
    import server_workspace as storage
    value=body.get('input','.')
    if isinstance(value,str) and (Path(value).is_absolute() or value.startswith('~/')):
        name,folder=storage.source(r,db,value)
        return name,folder,folder,'.'
    name=r.identifier(body.get('source'));base=r.source_path(r.config(db).get('sources',{}).get(name,''),imported=name.startswith('import_'))
    folder=input_path(r,base,relative(value,allow_dot=True))
    if folder!=base/value:
        name,folder=storage.source(r,db,str(folder));return name,folder,folder,'.'
    return name,base,folder,value

def external_inputs(r,values,configs,folder,image):
    import server_workspace as storage
    mounts={};mapping={};documents={};seen=set()
    def visit(value,key=''):
        if isinstance(value,dict):
            for k,v in value.items():visit(v,k)
        elif isinstance(value,list):
            for v in value:visit(v,key)
        elif isinstance(value,str):
            if key=='de_files':
                for part in value.split(','):visit(part.strip(),'de_file')
            elif Path(value).is_absolute() or value.startswith('~/'):add(value,key)
            elif value and key in ('config','sample_table','samples','metadata','sample_config') and not value.startswith('@'):
                p=folder/value
                if p.is_file():inspect_document(p)
    def inspect_document(p):
        name=str(p.resolve())
        if name in seen or p.suffix.lower() not in ('.json','.yaml','.yml','.csv','.tsv') or p.stat().st_size>1048576:return
        seen.add(name);text=p.read_text(encoding='utf-8-sig')
        if p.suffix.lower() in ('.csv','.tsv'):
            import csv,io
            reader=csv.DictReader(io.StringIO(text),delimiter='\t' if p.suffix.lower()=='.tsv' else ',');rows=list(reader)
            documents[name]={'__omaax_table__':True,'columns':reader.fieldnames or [],'rows':rows,'delimiter':'\t' if p.suffix.lower()=='.tsv' else ','};visit(rows)
            return
        try:document=json.loads(text)
        except json.JSONDecodeError:
            try:
                import yaml
                document=yaml.safe_load(text)
            except ImportError:
                code='import json,sys,yaml\nv=yaml.safe_load(sys.stdin.read())\nn=[0]\ndef check(x,d=0):\n n[0]+=1\n if d>32 or n[0]>20000:raise ValueError("Configuration too large")\n if isinstance(x,dict):\n  for y in x.values():check(y,d+1)\n elif isinstance(x,list):\n  for y in x:check(y,d+1)\ncheck(v)\ns=json.dumps(v)\nif len(s)>2097152:raise ValueError("Configuration too large")\nprint(s)'
                result=subprocess.run(r.image_command(image,['python3','-c',code]),input=text,text=True,capture_output=True,timeout=40)
                if result.returncode:raise ValueError('配置文件无法解析，请用工作文件夹的配置表功能重新生成')
                document=json.loads(result.stdout)
        safe_values(document);documents[name]=document;visit(document)
    def add(value,key):
        raw=Path(value).expanduser()
        if not raw.is_absolute():raise ValueError('参考路径不正确')
        directory=None;index=False
        if raw.exists():
            p=storage.read_path(r,str(raw));directory=p if p.is_dir() else None
            paths=[p/name for name in input_files(r,p,allow_empty=True)] if directory else [raw]
            mapping[value]=('__main__:'+p.relative_to(folder).as_posix() if p==folder or folder in p.parents else '/input/refs/'+hashlib.sha256(str(p if p.is_dir() else p.parent).encode()).hexdigest()[:20]+('/'+p.name if p.is_file() else ''))
        elif key in ('hisat2_index','bowtie2_index','bwa_index'):
            index=True
            parent=storage.read_path(r,str(raw.parent));paths=sorted(parent.glob(raw.name+'.*'))
            if not paths:raise ValueError('索引前缀没有对应文件：'+value)
            mapping[value]=('__main__:'+(parent/raw.name).relative_to(folder).as_posix() if parent==folder or folder in parent.parents else '/input/refs/'+hashlib.sha256(str(parent).encode()).hexdigest()[:20]+'/'+raw.name)
        else:raise ValueError('参考文件或目录不存在：'+value)
        mapping[str(raw.resolve())]=mapping[value]
        for logical in paths:
            if logical.is_dir():continue
            p=storage.read_path(r,str(logical))
            if mapping[value].startswith('__main__:'):
                inspect_document(p);continue
            target=mapping[value]+('/'+logical.relative_to(directory).as_posix() if directory else logical.name[len(raw.name):] if index else '')
            mounts[target]={'source':str(p),'target':target,'signature':signature(p)}
            if p!=logical:mounts[target]['originalPath']=str(logical)
            if len(mounts)>256:raise ValueError('参考文件过多，请使用更具体的参考路径')
            inspect_document(p)
    visit(values);visit(configs)
    return list(mounts.values()),mapping,documents
def bundle_path(r):
    parent=r.root()/'workflow-code'/CATALOG['bundleSha256']
    for p in [parent,parent.parent]:
        if p.is_symlink():raise ValueError('流程源码目录不能被重定向')
    parent.mkdir(parents=True,exist_ok=True,mode=0o700);p=parent/'kernel.zip'
    if p.is_symlink():raise ValueError('流程源码不能是链接')
    return p
def ready_bundle(r):
    p=bundle_path(r)
    if not p.is_file() or file_hash(p)!=CATALOG['bundleSha256']:raise ValueError('流程源码尚未准备完成，请重新检查运行环境')
    return p
def available_images(r):
    found={}
    if not shutil.which('podman'):return found
    for flow in ['gene_family','rnaseq','singlecell']:
        try:
            info=json.loads(r.command(['podman','image','inspect','localhost/omaax-workflow-'+flow+':0.4.0'],timeout=15))[0]
            image=info.get('Id') or info.get('ID')
            if image and not image.startswith('sha256:'):image='sha256:'+image
            if r.immutable_image(image):found[flow]=image
        except (ValueError,KeyError,IndexError,TypeError,FileNotFoundError,subprocess.TimeoutExpired):pass
    if 'singlecell' in found:found.update(spatial=found['singlecell'],metagenome=found['singlecell'])
    return found

# Check software presence, not biological suitability or availability of reference data.
STEP_SOFTWARE={
    'ncbi_fetch_sequences':{'modules':['Bio']},
    'pep':{'modules':['openpyxl']},
    'identify':{'tools':['hmmsearch','blastp']},'phylogeny':{'tools':['mafft','iqtree']},
    'motif':{'tools':['meme']},'domain':{'tools':['interproscan']},
    'synteny':{'tools':['MCScanX','blastp']},'interspecies_synteny':{'tools':['MCScanX','blastp']},
    'ortholog':{'tools':['blastp']},'kaks':{'tools':['mafft','KaKs_Calculator']},
    'msa':{'tools':['mafft']},'msa_region_plot':{'tools':['mafft']},
    'rnaseq_qc':{'tools':['fastp']},'rnaseq_align':{'tools':['hisat2','samtools']},
    'rnaseq_quant':{'tools':['featureCounts']},
    'rnaseq_sample_qc':{'r':['DESeq2','ggplot2','pheatmap']},
    'rnaseq_de':{'r':['DESeq2','ggplot2','pheatmap']},
    'rnaseq_wgcna':{'r':['WGCNA','DESeq2','ggplot2','pheatmap']},
    'rnaseq_go_enrich':{'r':['clusterProfiler']},'rnaseq_kegg_enrich':{'r':['clusterProfiler']},
    'rnaseq_enrich':{'r':['clusterProfiler']},'enrichment':{'r':['clusterProfiler']},
    'rnaseq_fgsea':{'r':['fgsea']},
    'singlecell_analyze':{'modules':['scanpy','anndata']},
    'singlecell_annotate':{'modules':['anndata','pandas']},
    'singlecell_pseudobulk':{'modules':['anndata','pandas']},
    'singlecell_de':{'r':['DESeq2']},
    'singlecell_enrich':{'modules':['scipy','pandas']},
    'spatial_export':{'modules':['spatialdata','anndata']},
    'spatial_analyze':{'modules':['scanpy','squidpy','anndata']},
    'spatial_enrich':{'modules':['scipy','pandas']},
    'metagenome_merge':{'modules':['pandas']},'metagenome_summarize':{'modules':['pandas','matplotlib']},
    'metagenome_community':{'r':['vegan','ggplot2']},
    'metagenome_differential':{'r':['ANCOMBC','vegan']},
}
for _step in ('singlecell_upstream','spatial_segment','metagenome_profile','metagenome_mag'):
    STEP_SOFTWARE[_step]={'tools':['nextflow','java']}

def software_probe(r,image,profile=None):
    import workflow_environments as environments
    requirements=[profile] if profile is not None else list(STEP_SOFTWARE.values())+list(environments.DEPENDENCIES.values())
    modules=sorted({n for spec in requirements for n in spec.get('modules',[])})
    modules=sorted(set(modules)|({'harmonypy','scrublet'} if profile is None else set()))
    tools=sorted({n for spec in requirements for n in spec.get('tools',[])}|{'Rscript'}|({'cutadapt','salmon','kallisto'} if profile is None else set()))
    packages=sorted({n for spec in requirements for n in spec.get('r',[])}|({'tximport'} if profile is None else set()))
    upstream=sorted({n for spec in requirements for n in spec.get('processEnvironments',[])})
    code="""import importlib.util,json,shutil,subprocess,os,sys
modules=MODULES;tools=TOOLS;packages=PACKAGES
aliases={'iqtree':['iqtree3','iqtree2','iqtree'],'interproscan':['interproscan.sh','interproscan']}
def exists(n):
 try:return importlib.util.find_spec(n) is not None
 except (ImportError,ModuleNotFoundError,ValueError):return False
metadata={}
if os.path.isfile(os.environ.get('OMAAX_NEXTFLOW_ENVS','/nonexistent')):
 try:
  with open(os.environ['OMAAX_NEXTFLOW_ENVS']) as file:metadata=json.load(file)
 except (OSError,ValueError):pass
result={'modules':{n:exists(n) for n in modules},'tools':{n:any(shutil.which(a) for a in aliases.get(n,[n])) for n in tools},'r':{},'processEnvironments':{n:metadata.get('processKey',metadata.get('upstream'))==n and bool(metadata.get('environments')) for n in UPSTREAM}}
if packages and result['tools']['Rscript']:
    expression=';'.join('cat(\"OMAAX_R:'+n+'=\",requireNamespace(\"'+n+'\",quietly=TRUE),\"\\n\",sep=\"\")' for n in packages)
    try:p=subprocess.run(['Rscript','-e',expression],capture_output=True,text=True,timeout=90,env=dict(os.environ,CONDA_PREFIX=sys.prefix,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1'))
    except subprocess.TimeoutExpired:result['rError']='R 软件检查超时，请等待服务器空闲后重新检查；不会因此重装已有软件'
    else:
        if p.returncode:result['rError']=p.stderr[-1000:] or ('R 检查进程退出，状态码 '+str(p.returncode))
        for line in p.stdout.splitlines():
            if line.startswith('OMAAX_R:'):
                name,value=line[8:].split('=',1);result['r'][name]=value=='TRUE'
print(json.dumps(result))
""".replace('MODULES',repr(modules)).replace('TOOLS',repr(tools)).replace('PACKAGES',repr(packages)).replace('UPSTREAM',repr(upstream))
    return json.loads(r.command(r.image_command(image,['python3','-c',code]),timeout=120))

def readiness_for(step,probe,required=None):
    if not step['entryPresent']:return {'state':'missing_source','missing':[],'detail':'此步骤暂不可用，请选择其他分析步骤。'}
    if probe is None:return {'state':'missing_environment','missing':['运行环境'],'detail':'尚未为此流程登记运行环境。'}
    if probe.get('error'):return {'state':'unchecked','missing':[],'detail':probe['error']}
    required=required if required is not None else STEP_SOFTWARE.get(step['id'],{})
    if required.get('r') and probe.get('rError'):return {'state':'unchecked','missing':[],'detail':'R 环境检查未完成：'+probe['rError']}
    missing=[('R 包 '+name if kind=='r' else name) for kind,names in required.items() for name in names if not probe.get(kind,{}).get(name,False)]
    if required.get('r') and not probe.get('tools',{}).get('Rscript'):missing.insert(0,'Rscript')
    return {'state':'missing_environment' if missing else 'ready','missing':missing,'detail':'缺少：'+ '、'.join(missing) if missing else '基础运行环境及已声明的软件已检查；输入文件和参考数据在生成计划及执行时检查。'}
def recheck(r,plan,cfg):
    base=r.source_path(cfg['sources'][plan['source']],imported=plan['source'].startswith('import_'));folder=r.contained(base,plan['input'])
    for name,s in plan['inputs'].items():
        p=input_path(r,folder,name)
        if str(p)!=s.get('sourcePath',str(folder/name)):raise ValueError('输入链接目标发生变化，请重新生成计划：'+name)
        now=signature(p)
        if any(now[k]!=s[k] for k in now):raise ValueError('输入文件发生变化，请重新生成计划')
        if s.get('sha256') and file_hash(p)!=s['sha256']:raise ValueError('输入内容发生变化，请重新生成计划')
    for file in plan.get('externalFiles',[]):
        import server_workspace
        p=server_workspace.read_path(r,file['source'])
        if file.get('originalPath') and server_workspace.read_path(r,file['originalPath'])!=p:raise ValueError('参考文件链接目标发生变化，请重新生成计划')
        if file.get('inputDirectory'):
            if not p.is_dir() or str(p)!=file['source']:raise ValueError('输入链接目录发生变化，请重新生成计划')
            continue
        if signature(p)!=file['signature']:raise ValueError('参考文件发生变化，请重新生成计划')
    return folder
def load_plan(r,db,body):
    no_extra(body,{'id','digest'});row=db.execute('SELECT * FROM workflow_plans WHERE id=?',(r.identifier(body.get('id')),)).fetchone()
    if not row or row['digest']!=body.get('digest'):raise ValueError('计划版本不匹配，请刷新后重新确认')
    p=json.loads(row['plan'])
    if p.get('engineHash')!=ENGINE_HASH or p.get('catalogHash')!=digest(CATALOG) or p.get('identity')!=identity(r):raise ValueError('执行版本或服务器身份变化，请重新生成计划')
    cfg=r.config(db);snapshot=r.source_path(cfg['sources'][p['planSource']],imported=True)
    if file_hash(r.contained(snapshot,'plan.json'))!=row['digest'] or file_hash(r.contained(snapshot,'kernel.zip'))!=p['bundleHash'] or file_hash(r.contained(snapshot,'adapter.py'))!=p['adapterHash']:raise ValueError('固定计划或源码内容变化，禁止执行')
    recheck(r,p,cfg);return row,p
def job_spec(p):
    code='import sys,runpy,os,json; source=json.loads(os.environ.get("OMAAX_PATH_MAP","{}" )).get("/input/"+sys.argv[1],"/input/"+sys.argv[1]); runpy.run_path(source+"/adapter.py",run_name="__main__")'
    return {'id':p['id'],'context':p.get('context'),'title':p.get('prefix','')+' · '+p.get('name','分析任务'),'workflow':{'id':p['id'],'digest':digest(p)},'image':p['image'],'argv':['python3','-c',code,p['planSource'],digest(p)],'sources':[p['source'],p['planSource']],**({'files':p['externalFiles']} if p.get('externalFiles') else {}),
            'pipeline':[{'step':sid,'image':p['stageImages'].get(sid,p['image'])} for sid in p['steps']],**{k:p['resources'][k] for k in ('cpu','memoryMb','timeoutSeconds')}}
def validate_bound_job(r,spec):
    db=r.database();tables(db);row,p=load_plan(r,db,spec['workflow'])
    if not row['confirmed'] or spec!=job_spec(p):raise ValueError('固化任务必须与本人确认的完整计划一致')
def finish(r,job,exit_success):
    p=r.jobdir(job)/'output/workflow-result.json'
    if not p.is_file():return 'failed','执行未完成，查看日志了解原因'
    m=json.loads(p.read_text())
    if exit_success and m.get('state') in ('complete','partial'):return m['state'],'所选步骤已执行，结果与日志已保存'
    return 'failed',m.get('error','所选步骤执行失败，已有结果保留')[-1000:]
def progress(r,job,state=None):
    p=r.jobdir(job)/'output/workflow-progress.json'
    if p.is_file() and not p.is_symlink() and p.stat().st_size<1048576:
        steps=json.loads(p.read_text()).get('steps',[])
        if state=='cancelled':
            return [{**step,'state':'cancelled'} if step.get('state') in ('running','pending','waiting','queued') else step for step in steps]
        return steps
    return []
def result_file_key(file):
    parts=Path(file['path']).parts;name=parts[-1].lower()
    meaningful='results' in parts or 'final' in parts or name in ('report.html','report.md')
    upstream=any(part.endswith('_upstream') for part in parts)
    kind=0 if name.endswith(('.html','.md')) else 1 if name.endswith(('.png','.svg','.pdf')) else 2 if name.endswith(('.csv','.tsv')) else 3
    return (0 if meaningful and not upstream else 1 if meaningful else 2,kind,file['path'])

def validate_sample_documents(flow,steps,values,documents,folder,configs=None):
    fid=flow['id'];configs=configs or {}
    if fid in ('atacseq','chipseq','cutandrun','methylseq'):
        stage=fid+('_dmr' if fid=='methylseq' else '_differential')
        if stage in {step['id'] for step in steps}:
            config=configs.get(stage,{});sheet=config.get('samples','@auto')
            if sheet=='@auto':sheet=configs.get(fid+('_coverage_qc' if fid=='methylseq' else '_count'),{}).get('samples','@auto')
            if sheet=='@auto':sheet=configs.get(fid+'_upstream',{}).get('input','')
            path=Path(sheet).expanduser();path=path if path.is_absolute() else folder/path
            table=documents.get(str(path.resolve()),{})
            if table.get('__omaax_table__'):
                groups={str(row.get('group') or row.get('sample') or '') for row in table['rows']}
                if not {str(config.get('control','')),str(config.get('treated',''))}.issubset(groups):
                    raise ValueError('差异分析的对照组或处理组不在样本表中；请把计数或覆盖度步骤的样本信息表指定为包含 sample_id、group 的分组表，并核对组名。')
        return
    if fid!='rnaseq':return
    for step in steps:
        params=values.get(step['id'],{})
        for key in ('config','sample_config'):
            value=params.get(key)
            if not value or value.startswith('@'):continue
            path=Path(value).expanduser();path=path if path.is_absolute() else folder/path
            document=documents.get(str(path.resolve()))
            if not isinstance(document,dict):raise ValueError(step['name']+'的样本配置必须是 YAML 或 JSON 对象，请按流程目录中的 inputTemplates 填写')
            samples=document.get('samples')
            if samples is not None and not isinstance(samples,dict):raise ValueError(step['name']+'的 samples 必须是“样本名 → 样本信息”的对象，不能使用列表；双端文件字段为 r1、r2，请按 inputTemplates 重新生成配置')
            if step['id'] in ('rnaseq_qc','rnaseq_align','rnaseq_quant') and not samples:raise ValueError(step['name']+'的配置缺少 samples 样本表')
            if step['id']=='rnaseq_qc':
                for name,item in samples.items():
                    pair=item[:2] if isinstance(item,list) else [item.get('r1') or item.get('read1'),item.get('r2') or item.get('read2')] if isinstance(item,dict) else []
                    if len(pair)!=2 or not all(isinstance(p,str) and p for p in pair):raise ValueError('样本 '+str(name)+' 必须填写 r1、r2 双端文件路径')
            if document.get('groups') is not None and not isinstance(document['groups'],dict):raise ValueError('groups 必须是“样本名 → 分组名”的对象')
            if samples and step['id']=='rnaseq_de':
                groups=document.get('groups',{});pairs=document.get('compare',[])
                pairs=[p for p in pairs if isinstance(p,list) and len(p)==2 and all(isinstance(v,str) for v in p)] if isinstance(pairs,list) else []
                control={p[0] for p in pairs};treated={p[1] for p in pairs}
                for name,item in samples.items():
                    if not isinstance(item,dict):raise ValueError('差异分析的样本信息必须是对象，请按 inputTemplates 填写')
                    # Translate only explicitly supplied groups/comparisons; never infer experimental roles.
                    condition=item.get('condition') or item.get('group') or item.get('treatment') or groups.get(name)
                    if not isinstance(condition,(str,int,float)) or isinstance(condition,bool) or not str(condition).strip():raise ValueError('样本 '+str(name)+' 缺少分组信息，请补齐 condition 或 groups')
                    condition=str(condition).strip()
                    if not (item.get('condition') or item.get('group') or item.get('treatment')):item['condition']=condition
                    if not item.get('group_role') and (condition in control)!=(condition in treated):item['group_role']='control' if condition in control else 'compare'
                    if not item.get('group_role'):raise ValueError('样本 '+str(name)+' 缺少 group_role，请明确对照/处理角色或填写 compare 对照关系')

def dispatch(r,action,body,actor='user'):
    db=r.database();tables(db);cfg=r.config(db)
    if actor=='agent' and action in ('workflow_confirm','workflow_bundle_status'):raise ValueError('模型只能提出计划，不能自行确认运行')
    if action=='workflow_deferred_plan':
        if actor!='user':raise ValueError('流程准备只接受桌面可信请求')
        import workflow_preflight
        return workflow_preflight.start(r,body)
    if action in ('workflow_prepare','workflow_prepare_status'):
        import workflow_environments as environments
        if action=='workflow_prepare':return environments.start(r,body)
        no_extra(body,{'workflow'});flow_for(body.get('workflow'));return environments.status(r,body['workflow'])
    if action=='workflow_bundle_status':
        no_extra(body,set());p=bundle_path(r);return {'path':str(p),'sha256':CATALOG['bundleSha256'],'ready':p.is_file() and file_hash(p)==CATALOG['bundleSha256']}
    if action=='workflow_validate':
        no_extra(body,PARAMETERS);flow=flow_for(body.get('workflow'));steps=select_steps(flow,body.get('steps'))
        resolve_parameters(flow,steps,body)
        source,base,folder,_=input_folder(r,db,body);files=input_files(r,folder)
        for k,low,high in [('cpu',1,os.cpu_count() or 1),('memoryMb',128,1048576),('timeoutSeconds',10,604800)]:
            v=body.get(k,{'cpu':1,'memoryMb':2048,'timeoutSeconds':86400}[k])
            if type(v) is not int or not low<=v<=high:raise ValueError('计算资源设置不正确：'+k)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',body.get('prefix','omaax')):raise ValueError('结果名称只能使用字母、数字、点、下划线或短横线')
        return {'valid':True,'fileCount':len(files),'steps':[s['id'] for s in steps]}
    if action=='workflow_readiness':
        no_extra(body,{'workflow','steps','parameters','configs'});safe_values(body.get('parameters',{}));safe_values(body.get('configs',{}));flow=flow_for(body.get('workflow'));env=cfg.get('workflowEnvironments',{}).get(flow['id'],{})
        steps=select_steps(flow,body['steps']) if 'steps' in body else flow['nodes']+flow['extraNodes']
        body=dict(body);body['parameters']=dict(body.get('parameters',{}));body['configs']=dict(body.get('configs',{}))
        for step in steps:
            sid=step['id'];body['parameters'][sid],body['configs'][sid]=normalize_step_parameters(step,body['parameters'].get(sid,{}),body['configs'].get(sid,{}))
        probes={};checks={}
        import workflow_environments as environments
        wanted=environments.combined([environments.requirements(step,body.get('parameters',{}).get(step['id'],{}),body.get('configs',{}).get(step['id'],{})) for step in steps])
        for step in steps:
            image=env.get('stageImages',{}).get(step['id']) or env.get('image')
            if image and image not in probes:
                if not r.immutable_image(image) or image not in cfg.get('images',[]):probes[image]={'error':'环境登记已失效，请重新选择环境。'}
                else:
                    try:probes[image]=software_probe(r,image,wanted)
                    except (ValueError,OSError,subprocess.TimeoutExpired) as error:probes[image]={'error':'环境检查未完成：'+str(error)}
            import workflow_environments as environments
            checks[step['id']]=readiness_for(step,probes.get(image),environments.requirements(step,body.get('parameters',{}).get(step['id'],{}),body.get('configs',{}).get(step['id'],{})))
        return {'workflow':flow['id'],'steps':checks,'checkedAt':time.time(),'readOnly':True}
    if action=='workflow_environment':
        no_extra(body,{'workflow','image','step'});flow=flow_for(body.get('workflow'));image=body.get('image')
        if not r.immutable_image(image):raise ValueError('请选择具有固定摘要的本地镜像')
        if body.get('step'):select_steps(flow,[body['step']])
        if image not in cfg.get('images',[]) and image.startswith('conda:'):raise ValueError('请选择已配置的 Conda 环境')
        if image not in cfg.get('images',[]):
            info=json.loads(r.command(['podman','image','inspect',image],timeout=15))
            if not info:raise ValueError('此镜像尚未安装到当前账号，请先配置环境')
            images=list(dict.fromkeys([*cfg.get('images',[]),image]));db.execute('INSERT OR REPLACE INTO config VALUES(?,?)',('images',json.dumps(images)))
            r.audit(db,'workflow.image_approved_by_user',{'workflow':flow['id'],'image':image})
        ready_bundle(r);r.protection()
        code="import sys,json,importlib.util,shutil; print(json.dumps({'python':sys.version.split()[0],'modules':{n:importlib.util.find_spec(n) is not None for n in ['numpy','pandas','yaml','Bio','scanpy','anndata','squidpy']},'tools':{n:bool(shutil.which(n)) for n in ['Rscript','hmmsearch','blastp','mafft','iqtree','meme','fastp','hisat2','samtools','featureCounts','nextflow','java']}}))"
        v=json.loads(r.command(r.image_command(image,['python3','-c',code]),timeout=60))
        envs=cfg.get('workflowEnvironments',{});entry=envs.setdefault(flow['id'],{'image':image,'versions':v,'stageImages':{}})
        if body.get('step'):entry.setdefault('stageImages',{})[body['step']]=image
        else:entry.update(image=image,versions=v)
        db.execute('INSERT OR REPLACE INTO config VALUES(?,?)',('workflowEnvironments',json.dumps(envs)));r.audit(db,'workflow.environment_ready',{'workflow':flow['id'],'image':image})
        return {'ready':True,'versions':v,'message':'运行环境已登记；所选步骤所需软件以执行时检查为准'}
    if action=='workflow_inspect':
        no_extra(body,{'source','input'});source,base,folder,_=input_folder(r,db,body)
        if not folder.is_dir():raise ValueError('请选择本次数据目录')
        rows=input_files(r,folder);files=[{'name':name,'size':value['size']} for name,value in rows.items()]
        headers={}
        for row in files[:300]:
            path=input_path(r,folder,row['name'])
            if path.suffix.lower() in ('.csv','.tsv','.txt'):
                with path.open('rb') as f:line=f.readline(1024)
                headers[row['name']]=line.decode('utf-8','replace').strip()
        return {'source':source,'input':str(folder),'files':files[:300],'fileCount':len(files),'headers':headers,'readOnly':True}
    if action=='workflow_plan':
        no_extra(body,PARAMETERS)
        if not ENGINE_HASH or not ADAPTER_SOURCE:raise ValueError('固定执行组件未准备完成')
        flow=flow_for(body.get('workflow'));steps=select_steps(flow,body.get('steps'));values,configs=resolve_parameters(flow,steps,body)
        env=cfg.get('workflowEnvironments',{}).get(flow['id'])
        if not env or env['image'] not in cfg.get('images',[]):raise ValueError('先选择并检查此流程的运行环境')
        import workflow_environments as environments
        probes={}
        wanted=environments.combined([environments.requirements(step,values.get(step['id'],{}),configs.get(step['id'],{})) for step in steps])
        for step in steps:
            image=env.get('stageImages',{}).get(step['id'],env['image'])
            if image not in cfg.get('images',[]):raise ValueError(step['name']+'的环境登记已失效，请重新配置')
            if image not in probes:probes[image]=software_probe(r,image,wanted)
            ready=readiness_for(step,probes[image],environments.requirements(step,values.get(step['id'],{}),configs.get(step['id'],{})))
            if ready['state']!='ready':raise ValueError(step['name']+'尚不能运行：'+ready['detail']+'。请先检查并配置所选步骤。')
        r.protection();bundle=ready_bundle(r);source,base,folder,chosen_input=input_folder(r,db,body)
        if not folder.is_dir():raise ValueError('请选择包含本次数据的目录')
        files=input_files(r,folder);external,path_map,documents=external_inputs(r,values,configs,folder,env['image']);validate_sample_documents(flow,steps,values,documents,folder,configs);external+=linked_mounts(files);total=sum(f['size'] for f in files.values())
        if len(external)>256:raise ValueError('输入分散在过多目录中，请选择更具体的数据目录或拆分任务')
        if total+bundle.stat().st_size+128*1048576>r.disk_available():raise ValueError('任务副本和结果准备空间不足，原文件未改动')
        resources={k:body.get(k,v) for k,v in [('cpu',20),('memoryMb',51200),('timeoutSeconds',86400)]}
        for k,low,high in [('cpu',1,os.cpu_count() or 1),('memoryMb',128,1048576),('timeoutSeconds',10,604800)]:
            if type(resources[k]) is not int or not low<=resources[k]<=high:raise ValueError('资源参数不正确：'+k)
        prefix=body.get('prefix','omaax')
        if not isinstance(prefix,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',prefix):raise ValueError('结果名称只能使用字母、数字、点、下划线或短横线')
        import workspace_context
        context=workspace_context.normalize(body.get('context'));workspace_context.base(r,context);preferences=body.get('preferences',{})
        no_extra(preferences,{'library_strandness'})
        if preferences.get('library_strandness','') not in ('','non_stranded','forward','reverse'):raise ValueError('文库链特异性选项不正确')
        transfer=str(uuid.uuid4());dest=Path(r.dispatch({'action':'import_prepare','body':{'id':transfer,'bytes':bundle.stat().st_size+2*1048576}})['path'])
        p={'id':str(uuid.uuid4()),'workflow':flow['id'],'context':context,'preferences':preferences,'name':flow['name'],'version':CATALOG['version'],'engineHash':ENGINE_HASH,'catalogHash':digest(CATALOG),'bundleHash':CATALOG['bundleSha256'],'identity':identity(r),
           'adapterHash':hashlib.sha256(ADAPTER_SOURCE.encode()).hexdigest(),'image':env['image'],'stageImages':{k:v for k,v in env.get('stageImages',{}).items() if k in {s['id'] for s in steps}},'software':env['versions'],'source':source,'sourceBase':str(base),'input':chosen_input,'inputs':files,'externalFiles':external,'pathMap':path_map,'configDocuments':documents,'planSource':'import_'+transfer,'prefix':prefix,'resources':resources,
           'steps':[s['id'] for s in steps],'parameters':values,'configs':configs,'parameterOrigin':'model_proposal' if actor=='agent' else 'user','validationMode':'execution_only'}
        sha=digest(p);(dest/'plan.json').write_bytes(canonical(p));(dest/'adapter.py').write_bytes(ADAPTER_SOURCE.encode());shutil.copyfile(bundle,dest/'kernel.zip')
        r.dispatch({'action':'import_finish','body':{'id':transfer,'files':[{'name':f.name,'size':f.stat().st_size,'sha256':file_hash(f)} for f in dest.iterdir()]}})
        db.execute('INSERT INTO workflow_plans VALUES(?,?,?,?,?)',(p['id'],sha,json.dumps(p),0,time.time()));r.audit(db,'workflow.plan_created',{'id':p['id'],'workflow':flow['id']})
        return {'plan':p,'digest':sha,'confirmed':False,'state':'awaiting_confirmation'}
    if action=='workflow_resume':
        if actor!='user':raise ValueError('续跑需要用户在分析页面确认')
        import scheduler_backend,workflow_preflight
        cluster=scheduler_backend.cluster()
        if cluster and not scheduler_backend.allocation():
            return workflow_preflight.resume(r,body,cluster)
        row,p=load_plan(r,db,body)
        if not row['confirmed']:raise ValueError('请先确认执行计划')
        return r.resume_job(p['id'])
    if action=='workflow_confirm':
        intent=body.get('intent')
        if intent is not None:
            no_extra(body,{'id','digest','intent'})
            if not isinstance(intent,dict) or intent.get('source')!='user' or intent.get('executionRequested') is not True:raise ValueError('缺少真实用户任务指令')
        row,p=load_plan(r,db,{k:v for k,v in body.items() if k!='intent'})
        if cfg.get('workflowEnvironments',{}).get(p['workflow'],{}).get('image')!=p['image']:raise ValueError('运行环境已变化，请重新生成计划')
        current=cfg.get('workflowEnvironments',{}).get(p['workflow'],{}).get('stageImages',{})
        if {k:v for k,v in current.items() if k in p['steps']}!=p['stageImages']:raise ValueError('步骤环境已变化，请重新生成计划')
        db.execute('UPDATE workflow_plans SET confirmed=1 WHERE id=?',(p['id'],));r.audit(db,'workflow.confirmed',{'id':p['id'],'digest':row['digest'],'intent':intent})
        result=r.dispatch({'action':'submit','body':job_spec(p)})
        if intent is not None:(r.jobdir(p['id'])/'task-intent.json').write_text(json.dumps(intent,ensure_ascii=False),encoding='utf-8')
        return result
    if action=='workflow_plans':
        no_extra(body,set())
        import workflow_preflight
        return workflow_preflight.pending(r)+[{'plan':json.loads(row['plan']),'digest':row['digest'],'confirmed':bool(row['confirmed']),'state':row['state'] or 'awaiting_confirmation','detail':row['detail'] or '', 'progress':progress(r,row['id'],row['state'])} for row in db.execute('SELECT p.*,j.state,j.detail FROM workflow_plans p LEFT JOIN jobs j ON p.id=j.id ORDER BY p.created DESC LIMIT 30')]
    if action=='workflow_result':
        no_extra(body,{'id','file','offset'});offset=body.get('offset',0)
        if type(offset) is not int or offset<0:raise ValueError('结果列表位置无效')
        job=r.identifier(body.get('id'));row=db.execute('SELECT p.plan,j.state,j.detail FROM workflow_plans p JOIN jobs j ON p.id=j.id WHERE j.id=?',(job,)).fetchone()
        if not row or row['state'] not in ('complete','partial','failed','cancelled','invalid_result','interrupted'):raise ValueError('任务尚未结束，请先查看进度或日志')
        directory=r.jobdir(job)/'output'
        if (directory/'workflow-result.json').is_file():m=json.loads(r.contained(directory,'workflow-result.json').read_text())
        elif row['state'] in ('failed','cancelled','invalid_result','interrupted'):m={'error':row['detail'] or '任务未开始分析，详情请查看日志','steps':progress(r,job,row['state'])}
        else:raise ValueError('结果记录缺失，请查看任务日志；不会将缺失的结果视为已完成')
        p=json.loads(row['plan']);verified=[]
        for f in m.get('files',[]):
            path=r.contained(directory,f['path'])
            if path.stat().st_size!=f['size'] or file_hash(path)!=f['sha256']:raise ValueError('结果文件发生变化，不能使用旧结果摘要')
            verified.append(f)
        verified.sort(key=result_file_key)
        if offset>len(verified):raise ValueError('结果列表位置无效，请重新查看结果')
        result={'id':job,'workflow':p['workflow'],'name':p.get('name'),'version':p['version'],'state':row['state'],'steps':m.get('steps',[]),'files':verified[offset:offset+200],'fileCount':len(verified),'nextOffset':offset+200 if offset+200<len(verified) else None,'parameters':p['parameters'],'configs':p['configs'],'resources':p['resources'],'software':p['software'],'validationMode':'execution_only','error':m.get('error')}
        if body.get('file'):
            selected=next((f for f in verified if f['path']==body['file']),None)
            if not selected:raise ValueError('只能读取本任务发布的结果文件')
            path=r.contained(directory,selected['path'])
            mime={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp','.pdf':'application/pdf'}.get(path.suffix.lower())
            if mime:
                if path.stat().st_size>8*1048576:raise ValueError('文件超过 8 MiB，请下载查看')
                result.update(contentBase64=base64.b64encode(path.read_bytes()).decode(),mime=mime,contentFile=selected['path'])
            else:
                if path.suffix.lower() not in ('.txt','.tsv','.csv','.md','.json','.html','.log'):raise ValueError('此格式请下载查看')
                with path.open('rb') as handle:content=handle.read(65536)
                result.update(content=content.decode('utf-8','replace'),contentFile=selected['path'],truncated=selected['size']>65536)
        return result
    raise ValueError('不支持的一体化分析流程操作')
