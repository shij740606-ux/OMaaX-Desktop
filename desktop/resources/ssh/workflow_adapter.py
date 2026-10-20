"""Run bundled entry points in a new task directory. No biological approval checkpoints."""
import ast,csv,gzip,hashlib,json,math,os,re,shlex,shutil,stat,subprocess,sys,time,zipfile
from pathlib import Path,PurePosixPath

def runtime_path(value):
    for target,source in sorted(json.loads(os.environ.get('OMAAX_PATH_MAP','{}')).items(),key=lambda pair:-len(pair[0])):
        if value==target or value.startswith(target+'/'):return source+value[len(target):]
    return value

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        while b:=f.read(1048576):h.update(b)
    return h.hexdigest()
def save(path,data):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
def contained(base,name):
    if not isinstance(name,str) or '\\' in name or Path(name).is_absolute() or '..' in Path(name).parts:raise ValueError('Invalid task-relative path')
    p=base/name
    if any(x.is_symlink() for x in [p,*p.parents]):raise ValueError('Linked data paths are not allowed')
    resolved=p.resolve()
    if resolved!=base.resolve() and base.resolve() not in resolved.parents:raise ValueError('Path outside task')
    return p
def copy_input(source,name,dest,expected):
    parts=PurePosixPath(name).parts
    if not parts or '..' in parts:raise ValueError('Invalid input name')
    directory=os.open(source,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory);os.close(directory);directory=nxt
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
    finally:os.close(directory)
    dest.parent.mkdir(parents=True,exist_ok=True);h=hashlib.sha256()
    with os.fdopen(fd,'rb') as f:
        before=os.fstat(f.fileno())
        if not stat.S_ISREG(before.st_mode):raise ValueError('Special input file')
        now={'size':before.st_size,'mtimeNs':before.st_mtime_ns,'ctimeNs':before.st_ctime_ns,'device':before.st_dev,'inode':before.st_ino}
        if any(now[k]!=expected[k] for k in now):raise ValueError('Input changed after confirmation')
        with dest.open('xb') as target:
            while b:=f.read(1048576):h.update(b);target.write(b)
        after=os.fstat(f.fileno())
        if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise ValueError('Input changed during copy')
    checksum=h.hexdigest()
    if expected.get('sha256') and checksum!=expected['sha256']:raise ValueError('Input content changed')
    return {'path':name,'size':before.st_size,'sha256':checksum}

def unpack(bundle,target):
    target.mkdir(exist_ok=False)
    with zipfile.ZipFile(bundle) as z:
        for info in z.infolist():
            path=PurePosixPath(info.filename)
            if path.is_absolute() or '..' in path.parts or stat.S_ISLNK(info.external_attr>>16):raise ValueError('Invalid source archive path')
            p=target.joinpath(*path.parts)
            if info.is_dir():p.mkdir(parents=True,exist_ok=True);continue
            p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(z.read(info))
            if 'bin' in path.parts or p.suffix in ('.sh','.pl','.py'):p.chmod(0o700)

PATH_KEYS={'input','samples','sample_table','expression_matrix','counts','count_matrix','metadata','annotations','abundance','taxon_names','selected','universe','gmt','gene_map','gene2go','gene2ko','go_obo','hisat2_index','source_manifest','source_prediction_manifest','databases','fasta','gtf','host_fasta','hostremoval_reference','spaceranger_reference','transcript_fasta','config','scope_file','gff3','proteins','protein_fasta','tree','subfamily','ref_file','ref_root','pfam_dir','family_ids','label_file','path','fastq_1','fastq_2','read1','read2','r1','r2','fastq','fq1','fq2','image','transcripts','ligand_receptor','quality_table','peaks','regions','background_fasta','motifs','bigwigs','bam'}
PATH_MAP={}
def value_path(text,key,input_dir,workspace,original):
    if not isinstance(text,str):return text
    if key=='de_files' and text:return ','.join(value_path(item.strip(),'de_file',input_dir,workspace,original) for item in text.split(','))
    if text.startswith('@input:'):return str(contained(input_dir,text.split(':',1)[1]))
    if text.startswith('@output:'):return str(contained(workspace,text.split(':',1)[1]))
    candidate=original+'/'+text
    if candidate in PATH_MAP:return PATH_MAP[candidate]
    for origin,target in sorted(PATH_MAP.items(),key=lambda pair:-len(pair[0])):
        if text==origin or text.startswith(origin+'/'):
            suffix=text[len(origin):]
            if target.startswith('__main__:'):return str(contained(input_dir,target.split(':',1)[1]+suffix))
            return target+suffix
    if text.startswith('@'):
        match=re.fullmatch(r'@([a-z0-9_]+):(.+)',text)
        if not match:raise ValueError('Use @stage:relative-result for a previous step')
        marker=contained(workspace/'final',match.group(1)+'.manifest.json')
        record=json.loads(marker.read_text())
        if record.get('status') not in ('success','partial_success'):raise ValueError('Earlier step has not completed')
        root=Path(record['output_dir']).resolve()
        if workspace.resolve() not in root.parents:raise ValueError('Step result outside the current task')
        return str(contained(root,match.group(2)))
    if text.startswith(original+'/'):return str(contained(input_dir,text[len(original)+1:]))
    if Path(text).is_absolute():raise ValueError('A parameter points outside the selected input')
    if key in PATH_KEYS or key.endswith(('_file','_path','_dir','_fasta')):
        if text:return str(contained(input_dir,text))
    return text
def config_paths(value,input_dir,workspace,original,key=''):
    if isinstance(value,dict):return {k:config_paths(v,input_dir,workspace,original,k) for k,v in value.items() if k!='outdir'}
    if isinstance(value,list):return [config_paths(v,input_dir,workspace,original,key) for v in value]
    return value_path(value,key,input_dir,workspace,original)
def rewrite_sheets(input_dir,workspace,original):
    fields={'path','fastq_1','fastq_2','fasta','db_path','fastq','image','transcripts'}
    for p in [*input_dir.rglob('*.csv'),*input_dir.rglob('*.tsv')]:
        if p.stat().st_size>8*1048576:continue
        with p.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f,delimiter='\t' if p.suffix=='.tsv' else ',');header=reader.fieldnames or []
            if not fields.intersection(header):continue
            rows=list(reader)
        for row in rows:
            for key in fields.intersection(header):
                if row[key]:row[key]=value_path(row[key],key if key!='path' else 'input',input_dir,workspace,original)
        with p.open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=header,delimiter='\t' if p.suffix=='.tsv' else ',');writer.writeheader();writer.writerows(rows)

def prepare_documents(plan,configs,input_dir,workspace,original,first):
    documents=plan.get('configDocuments',{})
    for origin,document in documents.items():
        suffix=('.tsv' if document.get('delimiter')=='\t' else '.csv') if isinstance(document,dict) and document.get('__omaax_table__') else '.yaml'
        target=str(configs/('input-'+hashlib.sha256(origin.encode()).hexdigest()[:20]+suffix));previous=PATH_MAP.get(origin)
        if previous:
            for alias,value in list(PATH_MAP.items()):
                if value==previous:PATH_MAP[alias]=target
        PATH_MAP[origin]=target
    if not first:return
    for origin,document in documents.items():
        target=Path(PATH_MAP[origin])
        if isinstance(document,dict) and document.get('__omaax_table__'):
            rows=config_paths(document['rows'],input_dir,workspace,original)
            with target.open('x',encoding='utf-8',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=document['columns'],delimiter=document['delimiter']);writer.writeheader();writer.writerows(rows)
        else:
            document=config_paths(document,input_dir,workspace,original)
            strandness=plan.get('preferences',{}).get('library_strandness')
            if strandness and isinstance(document,dict) and isinstance(document.get('samples'),dict):
                document.setdefault('rnaseq',{})['library_strandness']=strandness
                for sample in document['samples'].values():
                    if isinstance(sample,dict):sample['library_strandness']=strandness
            target.write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')

def nextflow_command(real,kernel,args,config):
    allowed={'nf-core/scrnaseq':'nf-core_scrnaseq_4.2.0','nf-core/sopa':'nf-core_sopa_1.0.1','nf-core/taxprofiler':'nf-core_taxprofiler_2.0.1','nf-core/mag':'nf-core_mag_5.5.0','nf-core/atacseq':'nf-core_atacseq_2.1.2','nf-core/chipseq':'nf-core_chipseq_2.1.0','nf-core/cutandrun':'nf-core_cutandrun_3.2.2','nf-core/methylseq':'nf-core_methylseq_4.2.0'}
    if len(args)<2 or args[0]!='run' or args[1] not in allowed:raise ValueError('Only pinned Nextflow sources are allowed')
    cmd=[real,'-c',str(config),'run',str(kernel/'upstream_sources'/allowed[args[1]]),'-offline']
    rest=args[2:];i=0
    while i<len(rest):
        if rest[i]=='-r':i+=2;continue
        if rest[i]=='-profile':
            profiles=[p for p in rest[i+1].split(',') if p not in ('docker','podman','singularity','apptainer','conda','mamba','wave','awsbatch','azurebatch','test','test_full')]
            if profiles:cmd+=['-profile',','.join(profiles)]
            i+=2;continue
        if rest[i] in ('-params-file','-work-dir','-with-report','-with-trace') and i+1<len(rest):cmd+=rest[i:i+2];i+=2;continue
        raise ValueError('Unsupported Nextflow runtime option')
    return cmd

def install_nextflow_shim(kernel,project,plan):
    real=shutil.which('nextflow')
    if not real:return
    environment_file=os.environ.get('OMAAX_NEXTFLOW_ENVS')
    if environment_file:
        metadata=json.loads(Path(environment_file).read_text());mapping=metadata['environments']
        legacy={'nf-core_atacseq_2.1.2','nf-core_chipseq_2.1.0','nf-core_cutandrun_3.2.2','nf-core_methylseq_4.2.0'}
        os.environ['NXF_SYNTAX_PARSER']='v1' if metadata['upstream'].rsplit('/',1)[-1] in legacy else 'v2'
        # Bismark's fixed module controls workers; injected OpenMP workers corrupt its read ordering.
        if metadata['upstream'].rsplit('/',1)[-1]=='nf-core_methylseq_4.2.0':os.environ['OMP_NUM_THREADS']='1'
        pipeline=kernel/metadata['upstream'];config=pipeline/'nextflow.config'
        if config.is_file():config.write_text(re.sub(r"(id\s+['\"])nf-validation(['\"])",r'\g<1>nf-validation@1.1.3\2',config.read_text()))
        trim=pipeline/'modules/local/for_patch/trimgalore/main.nf'
        if trim.is_file():trim.write_text(re.sub(r'(emit:\s*\w+)\s+optional\s+(true|false)',r'\1, optional: \2',trim.read_text()))
        qc=pipeline/'assets/multiqc_config.yml'
        if qc.is_file():
            # The offline QC HTML keeps interactive plots without a browser renderer.
            text=re.sub(r'(?m)^export_plots:\s*true\s*$', 'export_plots: false',qc.read_text())
            if not re.search(r'(?m)^plots_force_interactive:',text):text+='\nplots_force_interactive: true\n'
            qc.write_text(text)
        default_environment=metadata.get('defaultEnvironment')
        if default_environment and (not default_environment.startswith(os.environ.get('OMAAX_OPT','/opt')+'/omaax/envs/') or not Path(default_environment).is_dir()):raise ValueError('Fixed default process environment is unavailable')
        for environment,destination in mapping.items():
            if not destination.startswith(os.environ.get('OMAAX_OPT','/opt')+'/omaax/envs/') or not Path(destination).is_dir():raise ValueError('Fixed Nextflow process environment is unavailable')
            modules=[kernel/metadata['upstream']/environment] if environment.endswith('.nf') else (kernel/'upstream_sources').glob('*/'+str(Path(environment).parent)+'/*.nf')
            for module in modules:
                text=module.read_text()
                text=re.sub(r'''(?m)^(\s*conda\s+)(["'])([^\n]*?)\2\s*$''',lambda m:m.group(1)+json.dumps(destination),text)
                module.write_text(text)
        cached=Path(os.environ.get('OMAAX_OPT','/opt'))/'omaax/nxf'
        if cached.is_dir():shutil.copytree(cached,Path(os.environ['NXF_HOME']),dirs_exist_ok=True)
    cfg=project/'omaax-nextflow.config'
    # Nextflow runs as local child processes INSIDE the already constrained task container.
    cfg.write_text("process.executor='local'\nprocess.container=null\ndocker.enabled=false\npodman.enabled=false\nsingularity.enabled=false\napptainer.enabled=false\nconda.enabled="+('true' if environment_file else 'false')+"\nwave.enabled=false\nfusion.enabled=false\nexecutor.queueSize=1\nprocess.cpus="+str(plan['resources']['cpu'])+"\n",encoding='utf-8')
    with cfg.open('a',encoding='utf-8') as handle:
        if environment_file and default_environment:handle.write('process.conda='+json.dumps(default_environment)+'\n')
        handle.write('process.resourceLimits=[cpus:'+str(plan['resources']['cpu'])+',memory:'+str(plan['resources'].get('memoryMb',2048))+'.MB,time:'+str(plan['resources'].get('timeoutSeconds',86400))+'.s]\n')
        handle.write("trace.fields='task_id,hash,name,status,exit,cpus,memory,realtime,peak_rss'\n")
        handle.write("params.max_cpus="+str(plan['resources']['cpu'])+"\nparams.max_memory='"+str(plan['resources'].get('memoryMb',2048))+".MB'\nparams.max_time='"+str(plan['resources'].get('timeoutSeconds',86400))+".s'\n")
    folder=project/'bin';folder.mkdir(exist_ok=True)
    # Existing cached minimal images have gzip but may not include its zcat helper.
    for name,options in [('zcat','-cd'),('gunzip','-d')]:
        if not shutil.which(name):
            gzip_binary=shutil.which('gzip')
            if not gzip_binary:raise ValueError('Fixed upstream environment is missing gzip')
            helper=folder/name;helper.write_text('#!/bin/sh\nexec '+shlex.quote(gzip_binary)+' '+options+' "$@"\n');helper.chmod(0o700)
    shim=folder/'nextflow'
    shim.write_text('#!'+sys.executable+'\nimport runpy,sys,os\nm=runpy.run_path('+repr(str(kernel/'omaax-adapter.py'))+')\nc=m["nextflow_command"]('+repr(real)+',m["Path"]('+repr(str(kernel))+'),sys.argv[1:],m["Path"]('+repr(str(cfg))+'))\nos.execv(c[0],c)\n')
    shim.chmod(0o700);os.environ['PATH']=str(folder)+os.pathsep+os.environ.get('PATH','')

def command(step,plan,kernel,input_dir,workspace,config_dir,original):
    sid=step['id'];values=plan['parameters'].get(sid,{})
    if step['kind']=='omics':
        supplied=dict(plan['configs'][sid])
        for key,auto in step.get('autoInputs',{}).items():
            if supplied.get(key)!='@auto':continue
            candidates=[value for value in plan['steps'][:plan['steps'].index(sid)] if value in auto['steps'] and (workspace/'final'/(value+'.manifest.json')).is_file()]
            if not candidates:
                if 'fallback' in auto and not any(value in auto['steps'] for value in plan['steps'][:plan['steps'].index(sid)]):supplied[key]=auto['fallback'];continue
                raise ValueError(step['name']+'缺少前置步骤结果，请补齐输入文件。')
            if auto.get('fromConfig'):
                supplied[key]=plan['configs'][candidates[-1]][auto['fromConfig']]
            else:supplied[key]='@'+candidates[-1]+':'+auto['artifact']
        path=config_dir/(sid+'.json');cfg=config_paths(supplied,input_dir,workspace,original);output=workspace/sid/'results';cfg['outdir']=str(output)
        cfg.pop('expected_outputs',None);action=step['contract']['action'];backend=kernel/'workflow_backends'/step['contract']['workflow'];save(path,cfg)
        if step.get('extensionStage'):
            cfg['cpu']=plan['resources']['cpu'];save(path,cfg)
            return [sys.executable,str(kernel/step['entry']),'--stage',sid,'--config',str(path)]
        if step.get('fixedNextflow'):
            effective=dict(cfg);profile=effective.pop('profile',None)
            if effective.get('igenomes_ignore') and 'igenomes_base' in step.get('configSchema',{}).get('properties',{}):effective['igenomes_base']=None
            if step['fixedNextflow']=='nf-core/mag' and effective.get('skip_binqc') and 'checkm_download_url' in step.get('configSchema',{}).get('properties',{}):effective['checkm_download_url']=None
            if step['fixedNextflow']=='nf-core/scrnaseq' and 'skip_emptydrops' in effective:
                previous=effective.pop('skip_emptydrops');effective.setdefault('skip_cellbender',previous)
            save(path,effective)
            cmd=['nextflow','run',step['fixedNextflow'],'-params-file',str(path),'-work-dir',str(workspace/sid/'nextflow'),'-with-report',str(workspace/sid/'nextflow-report.html'),'-with-trace',str(workspace/sid/'nextflow-trace.tsv')]
            if profile:cmd+=['-profile',str(profile)]
            return cmd
        if step['contract'].get('upstream'):
            effective=dict(cfg);effective.pop('profile',None);save(path,effective)
            return [sys.executable,str(backend/'run.py'),action,'--params',str(path),'--profile',str(cfg.get('profile') or 'docker'),'--work-dir',str(workspace/sid/'nextflow'),'--execute']
        if action in ('analyze','annotate','pseudobulk','summarize'):return [sys.executable,str(backend/'run.py'),action,'--config',str(path)]
        if action=='merge-bracken':return [sys.executable,str(backend/'run.py'),action,'--samples',cfg['samples'],'--outdir',str(output)]
        if action in ('export','enrich'):output.mkdir(parents=True,exist_ok=False)
        if action=='export':return [sys.executable,str(backend/'scripts/export_spatialdata.py'),'--input',cfg['input'],'--table',str(cfg['table']),'--counts-source',str(cfg['counts_source']),'--output',str(output/'exported.h5ad')]
        if action=='enrich':
            cmd=[sys.executable,str(backend/'scripts/enrichment.py'),'--selected',cfg['selected'],'--universe',cfg['universe'],'--gmt',cfg['gmt'],'--output',str(output/'enrichment.tsv')]
            for key in ('gene_map','min_size','max_size'):
                if cfg.get(key) is not None:cmd+=['--'+key.replace('_','-'),str(cfg[key])]
            return cmd
        if action=='de':return ['Rscript',str(backend/'scripts/pseudobulk_deseq2.R'),cfg['counts'],cfg['metadata'],str(cfg['control']),str(cfg['treated']),str(output),'paired' if cfg.get('paired',False) else 'unpaired']
        if action=='differential':return ['Rscript',str(backend/'scripts/differential_abundance.R'),cfg['counts'],cfg['samples'],str(cfg['control']),str(output)]
        if action=='community':return ['Rscript',str(backend/'scripts/community_stats.R'),cfg['abundance'],cfg['samples'],str(output)]
        raise ValueError('Unsupported fixed backend action')
    cmd=[sys.executable,str(kernel/step['entry'])]
    managed={'input':str(input_dir),'input_dir':str(input_dir),'output':str(workspace/sid),'output_dir':str(workspace/sid),'outdir':str(workspace/sid),'workspace':str(workspace),'prefix':plan['prefix'],'cpu':plan['resources']['cpu'],'threads':plan['resources']['cpu'],'job_id':plan['id'],'run_id':plan['id'],'log_file':str(workspace/(sid+'.log')),'final_dir':str(workspace/'final'),'scripts_dir':str(kernel/'scripts'),'stage':sid}
    if (sid=='rnaseq_align' and 'rnaseq_qc' in plan['steps']) or (sid=='rnaseq_quant' and 'rnaseq_align' in plan['steps']):managed['input']=str(workspace/'final')
    if sid=='rnaseq_de' and 'rnaseq_quant' in plan['steps']:managed['input']=str(workspace/'final')
    if sid in ('rnaseq_go_enrich','rnaseq_kegg_enrich','rnaseq_enrich','enrichment') and 'rnaseq_de' in plan['steps']:
        managed['input']=str(workspace/'final');managed['input_dir']=str(workspace/'final')
    for a in step['arguments']:
        flag=next((f for f in a['flags'] if f.startswith('--')),a['flags'][0]);key=a['key']
        if a['managed']:
            if key not in managed:continue
            v=managed[key]
        elif key in values:v=[value_path(item,key,input_dir,workspace,original) for item in values[key]] if isinstance(values[key],list) else value_path(values[key],key,input_dir,workspace,original)
        else:continue
        if v is None or v=='' or v==[]:continue
        if a.get('action')=='boolean_optional':
            cmd.append(flag if v else '--no-'+flag.removeprefix('--'))
        elif a.get('action')=='store_true':
            if v:cmd.append(flag)
        elif a.get('action')=='store_false':
            if not v:cmd.append(flag)
        elif isinstance(v,list):
            if a.get('action')=='append':
                for item in v:cmd+=[flag,str(item)]
            else:cmd+=[flag,*[str(x) for x in v]]
        elif isinstance(v,str) and v.startswith('-'):cmd.append(flag+'='+v)
        else:cmd+=[flag,str(v)]
    return cmd

def main():
    source=Path(runtime_path('/input/'+sys.argv[1]));expected=sys.argv[2];out=Path(os.environ.get('OMAAX_OUTPUT','/output'))
    p=source/'plan.json'
    if sha(p)!=expected:raise ValueError('Plan fingerprint mismatch')
    plan=json.loads(p.read_text());bundle=source/'kernel.zip'
    if sha(bundle)!=plan['bundleHash']:raise ValueError('Workflow source fingerprint mismatch')
    only=sys.argv[3] if len(sys.argv)>3 else None
    if only is not None and only not in plan['steps']:raise ValueError('Step is not in the confirmed plan')
    manifest={'state':'running','planDigest':expected,'workflow':plan['workflow'],'version':plan['version'],'validationMode':'execution_only','steps':[],'files':[]}
    progress=out/'workflow-progress.json';result=out/'workflow-result.json'
    kernel=out/'_workflow_source';project=out/'analysis';workspace=project/'workspace'/plan['id'];input_dir=project/'input'/plan['id'];configs=project/'configs'
    try:
        first=only is None or only==plan['steps'][0]
        if first:
            unpack(bundle,kernel);workspace.mkdir(parents=True,exist_ok=False);input_dir.mkdir(parents=True,exist_ok=False);configs.mkdir();(workspace/'final').mkdir()
            if (kernel/'scripts').is_dir():(project/'scripts').symlink_to(os.path.relpath(kernel/'scripts',project),target_is_directory=True)
            mounted=contained(Path(runtime_path('/input/'+plan['source'])),plan['input']);copied=[]
            manifest['steps']=[{'id':sid,'state':'waiting'} for sid in plan['steps']];save(progress,manifest)
            for name,s in plan['inputs'].items():
                source=Path(runtime_path(s['readPath'])) if s.get('readPath') else None
                copied.append(copy_input(source.parent if source else mounted,source.name if source else name,contained(input_dir,name),s)|{'path':name})
            save(out/'input-fingerprints.json',copied)
        else:
            manifest=json.loads(progress.read_text())
            if manifest.get('planDigest')!=expected or any(row['state']!='complete' for row in manifest['steps'][:plan['steps'].index(only)]):raise ValueError('Earlier workflow steps are not complete')
        task_tmp=Path(os.environ.get('TMPDIR','/tmp'));task_tmp.mkdir(parents=True,exist_ok=True)
        os.environ.update(PLANTAI_PROJECT_ROOT=str(project),BBAV5_PROJECT_ROOT=str(project),PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1',MPLBACKEND='Agg',MPLCONFIGDIR=str(task_tmp/'omaax-mpl'),NUMBA_CACHE_DIR=str(task_tmp/'omaax-numba'),XDG_CACHE_HOME=str(task_tmp/'omaax-cache'),NXF_HOME=str(task_tmp/'omaax-nxf'),NXF_OFFLINE='true')
        for variable in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):os.environ[variable]=str(plan['resources']['cpu'])
        os.environ['NXF_OPTS']='-XX:ActiveProcessorCount='+str(plan['resources']['cpu'])
        # Source CLI paths follow this step's approved image, never a host conda installation.
        os.environ.update(PLANTAI_RNASEQ_BIN=str(Path(sys.executable).parent),PLANTAI_RNAR_BIN=str(Path(sys.executable).parent),CONDA_PREFIX=sys.prefix)
        interproscan=shutil.which('interproscan.sh') or shutil.which('interproscan')
        if interproscan:os.environ['INTERPROSCAN_BIN']=interproscan
        original=str(Path(plan['sourceBase'])/plan['input'])
        PATH_MAP.update({key:runtime_path(value) for key,value in plan.get('pathMap',{}).items()})
        prepare_documents(plan,configs,input_dir,workspace,original,first)
        if first:rewrite_sheets(input_dir,workspace,original)
        by_id={s['id']:s for f in json.loads((kernel/'omaax/catalog.json').read_text())['workflows'] for s in f['nodes']+f['extraNodes']}
        # The adapter is supplied in the trusted payload; persist only its known functions for the shim.
        (kernel/'omaax-adapter.py').write_text('import sys,os\nfrom pathlib import Path\n'+NEXTFLOW_FUNCTION_SOURCE,encoding='utf-8')
        install_nextflow_shim(kernel,project,plan)
        for row in manifest['steps']:
            if only is not None and row['id']!=only:continue
            row.update(state='running',started=time.time());save(progress,manifest)
            step=by_id[row['id']];args=command(step,plan,kernel,input_dir,workspace,configs,original)
            print(json.dumps({'step':step['name'],'parameters':plan['parameters'].get(step['id'],{}),'config':plan['configs'].get(step['id'],{})},ensure_ascii=False),flush=True)
            env=dict(os.environ,PYTHONPATH=str(kernel)+os.pathsep+os.environ.get('PYTHONPATH',''))
            with (workspace/(row['id']+'.stdout.log')).open('wb') as log:
                process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,cwd=workspace,env=env)
                while b:=process.stdout.read(65536):
                    log.write(b);log.flush();sys.stdout.buffer.write(b);sys.stdout.buffer.flush()
                code=process.wait()
            row.update(state='complete' if code==0 else 'failed',exitCode=code,finished=time.time(),software=step['software'])
            if code==0 and step['kind']=='omics':
                save(workspace/'final'/(step['id']+'.manifest.json'),{'stage':step['id'],'status':'success','output_dir':str(workspace/step['id']/'results'),'parameters':plan['configs'][step['id']]})
            save(progress,manifest)
            if code:raise RuntimeError(step['name']+'执行失败，查看日志了解软件提示')
        manifest['state']='complete' if all(s['state']=='complete' for s in manifest['steps']) else 'running'
    except BaseException as error:
        manifest.update(state='failed',error=str(error))
        for row in manifest['steps']:
            if row['state']=='running':row.update(state='failed',error=str(error))
            elif row['state']=='waiting':row['state']='skipped'
        raise
    finally:
        if workspace.exists() and manifest['state']!='running':
            for file in sorted(workspace.rglob('*')):
                if file.is_file() and not file.is_symlink():manifest['files'].append({'path':file.relative_to(out).as_posix(),'size':file.stat().st_size,'sha256':sha(file)})
        save(progress,manifest);save(result,manifest)

_source=globals().get('OMAAX_ADAPTER_SOURCE') or Path(__file__).read_text(encoding='utf-8')
NEXTFLOW_FUNCTION_SOURCE=next(ast.get_source_segment(_source,n) for n in ast.parse(_source).body if isinstance(n,ast.FunctionDef) and n.name=='nextflow_command')
if __name__=='__main__':main()
