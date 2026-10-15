"""Freeze the five user-provided workflows and their declared configurable parameters."""
import ast,hashlib,importlib.util,json,subprocess,sys,zipfile
from pathlib import Path,PurePosixPath

repo=Path(__file__).resolve().parents[2]
out=repo/'desktop/resources/workflows'
archive=repo/'source-inputs/PlantAI_five_omics_scripts_20261013.zip'
subprocess.run([sys.executable,str(repo/'desktop/scripts/import-fixed-workflows.py')],check=True,capture_output=True)
catalog=json.loads((out/'catalog.json').read_text(encoding='utf-8'))
catalog.update(schemaVersion=2,version='20261015.2',validationMode='execution_only')
catalog.pop('scientificValidation',None)
managed={'input','input_dir','output','output_dir','outdir','workspace','prefix','cpu','threads','job_id','run_id','log_file','final_dir','scripts_dir','stage','config_file'}
defaults={'gene_family':['genome_prepare','identify','phylogeny','motif','domain','structure','combo','promoter','tfbs','synteny','karyotype','pep','gene_family_report'],
 'rnaseq':['rnaseq_qc','rnaseq_align','rnaseq_quant','rnaseq_sample_qc','rnaseq_de','rnaseq_go_enrich','rnaseq_kegg_enrich'],
 'singlecell':['singlecell_analyze'],'spatial':['spatial_analyze'],'metagenome':['metagenome_summarize']}
nf={'singlecell_upstream':'nf-core_scrnaseq_4.2.0','spatial_segment':'nf-core_sopa_1.0.1','metagenome_profile':'nf-core_taxprofiler_2.0.1','metagenome_mag':'nf-core_mag_5.5.0'}
names={'config':'配置文件','proteins':'蛋白序列文件','operation':'处理方式','sample_parallel_jobs':'同时处理的样本数','threads_per_sample':'每个样本使用的线程数','resume_existing':'接续已有结果','adapter_r1':'第一端接头序列','adapter_r2':'第二端接头序列','detect_adapter_for_pe':'自动识别双端接头','cut_front':'剪切前端低质量区','cut_tail':'剪切末端低质量区','cut_window_size':'质量检查窗口大小','cut_mean_quality':'窗口平均质量下限','qualified_quality_phred':'合格碱基质量下限','unqualified_percent_limit':'不合格碱基比例上限','n_base_limit':'未知碱基数量上限','length_required':'最短序列长度','mafft_params':'序列比对参数','iqtree_params':'建树参数','input':'输入数据','samples':'样本信息表','counts':'计数矩阵','metadata':'样本分组表','abundance':'物种丰度表','outdir':'结果位置','annotations':'细胞类型对应表','seed':'随机种子','n_hvg':'高变基因数量','n_pcs':'主成分数量','n_neighbors':'邻居数量','resolution':'聚类分辨率','counts_layer':'使用的计数层','integration':'批次整合方式','paired':'配对分析','top_taxa':'显示的物种数量','n_permutations':'置换次数','point_size':'图中点大小','invert_y':'翻转纵轴','n_jobs':'并行数量','table':'表达数据表','counts_source':'计数来源','profiler':'分类软件','database_version':'数据库版本','taxonomic_rank':'分类层级','abundance_type':'丰度类型','control':'对照组','treated':'处理组','padj':'显著性阈值','log2fc':'变化阈值','module':'分析模块','min_genes':'最少基因数量','min_counts':'最少计数','max_genes':'最多基因数量','min_cells_per_gene':'基因出现的最少细胞数','max_pct_mt':'线粒体比例上限','mitochondrial_pattern':'线粒体基因名称规则','mode':'空间邻居方式','radius':'空间半径','plots':'生成图表','qc_tool':'质控软件','blast_evalue':'BLAST 匹配阈值','blast_identity_min':'BLAST 一致性阈值','hmm_cov_min':'HMM 覆盖阈值','dom_i_evalue_max':'结构域匹配阈值','tree_layout':'进化树布局','gene_map':'基因对应表','gmt':'基因集文件','selected':'候选基因列表','universe':'背景基因列表'}

def arguments(data):
 rows=[]
 for call in ast.walk(ast.parse(data)):
  if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Attribute) or call.func.attr!='add_argument':continue
  flags=[a.value for a in call.args if isinstance(a,ast.Constant) and isinstance(a.value,str)]
  if not flags:continue
  kw={k.arg:k.value for k in call.keywords}
  flag=next((f for f in flags if f.startswith('--')),flags[0]);key=flag.lstrip('-').replace('-','_')
  if 'dest' in kw:
   try:key=ast.literal_eval(kw['dest'])
   except (ValueError,TypeError):pass
  row={'flags':flags,'key':key,'type':'string','managed':key in managed or key in {'script','command','python','rscript','project_root','interproscan'} or key.endswith(('_bin','_executable','_script')),'label':names.get(key,key.replace('_',' '))}
  if isinstance(kw.get('type'),ast.Name) and kw['type'].id in ('int','float'):row['type']='integer' if kw['type'].id=='int' else 'number'
  for k in ('default','help','choices','required','action','nargs'):
   if k in kw:
    try:row[k]=ast.literal_eval(kw[k])
    except (ValueError,TypeError):pass
  action=kw.get('action')
  if (isinstance(action,ast.Attribute) and action.attr=='BooleanOptionalAction') or (isinstance(action,ast.Name) and action.id=='BooleanOptionalAction'):row['action']='boolean_optional'
  if row.get('action') in ('store_true','store_false','boolean_optional'):row['type']='boolean'
  elif isinstance(row.get('default'),bool):row['type']='boolean'
  if isinstance(row.get('default'),list) or row.get('nargs') in ('+','*') or row.get('action')=='append':row['type']='array'
  if row.get('action') not in (None,'store_true','store_false','boolean_optional','store','append'):
   row['managed']=True
  rows.append(row)
 return rows

def properties(value):
 if isinstance(value,dict):return {'type':'object','properties':{k:{**properties(v),'label':names.get(k,k.replace('_',' '))} for k,v in value.items()},'additionalProperties':True}
 if isinstance(value,bool):return {'type':'boolean','default':value}
 if isinstance(value,int):return {'type':'integer','default':value}
 if isinstance(value,float):return {'type':'number','default':value}
 if isinstance(value,list):return {'type':'array','default':value}
 return {'type':'string','default':value}

with zipfile.ZipFile(archive) as z:
 pre='PlantAI_five_omics_source_20261013/'
 specs=json.loads(z.read(pre+'project_source/bioflow_kernel/workflow_backends/stages.json'))
 hashes={}
 with zipfile.ZipFile(out/'kernel.zip','w',zipfile.ZIP_DEFLATED,compresslevel=6) as package:
  for item in z.infolist():
   relative=item.filename.removeprefix(pre)
   if item.is_dir():continue
   if relative.startswith('project_source/bioflow_kernel/'):
    name=relative.removeprefix('project_source/bioflow_kernel/')
    if any(p in PurePosixPath(name).parts for p in ('__pycache__','.git','extra-envs','tests')) or name.endswith(('.pyc','.bak','.bak2','.old')):continue
    if name.startswith('docker/'):continue
   elif relative.startswith('upstream_sources/'):
    name=relative
    if any(p in PurePosixPath(name).parts for p in ('.git','.github','tests','docs')):continue
   else:continue
   if '..' in PurePosixPath(name).parts or PurePosixPath(name).is_absolute():raise ValueError(name)
   raw=z.read(item)
   if name=='cli/run_phylogeny.py':
    text=raw.decode('utf-8');needle='        identify_info = validate_identify_output(workspace, effective_prefix)'
    if text.count(needle)!=1:raise ValueError('Phylogeny entry changed; review compatibility patch')
    text=text.replace(needle,'        # OMaaX: an explicit protein input does not require an Identify audit record.\n        identify_info = {"protein_fasta": Path(args.proteins), "identify_manifest": {}, "identify_metrics": {}, "identify_parameters": {}} if args.proteins else validate_identify_output(workspace, effective_prefix)')
    raw=text.encode('utf-8')
   if name=='cli/run_rnaseq_align.py':
    text=raw.decode('utf-8');needle='    sample_dir = Path(sample_config).parent if sample_config else None'
    if text.count(needle)!=1:raise ValueError('RNA alignment entry changed; review explicit-index compatibility patch')
    text=text.replace(needle,'    # OMaaX: use a complete explicitly selected index without imposing file naming.\n    for suffix in ("ht2", "ht2l"):\n        if all(Path(f"{requested_index}.{number}.{suffix}").is_file() for number in range(1, 9)):\n            return requested_index\n'+needle)
    raw=text.encode('utf-8')
   if name=='cli/run_sequence_extract_by_id.py':
    text=raw.decode('utf-8')
    if 'import os\n' not in text:text=text.replace('from pathlib import Path','import os\nfrom pathlib import Path',1)
    raw=text.encode('utf-8')
   if name=='cli/run_ortholog.py':
    text=raw.decode('utf-8').replace('Identity threshold (%)','Identity threshold (%%)')
    # argparse expands help strings with percent formatting.
    text=text.replace('identity (0-100%)','identity (0-100%%)')
    raw=text.encode('utf-8')
   info=zipfile.ZipInfo(name,(2026,10,13,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100600<<16
   package.writestr(info,raw);hashes[name]=hashlib.sha256(raw).hexdigest()
  for flow in catalog['workflows']:
   flow.update(status='available',version=catalog['version'],executionMode='fixed-pipeline-v2',defaultSteps=defaults[flow['id']])
   allsteps=flow['nodes']+flow['extraNodes']
   # Include actual auxiliary nodes in their source-defined order, without inventing biological checkpoints.
   flow['order']=[s['id'] for s in allsteps if s['entryPresent']]
   for sid in defaults[flow['id']]:
    if sid not in flow['order']:raise ValueError(sid)
   for step in allsteps:
    step['status']='available' if step['entryPresent'] else 'missing_source'
    step['entry']=step['entry'].removeprefix('project_source/bioflow_kernel/') if step['entry'] else None
    if step['entry']:
     step['originalSourceSha256']=step['sourceSha256'];step['sourceSha256']=hashes[step['entry']]
    step['kind']='omics' if step['id'] in specs else 'cli'
    step['arguments']=arguments(z.read(pre+'project_source/bioflow_kernel/'+step['entry']).decode('utf-8')) if step['entry'] else []
    if step['kind']=='omics':
     spec=dict(specs[step['id']]);spec['required_config']=[k for k in spec['required_config'] if k!='expected_outputs'];step['contract']=spec
     stage=step['id'];root='workflow_backends/'+flow['id']
     example={'singlecell_analyze':'config.example.json','singlecell_annotate':'annotate.example.json','singlecell_pseudobulk':'pseudobulk.example.json','spatial_analyze':'config.example.json','metagenome_summarize':'config.example.json'}.get(stage)
     cfg=json.loads(z.read(pre+'project_source/bioflow_kernel/'+root+'/config/'+example)) if example else {k:'' for k in spec['required_config']}
     cfg.pop('outdir',None);cfg.pop('expected_outputs',None)
     if stage=='metagenome_summarize':cfg.update(abundance='abundance.tsv',samples='samples.csv',taxon_names='',database_version='user_input')
     if stage in ('singlecell_analyze','spatial_analyze'):cfg['samples']='samples.csv'
     if stage in nf:
      schema=json.loads(z.read(pre+'upstream_sources/'+nf[stage]+'/nextflow_schema.json'))
      prop={}
      for group in schema.get('$defs',schema.get('definitions',{})).values():prop.update(group.get('properties',{}))
      step['configSchema']={'type':'object','properties':{k:v for k,v in prop.items() if k not in ('outdir','custom_config_base','custom_config_version','custom_config','config_profile_name','config_profile_description','config_profile_contact','config_profile_url')},'additionalProperties':False}
      step['nextflowSource']='upstream_sources/'+nf[stage]
     else:step['configSchema']=properties(cfg)
     step['configDefaults']=cfg
    else:
     required={a['key']:'' for a in step['arguments'] if a.get('required') and not a['managed']}
     step['configDefaults']={}
     step['parameterDefaults']={**{a['key']:a['default'] for a in step['arguments'] if not a['managed'] and a.get('default') is not None},**required}
     if step['id']=='gene_family_report':step['parameterDefaults']['no_model']=True
     if step['id']=='rnaseq_kegg_enrich':step['parameterDefaults']['enrich_type']='kegg'
   early={'gene_family':['prepare_organize','prepare_normalize_gff','prepare_longest_transcript','prepare_extract_pep','prepare_extract_cds','prepare_filter_fasta','prepare_simplify_ncbi'],'singlecell':['singlecell_upstream'],'spatial':['spatial_segment','spatial_export'],'metagenome':['metagenome_profile','metagenome_merge']}.get(flow['id'],[])
   flow['order']=list(dict.fromkeys([*early,*defaults[flow['id']],*flow['order']]))
   if flow['id']=='gene_family':
    sequence=flow['order'];sequence.remove('identify_refine');sequence.insert(sequence.index('identify')+1,'identify_refine')
   report={'gene_family':'gene_family_report','rnaseq':'rnaseq_report'}.get(flow['id'])
   if report and report in flow['order']:flow['order']=[s for s in flow['order'] if s!=report]+[report]
   if flow['id']=='rnaseq':
    flow['inputTemplates']={'sample_configuration':{'format':'yaml','description':'samples 必须是样本名到信息的对象，不是列表；r1/r2 或 read1/read2 为双端路径。groups 是样本到分组的对象，compare 每项按 [对照组,处理组] 填写。以用户实际提供的分组和文库类型为准。','content':{'samples':{'sample_A':{'r1':'sample_A_R1.fastq.gz','r2':'sample_A_R2.fastq.gz','group':'control','condition':'control','group_role':'control','replicate':'1'},'sample_B':{'r1':'sample_B_R1.fastq.gz','r2':'sample_B_R2.fastq.gz','group':'treated','condition':'treated','group_role':'compare','replicate':'1'}},'groups':{'sample_A':'control','sample_B':'treated'},'compare':[['control','treated']]}}}
  spec=importlib.util.spec_from_file_location('catalog_extension',repo/'desktop/workflow-extensions/catalog_extension.py')
  extension=importlib.util.module_from_spec(spec);spec.loader.exec_module(extension)
  dependency_path=repo/'desktop/resources/ssh/workflow-dependencies.json'
  dependencies=json.loads(dependency_path.read_text(encoding='utf-8'))
  extension.extend(catalog,package,hashes,dependencies)
  dependency_path.write_text(json.dumps(dependencies,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
  package.writestr('omaax/catalog.json',json.dumps(catalog,ensure_ascii=False))
  package.writestr('omaax/source-hashes.json',json.dumps(hashes))
catalog['bundleSha256']=hashlib.sha256((out/'kernel.zip').read_bytes()).hexdigest()
(out/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(out/'bundle-manifest.json').write_text(json.dumps({'sha256':catalog['bundleSha256'],'fileCount':len(hashes),'files':hashes},indent=2),encoding='utf-8')
(out/'SOURCE.md').write_text('# 一体化分析流程来源\n\n来自用户提供的 PlantAI_five_omics_scripts_20261013.zip，来源提交 214e3b9079c4aa807beb5340642d18969a1e6530。保留源文件说明、许可证与来源记录。原有五条流程和新增表观遗传流程固定源码与调用方式，开放程序声明的参数；OMaaX 不增加生物学审核或科学验收门槛。\n\n仅打包已收录流程相关源码及固定 nf-core 上游源码，不安装社区工具库、全部分析软件或参考数据库。运行环境按所选步骤检查和配置；不自动下载参考数据库。原项目与各第三方组件的许可证继续适用。\n',encoding='utf-8')
print(json.dumps({'workflows':len(catalog['workflows']),'sourceFiles':len(hashes),'bundleBytes':(out/'kernel.zip').stat().st_size,'bundleSha256':catalog['bundleSha256']}))
