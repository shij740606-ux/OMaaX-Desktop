"""Prepare only requested fixed steps, in new rootless images; never alter host environments."""
import hashlib,json,os,platform,re,shlex,shutil,subprocess,sys,tarfile,time,urllib.request,zipfile
from pathlib import Path

DEPENDENCIES=json.loads(Path(__file__).with_name('workflow-dependencies.json').read_text(encoding='utf-8')) if '__file__' in globals() and Path(__file__).with_name('workflow-dependencies.json').is_file() else {}
RUNTIME_VERSION='micromamba-2.3.3-minimal-v5'
PACKAGE_NAMES={
 'Bio':'biopython','yaml':'pyyaml','PIL':'pillow','sklearn':'scikit-learn',
 'hmmsearch':'hmmer','blastp':'blast','featureCounts':'subread','MCScanX':'mcscanx',
 'KaKs_Calculator':'kaks_calculator','java':'openjdk=17','iqtree':'iqtree','interproscan':'interproscan',
 'DESeq2':'bioconductor-deseq2','WGCNA':'r-wgcna','clusterProfiler':'bioconductor-clusterprofiler',
 'fgsea':'bioconductor-fgsea','ANCOMBC':'bioconductor-ancombc','vegan':'r-vegan',
 'ggplot2':'r-ggplot2','pheatmap':'r-pheatmap','ggtree':'bioconductor-ggtree',
 'Rscript':'r-base','ape':'r-ape','patchwork':'r-patchwork','dplyr':'r-dplyr','fimo':'meme',
 'skimage':'scikit-image>=0.22,<1','adjustText':'adjusttext','igraph':'python-igraph>=0.11,<2',
 'scanpy':'scanpy=1.12.4','squidpy':'squidpy=1.8.3','anndata':'anndata>=0.10,<0.13',
 'numpy':'numpy>=1.26,<3','pandas':'pandas>=2.2,<3','scipy':'scipy>=1.12,<2','matplotlib':'matplotlib>=3.8,<4',
 'harmonypy':'harmonypy=0.0.10','tximport':'bioconductor-tximport',
 'exec_annotation':'kofamscan',
 'ame':'meme','computeMatrix':'deeptools','plotHeatmap':'deeptools','plotProfile':'deeptools','DSS':'bioconductor-dss',
}
R_PACKAGES={name:package for name,package in PACKAGE_NAMES.items() if package.startswith(('r-','bioconductor-'))}
R_PACKAGES.update({'igraph':'r-igraph','Biostrings':'bioconductor-biostrings','msa':'bioconductor-msa','treeio':'bioconductor-treeio','AnnotationDbi':'bioconductor-annotationdbi','AnnotationHub':'bioconductor-annotationhub','biomaRt':'bioconductor-biomart'})

def upstream_selection(step,configs=None):
    props=step.get('configSchema',{}).get('properties',{});given={**step.get('configDefaults',{}),**(configs or {})}
    if 'skip_emptydrops' in given and 'skip_cellbender' not in given:given['skip_cellbender']=given['skip_emptydrops']
    route={key:given.get(key,prop.get('default')) for key,prop in props.items() if key in ('aligner','peakcaller','profile','technology','refine_bins_dastool','bbnorm') or key.startswith(('run_','skip_','use_','perform_'))}
    result={key:value for key,value in route.items() if value is not None}
    result['_pipeline']=step.get('nextflowSource','')
    result['_selector_version']=5 if 'atacseq_' in result['_pipeline'] else 4 if any(name in result['_pipeline'] for name in ('mag_','cutandrun_')) else 3
    if 'aligner' in result and props.get('aligner',{}).get('enum') and result['aligner'] not in props['aligner']['enum']:raise ValueError('请选择此固定流程声明的比对软件。')
    return result

def process_selected(relative,route):
    """Omit unselected alternative aligners/profilers, retaining common process dependencies."""
    parts=set(Path(relative).parts)
    groups={'bwa':{'bwa','bwamem2'},'bowtie2':{'bowtie2'},'chromap':{'chromap'},'star':{'star','starsolo','star_align','star_genomegenerate','star_genomeparams_upgrade'},'hisat2':{'hisat2'},'minimap2':{'minimap2'},'kallisto':{'kallisto','bustools','kallistobustools'},'salmon':{'salmon','simpleaf','piscem','alevin','alevinfry'},'cellranger':{'cellranger','cellrangerarc','cellrangermulti','parse_cellrangermulti_samplesheet'},'bismark':{'bismark','bowtie2'},'bismarkhisat':{'bismark','hisat2'},'bwameth':{'bwameth','bwa','methyldackel'},'rastair':{'rastair','bwa'},'parabricks':{'parabricks'}}
    aligner=str(route.get('aligner','')).lower().replace('-','').replace('_','')
    aliases={'bwamem':'bwa','bwamem2':'bwa','simpleaf':'salmon','alevin':'salmon','starsolo':'star','bwameth':'bwameth','bismark':'bismark','bwa':'bwa'}
    aligner=aliases.get(aligner,aligner)
    if 'methylseq' in route.get('_pipeline','') and str(route.get('aligner'))=='bwamem':aligner='rastair'
    alternatives=set().union(*groups.values())
    if aligner in groups and parts & alternatives and not parts & groups[aligner]:return False
    peakcaller=route.get('peakcaller')
    if peakcaller in ('macs2','macs3','seacr') and parts & {'macs2','macs3','seacr'} and peakcaller not in parts:return False
    for key,value in route.items():
        if key.startswith('run_') and value is False and key[4:] in parts:return False
        if key.startswith('skip_') and value is True and any(part==key[5:] or part.startswith(key[5:]+'_') for part in parts):return False
        if key.startswith('use_') and value is False and any(part==key[4:] or part.endswith('_'+key[4:]) for part in parts):return False
    if route.get('skip_picard_metrics') and 'collectmultiplemetrics' in parts:return False
    if route.get('skip_trimming') and parts & {'trimgalore','adapterremoval','fastp'}:return False
    if route.get('skip_plot_profile') and parts & {'computematrix','plotheatmap','plotprofile'}:return False
    if route.get('skip_plot_fingerprint') and 'plotfingerprint' in parts:return False
    if route.get('skip_spp') and 'phantompeakqualtools' in parts:return False
    if route.get('skip_ataqv') and 'ataqv' in parts:return False
    if 'sopa' in route.get('_pipeline',''):
        if route.get('technology')!='visium_hd' and 'spaceranger' in parts:return False
        if route.get('use_fluorescence_annotation') is False and 'fluo_annotation' in parts:return False
        if route.get('use_scanpy_preprocessing') is False and 'scanpy_preprocess' in parts:return False
    if 'taxprofiler' in route.get('_pipeline',''):
        disabled={'perform_longread_qc':{'nanoplot','filtlong','porechop','chopper'},'perform_longread_hostremoval':{'minimap2'},'perform_shortread_hostremoval':{'bowtie2'},'perform_shortread_redundancyestimation':{'nonpareil'},'perform_shortread_complexityfilter':{'bbduk','prinseqplusplus'},'perform_runmerging':{'cat'},'run_malt':{'megan'},'run_profile_standardisation':{'taxpasta'}}
        if any(route.get(key) is False and parts & names for key,names in disabled.items()):return False
    if 'mag_' in route.get('_pipeline',''):
        if route.get('skip_binqc') and parts & {'busco','checkm','checkm2','gunc'}:return False
        if not route.get('refine_bins_dastool',False) and parts & {'das_tool','dastool'}:return False
        if not route.get('bbnorm',False) and 'bbnorm' in parts:return False
    if 'methylseq' in route.get('_pipeline',''):
        if not route.get('run_targeted_sequencing',False) and parts & {'addorreplacereadgroups','bedtointervallist','collecthsmetrics'}:return False
        if route.get('run_qualimap') is False and 'qualimap' in parts:return False
    return True

def requirements(step,parameters=None,configs=None):
    spec={kind:list(names) for kind,names in DEPENDENCIES.get(step['id'],{}).items()}
    parameters=parameters or {}
    configs=configs or {}
    if step['id']=='rnaseq_qc' and parameters.get('qc_tool')=='cutadapt':spec['tools']=['cutadapt']
    if step['id']=='rnaseq_quant':
        spec['tools']=list(dict.fromkeys(spec.get('tools',[])+['samtools']))
        engine=parameters.get('abundance_engine','auto')
        if parameters.get('quant_mode','dual')!='count_only' and engine!='none':
            spec['tools'].append('kallisto' if engine=='kallisto' else 'salmon');spec.setdefault('r',[]).append('tximport')
    if step['id']=='annotation':
        if not parameters.get('prefer_bioc_gene2go'):spec.pop('r',None)
        if parameters.get('skip_go'):spec['tools']=[n for n in spec.get('tools',[]) if n!='interproscan'];spec.pop('r',None)
        if parameters.get('skip_kegg'):spec['tools']=[n for n in spec.get('tools',[]) if n not in ('exec_annotation','hmmsearch')]
    if step['id']=='singlecell_analyze':
        if configs.get('integration')=='harmony':spec.setdefault('modules',[]).append('harmonypy')
        if configs.get('doublets',{}).get('enabled'):spec.setdefault('modules',[]).append('scrublet')
    if step.get('nextflowSource'):
        route=upstream_selection(step,configs)
        if 'cellranger' in str(route.get('aligner','')).lower():spec['services']=['Cell Ranger 需自行配置符合其许可证的程序；可选择其他固定比对路线']
        if route:
            key=step['nextflowSource']+'#'+hashlib.sha256(json.dumps(route,sort_keys=True).encode()).hexdigest()[:16]
            spec['processEnvironments']=[key]
    return spec

def packages(spec):
    result={'python=3.12','pyyaml'}
    for kind,names in spec.items():
        if kind in ('services','processEnvironments'):continue
        for name in names:
            if kind=='r':result.add('r-base' if name in ('grid','stats','utils','methods','parallel','graphics','grDevices','splines') else R_PACKAGES.get(name,'r-'+name.lower()))
            else:result.add(PACKAGE_NAMES.get(name,name))
    return sorted(result)

def combined(specs):
    kinds={key for spec in specs for key in spec}
    return {key:sorted({name for spec in specs for name in spec.get(key,[])}) for key in kinds}

def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS workflow_environment_tasks(id TEXT PRIMARY KEY, workflow TEXT NOT NULL, request TEXT NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL, created REAL, updated REAL)')
    db.commit()

def status(r,workflow):
    db=r.database();tables(db)
    row=db.execute('SELECT * FROM workflow_environment_tasks WHERE workflow=? ORDER BY created DESC LIMIT 1',(workflow,)).fetchone()
    if not row:return {'state':'idle','workflow':workflow,'detail':'选择步骤后，检查并配置所需环境。'}
    result=dict(row);result['request']=json.loads(result['request'])
    pid=result['request'].get('_pid')
    if row['state'] in ('queued','running') and type(pid) is int and time.time()-row['updated']>6:
        try:alive=(b'--prepare\0'+row['id'].encode()) in Path('/proc/'+str(pid)+'/cmdline').read_bytes()
        except OSError:alive=False
        if not alive:
            detail='安装进程已结束但没有完成记录，请重新检查并配置；原有环境保持不变。'
            update(db,row['id'],'failed',detail);result.update(state='failed',detail=detail)
    directory=r.root()/'workflow-environments'/r.identifier(row['id']);log=directory/'install.log'
    result['log']=''
    if log.is_file():
        log=r.contained(directory,'install.log')
        with log.open('rb') as handle:handle.seek(max(0,log.stat().st_size-12000));result['log']=handle.read().decode('utf-8','replace')
    return result

def validate_recipes(recipes,steps):
    if not isinstance(recipes,dict) or set(recipes)-{step['id'] for step in steps}:raise ValueError('安装方案必须对应本次选定步骤')
    for recipe in recipes.values():
        if not isinstance(recipe,dict) or set(recipe)-{'packages','channels','script'}:raise ValueError('安装方案字段无效')
        for key in ('packages','channels'):
            values=recipe.get(key,[])
            if not isinstance(values,list) or len(values)>256 or any(not isinstance(v,str) or not v or len(v)>512 or v.startswith('-') or any(c in v for c in ('\n','\r','\x00')) for v in values):raise ValueError('安装软件或来源格式无效')
        script=recipe.get('script','')
        if not isinstance(script,str) or len(script.encode())>131072 or '\x00' in script:raise ValueError('安装脚本无效或过大')
    return recipes

def start(r,body):
    import workflow_engine as w
    w.no_extra(body,{'workflow','steps','parameters','configs','recipes'})
    flow=w.flow_for(body.get('workflow'));steps=w.select_steps(flow,body.get('steps'))
    recipes=validate_recipes(body.get('recipes',{}),steps)
    w.safe_values(body.get('parameters',{}));w.safe_values(body.get('configs',{}))
    r.protection();w.ready_bundle(r)
    db=r.database();tables(db)
    status(r,flow['id'])
    # Serialize allocation across windows so one click cannot launch duplicate installers.
    db.execute('BEGIN IMMEDIATE')
    running=db.execute("SELECT id,request FROM workflow_environment_tasks WHERE workflow=? AND state IN ('queued','running')",(flow['id'],)).fetchone()
    if running:
        db.commit();active=json.loads(running['request'])
        if active.get('steps')!=[s['id'] for s in steps] or active.get('parameters',{})!=body.get('parameters',{}) or active.get('configs',{})!=body.get('configs',{}) or active.get('recipes',{})!=recipes:raise ValueError('此流程正在配置其他步骤或参数，请等待完成后再为新的选择生成计划。')
        return status(r,flow['id'])
    job=r.uuid.uuid4().hex;directory=r.root()/'workflow-environments'/job
    directory.mkdir(parents=True,mode=0o700,exist_ok=False)
    request={'workflow':flow['id'],'steps':[s['id'] for s in steps],'parameters':body.get('parameters',{}),'configs':body.get('configs',{}),'recipes':recipes}
    now=time.time();db.execute('INSERT INTO workflow_environment_tasks VALUES(?,?,?,?,?,?,?)',(job,flow['id'],json.dumps(request),'queued','等待配置所选步骤的环境',now,now));db.commit()
    try:child=launch(r,job)
    except Exception as error:
        detail="环境安装进程启动失败："+str(error)
        (directory/"install.log").write_text(detail,encoding="utf-8")
        update(db,job,"failed",detail)
        return status(r,flow["id"])
    if child is not None and type(child.pid) is int:
        request['_pid']=child.pid;db.execute('UPDATE workflow_environment_tasks SET request=? WHERE id=?',(json.dumps(request),job));db.commit()
    return status(r,flow['id'])

def launch(r,job):
    return subprocess.Popen([sys.executable,str(Path(r.__file__).resolve()),'--prepare',job],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)

def update(db,job,state,detail):
    db.execute('UPDATE workflow_environment_tasks SET state=?,detail=?,updated=? WHERE id=?',(state,detail,time.time(),job));db.commit()

def build_context(w,r,directory,step,spec,model_recipe=None):
    """Network is allowed only while preparing software, with no input-data mount."""
    model_recipe=model_recipe or {}
    requested=model_recipe.get('packages',packages(spec));upstream=step.get('nextflowSource')
    requested=list(dict.fromkeys(['python','pyyaml',*requested]))
    if upstream:requested+=['conda','nextflow','openjdk=17']
    recipe='FROM scratch\nCOPY rootfs /\nENV MAMBA_ROOT_PREFIX=/opt/mamba PATH=/opt/conda/bin:/usr/local/bin:/usr/bin:/bin SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt\n'
    channels=model_recipe.get('channels') or ['conda-forge','bioconda']
    recipe+='RUN micromamba create -y -p /opt/conda --override-channels '+ ' '.join('-c '+shlex.quote(value) for value in channels)+' '+ ' '.join(shlex.quote(value) for value in requested)+' && micromamba clean --all --yes\n'
    model_commands=''
    if model_recipe.get('script'):
        (directory/'model-install.sh').write_text(model_recipe['script'],encoding='utf-8')
        model_commands='COPY model-install.sh /opt/omaax/model-install.sh\nRUN /bin/bash -eu /opt/omaax/model-install.sh\n'
    if upstream:
        # Each nf-core process keeps its original dependency versions in a separate environment.
        with zipfile.ZipFile(w.bundle_path(r)) as source:
            prefix=(upstream if upstream.startswith('upstream_sources/') else 'upstream_sources/'+upstream).rstrip('/')+'/'
            for item in source.namelist():
                if item.startswith(prefix) and (item.endswith('environment.yml') or item.endswith('.config') or item.endswith('.nf')) and process_selected(item.removeprefix(prefix),step.get('environmentSelection',{})):
                    target=directory/'upstream'/item.removeprefix(prefix)
                    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.read(item))
        recipe+='COPY upstream /opt/omaax/upstream\nCOPY prepare-nextflow.py /opt/omaax/prepare-nextflow.py\n'+model_commands+'RUN python3 /opt/omaax/prepare-nextflow.py\nENV OMAAX_NEXTFLOW_ENVS=/opt/omaax/nextflow-environments.json\n'
        (directory/'prepare-nextflow.py').write_text(NEXTFLOW_PREPARE.replace('UPSTREAM_ID',repr(upstream)).replace('PROCESS_KEY',repr(spec.get('processEnvironments',[upstream])[0])),encoding='utf-8')
    else:recipe+=model_commands
    recipe+='RUN python3 -c "import ssl,sqlite3,ctypes,yaml" && micromamba list -p /opt/conda --explicit > /opt/omaax-environment-lock.txt\nENTRYPOINT []\nUSER root\n'
    (directory/'Containerfile').write_text(recipe,encoding='utf-8')
    (directory/'requirements.json').write_text(json.dumps({'step':step['id'],'packages':requested,'requirements':spec},indent=2),encoding='utf-8')
    return recipe

def prepare_rootfs(directory):
    """Copy public system executables read-only, then install software only inside the new image."""
    rootfs=directory/'rootfs';rootfs.mkdir(mode=0o700)
    def copy(source,destination):
        target=rootfs/destination.lstrip('/');target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    for name in ['bash','sh','env','uname','cat','mkdir','rm','cp','ln','mv','ls','chmod','head','tail','sort','sed','grep','awk','date','dirname','readlink','pwd','touch','tee','sleep','tar','gzip','bzip2','xz','find','which','tr','wc','ps','mktemp','basename','cut','xargs','uniq','paste','seq','hostname','df','du','md5sum','sha256sum','timeout','realpath','stat','comm','split','rmdir','nproc','id','whoami','expr','printf']:
        source=next((Path(folder)/name for folder in ['/usr/bin','/bin'] if (Path(folder)/name).is_file()),None)
        if source is None:continue
        copy(source,str(source))
        # Standard library paths only; never follow links to user or project directories.
        p=subprocess.run(['/usr/bin/ldd',str(source)],capture_output=True,text=True,timeout=10)
        for value in re.findall(r'(/[^\s()]+)',p.stdout):
            library=Path(value)
            if library.is_file() and str(library.resolve()).startswith(('/usr/lib/','/lib/','/lib64/')):copy(library,value)
    for source,target in [('/usr/bin/bash','/bin/bash'),('/usr/bin/dash','/bin/sh')]:
        if Path(source).is_file():copy(Path(source),target)
    # Older conda binaries still link the compatibility libraries merged into modern glibc.
    for name in ['libutil.so.1','libdl.so.2','librt.so.1','libpthread.so.0','libm.so.6','libc.so.6','libresolv.so.2','libnss_dns.so.2','libnss_files.so.2','libanl.so.1','libgcc_s.so.1','libstdc++.so.6','libcrypt.so.1']:
        for folder in ['/lib','/lib64','/usr/lib']:
            for library in list(Path(folder).glob(name))+list(Path(folder).glob('*-linux-gnu/'+name)):
                if library.is_file():copy(library,str(library))
    certificate=Path('/etc/ssl/certs/ca-certificates.crt')
    if not certificate.is_file():raise ValueError('服务器缺少公共 CA 证书，请管理员配置 ca-certificates')
    copy(certificate,str(certificate))
    architecture={'x86_64':'linux-64','aarch64':'linux-aarch64'}.get(platform.machine())
    if not architecture:raise ValueError('目前自动配置支持 Linux x86_64 / aarch64 服务器')
    url='https://micro.mamba.pm/api/micromamba/'+architecture+'/2.3.3'
    archive=directory/'micromamba.tar.bz2';opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url,timeout=90) as response,archive.open('xb') as target:
        size=0
        while chunk:=response.read(1048576):
            size+=len(chunk)
            if size>32*1048576:raise ValueError('环境配置程序下载超过预期大小')
            target.write(chunk)
    with tarfile.open(archive) as package:
        entry=package.getmember('bin/micromamba')
        if not entry.isfile():raise ValueError('官方配置程序格式不正确')
        binary=rootfs/'bin/micromamba';binary.parent.mkdir(parents=True,exist_ok=True)
        with package.extractfile(entry) as source,binary.open('xb') as target:shutil.copyfileobj(source,target)
        binary.chmod(0o755)
    p=subprocess.run(['/usr/bin/ldd',str(binary)],capture_output=True,text=True,timeout=10)
    for value in re.findall(r'(/[^\s()]+)',p.stdout):
        library=Path(value)
        if library.is_file() and str(library.resolve()).startswith(('/usr/lib/','/lib/','/lib64/')):copy(library,value)
    (rootfs/'tmp').mkdir(mode=0o1777,exist_ok=True)
    (rootfs/'etc/passwd').write_text('root:x:0:0:OMaaX:/tmp:/bin/bash\n',encoding='utf-8')
    (rootfs/'etc/group').write_text('root:x:0:\n',encoding='utf-8')
    (directory/'bootstrap-source.json').write_text(json.dumps({'url':url,'version':'2.3.3','archiveSha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'noProxy':True}),encoding='utf-8')

NEXTFLOW_PREPARE=r'''import hashlib,json,pathlib,re,subprocess,yaml,os,shlex
base=pathlib.Path('/opt/omaax/upstream');records={};seen={}
def compatible_dependencies(values):
 deps=list(values)
 if any(value in ('deeptools=3.5.1','deeptools=3.5.*') for value in deps):deps+=['matplotlib<3.8','scipy<1.11']
 if 'multiqc=1.13' in deps:deps+=['python=3.10','setuptools<82']
 return deps
def install(normalized,pip,key):
 destination='/opt/omaax/envs/'+key
 if key not in seen:
  normalized=compatible_dependencies(normalized)
  if pip:normalized+=['python=3.11','pip']
  subprocess.run(['micromamba','create','-y','-p',destination,'--override-channels','-c','conda-forge','-c','bioconda',*normalized],check=True)
  if pip:subprocess.run([destination+'/bin/python','-m','pip','install',*pip],check=True)
  if any(value in ('deeptools=3.5.1','deeptools=3.5.*') for value in normalized):subprocess.run([destination+'/bin/bamCoverage','--version'],check=True)
  if 'multiqc=1.13' in normalized:subprocess.run([destination+'/bin/multiqc','--version'],check=True)
  seen[key]=destination
 return destination
for file in sorted(base.rglob('environment.yml')):
 raw=file.read_bytes();key=hashlib.sha256(raw).hexdigest();destination='/opt/omaax/envs/'+key
 if key not in seen:
  cfg=yaml.safe_load(raw);deps=cfg.get('dependencies',[])
  if not isinstance(deps,list):raise ValueError('Invalid pinned process environment')
  normalized=[];pip=[]
  for dep in deps:
   if isinstance(dep,str):normalized.append(dep.split('::')[-1])
   elif isinstance(dep,dict) and set(dep)=={'pip'}:pip+=dep['pip']
   else:raise ValueError('Unsupported fixed environment dependency')
  install(normalized,pip,key)
 records[file.relative_to(base).as_posix()]=destination
for file in sorted(base.rglob('*.nf')):
 for match in re.finditer(r"""(?m)^\s*conda\s+(["'])([^\n]*?)\1\s*$""",file.read_text()):
  declaration=match.group(2)
  if '${moduleDir}/environment.yml' in declaration:continue
  if '$' in declaration or '/' in declaration:raise ValueError('Unsupported dynamic fixed conda declaration: '+file.as_posix())
  normalized=[value.split('::')[-1] for value in shlex.split(declaration)]
  if not normalized:continue
  key=hashlib.sha256(json.dumps(normalized).encode()).hexdigest()
  records[file.relative_to(base).as_posix()]=install(normalized,[],key)
plugins=set()
for file in base.rglob('*.config'):
 plugins.update(re.findall(r"id\s+['\"]([a-z0-9-]+@[a-zA-Z0-9_.-]+)['\"]",file.read_text()))
 if re.search(r"id\s+['\"]nf-validation['\"]",file.read_text()):plugins.add('nf-validation@1.1.3')
os.environ['NXF_HOME']='/opt/omaax/nxf'
for plugin in sorted(plugins):subprocess.run(['nextflow','plugin','install',plugin],check=True)
pathlib.Path('/opt/omaax/nextflow-environments.json').write_text(json.dumps({'upstream':UPSTREAM_ID,'processKey':PROCESS_KEY,'environments':records,'plugins':sorted(plugins),'defaultEnvironment':records.get('dev/docker/master_local_module_image/environment.yml'),'runtimeVersion':'micromamba-2.3.3-minimal-v5'}))
subprocess.run(['micromamba','clean','--all','--yes'],check=True)
'''

def run(r,job):
    import fcntl
    import workflow_engine as w
    db=r.database();tables(db);row=db.execute('SELECT * FROM workflow_environment_tasks WHERE id=?',(r.identifier(job),)).fetchone()
    if not row or row['state'] not in ('queued','running'):return
    directory=r.root()/'workflow-environments'/job
    with (directory/'lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        request=json.loads(row['request']);flow=w.flow_for(request['workflow'])
        update(db,job,'running','正在检查所选步骤的软件')
        with (directory/'install.log').open('ab') as log:
            try:
                r.protection();steps=w.select_steps(flow,request['steps']);cfg=r.config(db)
                for step in steps:
                    if requirements(step,request['parameters'].get(step['id'],{}),request['configs'].get(step['id'],{})).get('services'):raise ValueError(step['name']+'需要外部在线服务，安装软件不能解除该限制。请取消此步骤或使用已有结果。')
                wanted=combined([requirements(step,request['parameters'].get(step['id'],{}),request['configs'].get(step['id'],{})) for step in steps])
                env=cfg.get('workflowEnvironments',{}).get(flow['id'],{})
                found=w.available_images(r);probes={};assigned={};built={}
                for step in steps:
                    spec=requirements(step,request['parameters'].get(step['id'],{}),request['configs'].get(step['id'],{}))
                    if spec.get('services'):raise ValueError(step['name']+'需要外部在线服务，安装软件不能解除该限制。请取消此步骤或使用已有结果。')
                    candidates=list(dict.fromkeys(filter(None,[env.get('stageImages',{}).get(step['id']),env.get('image'),found.get(flow['id']),*cfg.get('images',[])])))
                    image=None;unchecked=[]
                    model_recipe=request.get('recipes',{}).get(step['id'],{})
                    for candidate in ([] if model_recipe else candidates):
                        if candidate not in probes:
                            try:probes[candidate]=w.software_probe(r,candidate,wanted)
                            except Exception as error:probes[candidate]={'error':str(error)}
                        state=w.readiness_for(step,probes[candidate],spec)
                        if state['state']=='ready':image=candidate;break
                        if spec.get('r') and probes[candidate].get('rError'):unchecked.append(state['detail'])
                    if image:
                        update(db,job,'running',step['name']+'：已有软件可用，无需安装')
                    else:
                        if unchecked:raise ValueError(step['name']+'的软件检查未完成：'+unchecked[0]+'；请重新检查，原有环境未更改。')
                        fingerprint=hashlib.sha256(w.canonical({'requirements':spec,'upstream':step.get('nextflowSource'),'base':RUNTIME_VERSION,'installer':4,'recipe':model_recipe})).hexdigest()
                        tag='localhost/omaax-auto-'+fingerprint[:24]+':1'
                        image=built.get(fingerprint)
                        if not image:
                            try:
                                cached=json.loads(r.command(['podman','image','inspect',tag],timeout=20))[0];image=cached.get('Id') or cached.get('ID')
                            except Exception:image=None
                        if not image:
                            context=directory/fingerprint;context.mkdir(mode=0o700)
                            build_context(w,r,context,{**step,'environmentSelection':upstream_selection(step,request['configs'].get(step['id'],{}))},spec,model_recipe)
                            update(db,job,'running',step['name']+'：正在准备所需软件，不依赖镜像仓库')
                            prepare_rootfs(context)
                            update(db,job,'running',step['name']+'：正在安装缺少的软件，日志保留在服务器')
                            direct={k:v for k,v in os.environ.items() if k.lower() not in ('http_proxy','https_proxy','all_proxy','no_proxy')}
                            process=subprocess.run(['podman','build','--isolation=rootless','--cap-drop=ALL','--http-proxy=false','--label','org.omaax.environment='+fingerprint,'--tag',tag,'--file',str(context/'Containerfile'),str(context)],stdout=log,stderr=subprocess.STDOUT,timeout=14400,env=direct)
                            if process.returncode:raise ValueError(step['name']+'的软件安装失败，请查看安装日志并检查服务器下载连接；原有环境保持不变。')
                            info=json.loads(r.command(['podman','image','inspect',tag],timeout=20))[0];image=info.get('Id') or info.get('ID')
                        if not image.startswith('sha256:'):image='sha256:'+image
                        probe=w.software_probe(r,image,spec)
                        state=w.readiness_for(step,probe,spec)
                        if state['state']!='ready':raise ValueError(step['name']+'安装后检查未通过：'+state['detail'])
                        probes[image]=probe;built[fingerprint]=image
                    assigned[step['id']]=image
                # Publish only after every selected step has passed; previous mappings survive failure.
                db.execute('BEGIN IMMEDIATE');latest=r.config(db)
                (directory/'previous-environments.json').write_text(json.dumps({key:latest.get(key) for key in ['workflowEnvironments','images']},indent=2),encoding='utf-8')
                environments=latest.get('workflowEnvironments',{});entry=environments.setdefault(flow['id'],{'stageImages':{}})
                entry.setdefault('stageImages',{}).update(assigned);entry.setdefault('image',next(iter(assigned.values())))
                entry['versions']={s:probes[i] for s,i in assigned.items()};entry['preparedSteps']=request['steps']
                images=list(dict.fromkeys([*latest.get('images',[]),*assigned.values()]))
                for key,value in [('workflowEnvironments',environments),('images',images)]:db.execute('INSERT OR REPLACE INTO config VALUES(?,?)',(key,json.dumps(value)))
                db.commit();r.audit(db,'workflow.environment_prepared',{'workflow':flow['id'],'steps':request['steps'],'images':assigned})
                update(db,job,'complete','所选 '+str(len(assigned))+' 个步骤的环境已准备完成')
            except Exception as error:
                db.rollback();log.write(('\n'+str(error)+'\n').encode());log.flush()
                update(db,job,'failed',str(error))
