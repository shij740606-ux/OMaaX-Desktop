"""Extend the reviewed source bundle with real, independently selectable entry points."""
import hashlib,json
from pathlib import Path

BASE=Path(__file__).parent
LABELS={
 'samples':'样本信息表','input':'已有分析数据','counts_layer':'原始计数所在位置','min_genes':'每个细胞至少检测到的基因数','max_genes':'每个细胞最多检测到的基因数','min_counts':'每个细胞的最低计数','min_cells_per_gene':'基因至少出现的细胞数','max_pct_mt':'线粒体计数比例上限（%）','mitochondrial_pattern':'线粒体基因名称规则','expected_doublet_rate':'预计双细胞比例','remove_doublets':'移除预测双细胞','target_sum':'标准化后的总计数','n_hvg':'高变基因数量','n_pcs':'主成分数量','batch_key':'区分实验批次的字段','n_neighbors':'邻近细胞数量','min_dist':'低维图中点的紧密程度','resolution':'聚类精细程度','groupby':'使用的细胞分组','padj':'校正后显著性上限','log2fc':'变化倍数阈值（log2）','annotations':'细胞类型对应表','min_cells':'每份汇总的最低细胞数','gmt':'基因集文件','min_size':'每个基因集至少匹配的基因数','root_group':'轨迹的起始细胞群','ligand_receptor':'配体受体对应表','n_permutations':'随机置换次数','top_genes':'每群展示的标记基因数量','point_size':'图中点大小','spatial_mode':'组织位置的邻居选择方式','radius':'邻域半径','spatial_weight':'空间关系在分区中的权重','abundance':'丰度表','top_taxa':'图中展示的特征数量','pseudocount':'绘图时避免取对数零值的常数','correlation_cutoff':'相关性绝对值下限','quality_table':'MAG 质量评估表','min_completeness':'MAG 完整度下限（%）','max_contamination':'MAG 污染度上限（%）','peaks':'峰或区域文件','merge_distance':'区域合并的最大间隔（碱基）','paired_end':'按双端片段计数','strandedness':'计数使用的链方向','counts':'区域计数矩阵','control':'对照组名称','treated':'处理组名称','gtf':'基因注释文件','promoter_upstream':'启动子上游范围（碱基）','promoter_downstream':'启动子下游范围（碱基）','fasta':'参考基因组文件','background_fasta':'基序比较的背景序列','motifs':'已知基序数据库','bigwigs':'覆盖度轨迹文件列表','regions':'绘图区域文件','min_coverage':'位点最低测序覆盖','delta':'甲基化比例变化下限','pvalue_cutoff':'显著性上限','cell_type':'要比较的细胞群','paired':'使用配对供者设计','reference_point':'覆盖度图的对齐位置','seed':'随机种子','selected':'候选基因列表','universe':'背景基因列表'}
HELP={
 'reference_point':'峰区域通常按中点对齐。选择转录起点或终点时，需要提供带方向的基因区域文件。',
 'resolution':'值越大通常分出越多细胞群；若群体太粗或太碎，再调整。',
 'min_genes':'过滤基因太少的位置；建议结合质控分布调整。',
 'max_genes':'0 表示不设置上限；过高的基因数可能提示多个细胞混合。',
 'max_pct_mt':'0 表示不启用该过滤；需同时填写适合该物种的基因名称规则。',
 'counts_layer':'H5AD 中原始计数的层名称；填 X 表示主矩阵本身就是原始计数。',
 'expected_doublet_rate':'例如 0.06 表示预计 6% 双细胞；应参考测序上样量。',
 'groupby':'默认使用聚类编号；提供细胞注释后可改为 cell_type。',
 'root_group':'必须填写实际存在的起始群名称；这会决定拟时序方向。',
 'spatial_weight':'0 到 1；越大越重视组织相邻关系，越小越重视表达相似性。',
 'n_permutations':'越大越稳定但越耗时；通常保留默认，正式分析可增加。',
 'strandedness':'0 为非链特异，1 为正向，2 为反向；按文库实验类型选择。',
 'paired_end':'双端测序建议开启，以片段而不是两端分别计数。',
 'delta':'例如 0.1 表示至少相差 10 个百分点。',
 'padj':'越小筛选越严格；默认 0.05。',
 'log2fc':'1 表示至少两倍变化；0 表示不按倍数过滤。',
 'min_coverage':'低覆盖位点更不稳定；结合样本深度调整。',
 'promoter_upstream':'以基因转录起点为中心定义启动子范围，影响区域注释。',
 'spatial_mode':'网格用于规则芯片；普通坐标可选最近邻或指定半径。',
 'correlation_cutoff':'仅保留足够强的相关性；相关不代表因果或直接互作。',
}
CRITICAL={'reference_point','cell_type','paired','counts_layer','min_genes','max_genes','min_counts','min_cells_per_gene','max_pct_mt','mitochondrial_pattern','expected_doublet_rate','remove_doublets','n_hvg','n_pcs','n_neighbors','min_dist','resolution','groupby','padj','log2fc','min_cells','root_group','n_permutations','spatial_mode','radius','spatial_weight','top_taxa','correlation_cutoff','min_completeness','max_contamination','merge_distance','paired_end','strandedness','promoter_upstream','promoter_downstream','min_coverage','delta','pvalue_cutoff'}
CELL=['anndata','numpy','pandas','scipy','matplotlib','scanpy','igraph','leidenalg']
TABLE=['numpy','pandas','scipy','matplotlib']

def schema(defaults,required):
    types=lambda value:'boolean' if isinstance(value,bool) else 'integer' if isinstance(value,int) else 'number' if isinstance(value,float) else 'array' if isinstance(value,list) else 'string'
    props={k:{'type':types(v),'default':v,'label':LABELS.get(k,k),'description':HELP.get(k,'通常保留默认；可在对话中由模型根据输入配置调整。'),'userVisible':k in CRITICAL,'necessity':'按数据调整' if k in CRITICAL else '通常无需调整'} for k,v in defaults.items()}
    enums={'spatial_mode':['knn','grid','radius'],'strandedness':[0,1,2],'reference_point':['center','TSS','TES']}
    for key,choices in enums.items():
        if key in props:props[key]['enum']=choices
    return {'type':'object','properties':props,'required':required,'additionalProperties':False}

def extend(catalog,package,hashes,dependencies):
    registry={}
    source=BASE/'run_stage.py';entry='omaax_extensions/run_stage.py'
    def add(flow,suffix,name,module,action,defaults,outputs,software,required=None,modules=None,tools=None,r=None,auto=None,section='下游常用分析'):
        sid=flow['id']+'_'+suffix;required=required or []
        registry[sid]={'name':name,'module':module,'action':action,'outputs':outputs,'software':software}
        step={'id':sid,'name':name,'description':name+'，结果独立保存，可在对话中调整输入和参数。','software':software,'entry':entry,'entryPresent':True,'sourceSha256':hashlib.sha256(source.read_bytes()).hexdigest(),'status':'available','kind':'omics','extensionStage':True,'arguments':[],'optional':True,'section':section,'contract':{'workflow':flow['id'],'action':action,'required_config':required,'required_outputs':outputs,'upstream':False},'configDefaults':defaults,'configSchema':schema(defaults,required),'autoInputs':auto or {}}
        flow['nodes'].append(step);dependencies[sid]={'modules':modules if modules is not None else TABLE}
        if tools:dependencies[sid]['tools']=tools
        if r:dependencies[sid]['r']=r
        return sid
    def report(flow):return add(flow,'report','汇总图表与分析报告','', 'report',{},['report.html'],'OMaaX HTML 报告',modules=[],section='绘图与报告')
    for flow in catalog['workflows'][2:]:
        original=flow['nodes']+flow['extraNodes'];byid={s['id']:s for s in original}
        upstream=[s for s in original if s.get('contract',{}).get('upstream')]
        legacy=[s for s in original if s not in upstream];flow['nodes']=upstream;flow['extraNodes']=legacy
        for step in legacy:step['section']='原有兼容入口';step['name']+='（原有入口）'
        for step in upstream:
            step['section']='上游固定计算'
            step['fixedNextflow']={'singlecell_upstream':'nf-core/scrnaseq','spatial_segment':'nf-core/sopa','metagenome_profile':'nf-core/taxprofiler','metagenome_mag':'nf-core/mag'}[step['id']]
            dependencies[step['id']]={'tools':['java','nextflow'],'modules':['yaml'],'processEnvironments':[step['nextflowSource']]}
            if step['id']=='spatial_segment':step['configDefaults']['spaceranger_reference']=None
        if flow['id']=='metagenome':
            merged=byid['metagenome_merge'];flow['extraNodes'].remove(merged);flow['nodes'].insert(1,merged);merged['section']='输入整理'
        if flow['id'] in ('singlecell','spatial'):
            spatial=flow['id']=='spatial';state=[]
            def cell(suffix,name,action,defaults,outputs=(),software='Scanpy',required=(),modules=None):
                config={'input':'@auto',**defaults};sid=add(flow,suffix,name,'cells',action,config,['processed.h5ad',*outputs],software,required=['input',*required],modules=modules or CELL,auto={'input':{'steps':list(state),'artifact':'processed.h5ad'}})
                state.append(sid);return sid
            source_step='spatial_segment' if spatial else 'singlecell_upstream'
            prepare=add(flow,'prepare','导入表达矩阵与样本信息','cells','prepare',{'samples':'@auto','upstream_results':'@auto','counts_layer':'counts','spatial':spatial},['processed.h5ad','cells.csv'],'Scanpy / AnnData',required=['samples'],modules=CELL,auto={'samples':{'steps':[source_step],'fromConfig':'input','fallback':'samples.csv'},'upstream_results':{'steps':[source_step],'artifact':'.','fallback':''}});state.append(prepare)
            qc=cell('qc','过滤低质量细胞或位置','qc',{'min_genes':100 if spatial else 200,'min_counts':1,'max_genes':0,'min_cells_per_gene':3,'mitochondrial_pattern':'','max_pct_mt':0},['qc_metrics.csv'])
            if not spatial:cell('doublets','识别双细胞','doublets',{'expected_doublet_rate':.06,'remove_doublets':True,'seed':42},['doublets.csv'],'Scanpy Scrublet',modules=[*CELL,'sklearn','skimage'])
            norm=cell('normalize','标准化与选择高变基因','normalize',{'target_sum':10000,'n_hvg':2000},['variable_genes.csv'])
            pca=cell('pca','主成分分析','pca',{'n_pcs':30,'seed':42},['pca.csv'])
            if not spatial:cell('harmony','整合实验批次','harmony',{'batch_key':'batch','seed':42},['integrated_pca.csv'],'Harmony / Scanpy',modules=[*CELL,'harmonypy'])
            embed=cell('embedding','计算邻居与 UMAP','embedding',{'n_neighbors':15,'min_dist':.5,'seed':42},['umap.csv'])
            cluster=cell('cluster','划分表达相似的细胞群','cluster',{'resolution':.6,'seed':42},['clusters.csv'])
            markers=cell('markers','寻找各群标记基因','markers',{'groupby':'cluster','padj':.05,'log2fc':0},['markers.csv'])
            cell('annotation','填写细胞类型注释','annotate',{'annotations':'','groupby':'cluster'},['cell_annotations.csv'],'AnnData / 用户对应表',required=['annotations'])
            cell('composition','比较各样本细胞组成','composition',{'groupby':'cluster'},['cell_counts.csv','cell_fractions.csv','composition.png'])
            bulk=cell('bulk','按供者和细胞群汇总计数','pseudobulk',{'groupby':'cluster','min_cells':20},['counts.tsv','samples.csv'],'AnnData / NumPy')
            add(flow,'group_de','比较指定细胞群的组间表达差异','epigenetics','gene_differential',{'counts':'@auto','samples':'@auto','control':'','treated':'','padj':.05,'log2fc':1,'cell_type':'','paired':False},['differential_genes.tsv','selected_genes.tsv','volcano.png'],'DESeq2 / 供者汇总',required=['counts','samples','control','treated','cell_type'],r=['DESeq2'],auto={'counts':{'steps':[bulk],'artifact':'counts.tsv'},'samples':{'steps':[bulk],'artifact':'samples.csv'}})
            cell('gene_sets','比较功能基因集活性','gene_sets',{'gmt':'','min_size':3,'seed':42},['gene_set_scores.csv'],'Scanpy 基因集评分',required=['gmt'])
            cell('communication','分析配体受体表达关联','communication',{'groupby':'cluster','ligand_receptor':'','n_permutations':999,'padj':.05,'seed':42},['ligand_receptor_scores.tsv','communication.png'],'配体受体均值乘积 / 随机置换',required=['ligand_receptor'])
            if spatial:
                graph=cell('neighbors','建立组织位置邻居图','spatial_graph',{'spatial_mode':'knn','n_neighbors':6,'radius':100},['spatial_neighbors.csv'],'Squidpy',modules=[*CELL,'squidpy'])
                domains=cell('domains','结合表达与位置划分组织区域','spatial_domains',{'spatial_weight':.5,'resolution':.6,'seed':42},['spatial_domains.csv'],'Leiden / 空间与表达加权图')
                cell('variable_genes','寻找空间变化的基因','spatial_genes',{'n_permutations':999,'seed':42},['spatial_variable_genes.csv'],'Squidpy Moran’s I',modules=[*CELL,'squidpy'])
                cell('neighborhood','比较细胞群的空间邻域','neighborhood',{'groupby':'cluster','n_permutations':999,'seed':42},['neighborhood_enrichment.csv'],'Squidpy 邻域富集',modules=[*CELL,'squidpy'])
            else:cell('trajectory','分析细胞状态轨迹','trajectory',{'groupby':'cluster','root_group':'','seed':42},['pseudotime.csv','trajectory_graph.csv'],'Scanpy PAGA / DPT',required=['root_group'])
            plots=cell('plots','绘制细胞群、标记与轨迹图','plots',{'groupby':'cluster','top_genes':5,'point_size':3},['umap.png'],'Scanpy / Matplotlib');flow['nodes'][-1]['section']='绘图与报告'
            rep=report(flow)
            flow['defaultSteps']=[prepare,qc,norm,pca,embed,cluster,markers,*([graph,domains] if spatial else []),plots,rep]
        elif flow['id']=='metagenome':
            prepared=add(flow,'prepare','整理丰度表与样本信息','community','prepare',{'abundance':'@auto','samples':'@auto','database':'','taxonomic_rank':'S'},['abundance.tsv','relative_abundance.tsv','samples.csv'],'Pandas',required=['abundance','samples'],auto={'abundance':{'steps':['metagenome_profile','metagenome_merge'],'artifact':'.','fallback':'abundance.tsv'},'samples':{'steps':['metagenome_profile'],'fromConfig':'input','fallback':'samples.csv'}})
            auto={'abundance':{'steps':[prepared,'metagenome_merge'],'artifact':'abundance.tsv'}}
            added=[]
            for suffix,name,action,defaults,outputs in [
                ('composition','绘制群落组成','composition',{'top_taxa':20},['composition.tsv','composition.png']),
                ('alpha','比较样本内的多样性','alpha',{},['alpha_diversity.tsv','alpha_diversity.png']),
                ('beta','比较样本间群落差异','beta',{},['bray_curtis.tsv','pcoa.tsv','pcoa.png']),
                ('heatmap','绘制特征丰度热图','heatmap',{'top_taxa':30,'pseudocount':1e-6},['heatmap_abundance.tsv','abundance_heatmap.png']),
                ('network','分析物种间丰度相关性','network',{'top_taxa':50,'correlation_cutoff':.6,'padj':.05},['correlation_network.tsv','network_summary.png'])]:
                added.append(add(flow,suffix,name,'community',action,{'abundance':'@auto',**defaults},outputs,'SciPy / Pandas / Matplotlib',required=['abundance'],auto=auto))
            add(flow,'functional','绘制基因与通路功能丰度','community','functional',{'abundance':'','top_taxa':20},['functional_abundance.tsv','functional_profile.png'],'HUMAnN 等输出表 / Matplotlib',required=['abundance'])
            add(flow,'mag_quality','整理与筛选 MAG 质量','community','mag_summary',{'quality_table':'@auto','min_completeness':50,'max_contamination':10},['mag_quality.tsv','mag_quality.png'],'CheckM / CheckM2 输出表',required=['quality_table'],auto={'quality_table':{'steps':['metagenome_mag'],'artifact':'.','fallback':''}})
            rep=report(flow);flow['defaultSteps']=[prepared,*added[:4],rep]
        flow['order']=[s['id'] for s in flow['nodes']+flow['extraNodes'] if s['entryPresent']]
        flow['order'].remove(rep);flow['order'].append(rep)
        # Preserve existing upstream and all legacy entry points; downstream defaults use real split stages.
        flow['description']={'singlecell':'从测序或表达矩阵开始，选择质控、细胞分群、功能、轨迹和图表报告。','spatial':'从分割或空间矩阵开始，分析组织分区、空间基因、细胞邻域和图表报告。','metagenome':'从测序分类或丰度表开始，分析群落、多样性、差异、功能与 MAG。'}[flow['id']]
    provenance=json.loads((BASE/'upstream-manifest.json').read_text())
    for fid,name,nf,version,defaults,phases in [
        ('atacseq','ATAC-seq 染色质开放分析','atacseq','2.1.2',{'input':'','fasta':'','gtf':'','aligner':'bwa'},['测序质量与接头过滤','参考基因组比对','重复片段和低质量过滤','开放区域峰检测','一致区域、计数与质量汇总']),
        ('chipseq','ChIP-seq 结合区域分析','chipseq','2.1.0',{'input':'','fasta':'','gtf':'','aligner':'bwa'},['测序质量与接头过滤','参考基因组比对','重复片段和低质量过滤','结合区域峰检测','一致区域、计数与质量汇总']),
        ('cutandrun','CUT&Tag / CUT&RUN 分析','cutandrun','3.2.2',{'input':'','fasta':'','gtf':''},['测序质量与接头过滤','目标与 Spike-in 比对','片段过滤与覆盖度标准化','SEACR / MACS2 峰检测','一致区域与质量汇总']),
        ('methylseq','DNA 甲基化分析','methylseq','4.2.0',{'input':'','fasta':'','aligner':'bismark'},['测序质量与接头过滤','亚硫酸氢盐测序比对','重复片段过滤','逐位点提取甲基化比例','覆盖度与甲基化质量汇总'])]:
        sourceid='nf-core_'+nf+'_'+version;prefix='upstream_sources/'+sourceid
        sourcepath=BASE/prefix;schema_source=json.loads((sourcepath/'nextflow_schema.json').read_text());props={}
        for group in schema_source.get('$defs',schema_source.get('definitions',{})).values():props.update(group.get('properties',{}))
        excluded={'outdir','custom_config_base','custom_config_version','custom_config','config_profile_name','config_profile_description','config_profile_contact','config_profile_url'}
        stepid=fid+'_upstream';upstream={'id':stepid,'name':'运行固定测序上游','description':'按原 nf-core 流程依次执行下面的固定计算阶段。','software':'nf-core/'+nf+' '+version,'entry':prefix+'/main.nf','entryPresent':True,'sourceSha256':hashlib.sha256((sourcepath/'main.nf').read_bytes()).hexdigest(),'status':'available','kind':'omics','arguments':[],'optional':True,'section':'上游固定计算','substeps':phases,'nextflowSource':prefix,'fixedNextflow':'nf-core/'+nf,'contract':{'workflow':fid,'action':'upstream','required_config':['input','fasta'],'required_outputs':[],'upstream':True},'configDefaults':defaults,'configSchema':{'type':'object','properties':{k:v for k,v in props.items() if k not in excluded},'additionalProperties':False}}
        important={'aligner':'比对软件','narrow_peak':'使用窄峰模式','broad_cutoff':'宽峰显著性上限','min_trimmed_reads':'过滤后最低读段数','peakcaller':'峰检测软件','seacr_peak_threshold':'SEACR 峰检测阈值','macs2_narrow_peak':'MACS2 使用窄峰模式','macs2_broad_cutoff':'MACS2 宽峰显著性上限','minimum_alignment_q_score':'比对质量下限','skip_removeduplicates':'保留重复片段','meth_cutoff':'甲基化比例阈值','min_peak_overlap':'峰重叠比例下限'}
        for key,prop in upstream['configSchema']['properties'].items():
            prop['userVisible']=key in important
            if key in important:
                prop['label']=important[key];prop['description']='会影响比对、过滤或区域判定；按实验类型调整，通常保留原流程默认。';prop['necessity']='按实验调整'
                if prop.get('default') is not None:upstream['configDefaults'].setdefault(key,prop['default'])
        flow={'id':fid,'name':name,'description':'固定测序上游，搭配可选差异分析、区域注释、功能解释、图表与报告。','version':catalog['version'],'status':'available','executionMode':'fixed-pipeline-v2','nodes':[upstream],'extraNodes':[],'source':'https://github.com/nf-core/'+nf,'sourceVersion':version}
        flow['inputTemplates']={'downstream_sample_groups':{'format':'csv','description':'上游样本名不一定是实验分组。若用户提供独立分组表，请把计数或覆盖度步骤的 samples 设为该表；包含唯一 sample_id 和明确 group，样本编号必须对应上游产物。差异分析的 control/treated 必须与 group 中的组名一致，不要从名称推断分组。','columns':['sample_id','group']}}
        dependencies[stepid]={'tools':['java','nextflow'],'modules':['yaml'],'processEnvironments':[prefix]}
        if fid!='methylseq':
            regions=add(flow,'regions','整理与合并分析区域','epigenetics','regions',{'peaks':'@auto','merge_distance':0},['regions.bed','region_widths.png'],'固定 BED 区域合并',required=['peaks'],auto={'peaks':{'steps':[stepid],'artifact':'.','fallback':''}})
            count=add(flow,'count','汇总区域片段计数','epigenetics','count',{'peaks':'@auto','samples':'@auto','upstream_results':'@auto','bam_pattern':'','paired_end':True,'strandedness':0},['counts.tsv','samples.csv'],'featureCounts',required=['peaks','samples'],tools=['featureCounts'],auto={'peaks':{'steps':[regions],'artifact':'regions.bed'},'samples':{'steps':[stepid],'fromConfig':'input','fallback':''},'upstream_results':{'steps':[stepid],'artifact':'.','fallback':''}})
            qc=add(flow,'sample_qc','查看样本相关性与主成分','epigenetics','sample_qc',{'counts':'@auto'},['sample_pca.tsv','sample_correlation.tsv','sample_pca.png'],'NumPy PCA / Pandas',required=['counts'],auto={'counts':{'steps':[count],'artifact':'counts.tsv'}})
            add(flow,'differential','比较组间差异区域','epigenetics','differential',{'counts':'@auto','samples':'@auto','control':'','treated':'','padj':.05,'log2fc':1},['differential_regions.tsv','selected_regions.tsv','volcano.png'],'DESeq2',required=['counts','samples','control','treated'],r=['DESeq2'],auto={'counts':{'steps':[count],'artifact':'counts.tsv'},'samples':{'steps':[count],'artifact':'samples.csv'}})
            annotation=add(flow,'annotation','注释区域最近基因与启动子','epigenetics','annotate',{'peaks':'@auto','gtf':'','promoter_upstream':2000,'promoter_downstream':500},['region_annotations.tsv','genes.txt'],'GTF 转录起点最近基因注释',required=['peaks','gtf'],auto={'peaks':{'steps':[regions],'artifact':'regions.bed'}})
            add(flow,'motifs','比较区域的已知基序富集','epigenetics','motifs',{'peaks':'@auto','fasta':'','motifs':'','background_fasta':''},['motif_enrichment.tsv'],'MEME AME / bedtools',required=['peaks','fasta','motifs','background_fasta'],tools=['ame','bedtools'],auto={'peaks':{'steps':[regions],'artifact':'regions.bed'}})
            add(flow,'coverage','绘制区域覆盖度与热图','epigenetics','coverage',{'bigwigs':[],'regions':'@auto','samples':'@auto','upstream_results':'@auto','reference_point':'center','promoter_upstream':2000,'promoter_downstream':2000},['coverage_matrix.gz','coverage_heatmap.png','coverage_profile.png'],'deepTools',required=['regions'],tools=['computeMatrix','plotHeatmap','plotProfile'],auto={'regions':{'steps':[regions],'artifact':'regions.bed'},'samples':{'steps':[count],'artifact':'samples.csv','fallback':''},'upstream_results':{'steps':[stepid],'artifact':'.','fallback':''}})
            rep=report(flow);flow['defaultSteps']=[regions,count,qc,rep]
        else:
            qc=add(flow,'coverage_qc','过滤低覆盖位点与查看甲基化分布','epigenetics','methylation_qc',{'samples':'@auto','upstream_results':'@auto','min_coverage':5},['methylation_beta.tsv','methylation_qc.tsv','methylation_distribution.png','samples.csv'],'Bismark coverage / Pandas',required=['samples'],auto={'samples':{'steps':[stepid],'fromConfig':'input','fallback':''},'upstream_results':{'steps':[stepid],'artifact':'.','fallback':''}})
            sampleqc=add(flow,'sample_qc','比较样本甲基化主成分与相关性','epigenetics','methylation_samples',{'input':'@auto'},['sample_pca.tsv','sample_correlation.tsv','sample_pca.png'],'NumPy PCA / Pandas',required=['input'],auto={'input':{'steps':[qc],'artifact':'methylation_beta.tsv'}})
            heatmap=add(flow,'heatmap','绘制变化位点甲基化热图','epigenetics','methylation_heatmap',{'input':'@auto','top_taxa':50},['displayed_sites.tsv','methylation_heatmap.png'],'Matplotlib',required=['input'],auto={'input':{'steps':[qc],'artifact':'methylation_beta.tsv'}},section='绘图与报告')
            dmr=add(flow,'dmr','寻找组间差异甲基化区域','epigenetics','dmr',{'samples':'@auto','control':'','treated':'','delta':.1,'pvalue_cutoff':.05},['differential_sites.tsv','differential_methylated_regions.tsv','regions.bed'],'DSS',required=['samples','control','treated'],r=['DSS'],auto={'samples':{'steps':[qc],'artifact':'samples.csv','fallback':''}})
            annotation=add(flow,'annotation','注释甲基化区域附近的基因','epigenetics','annotate',{'peaks':'@auto','gtf':'','promoter_upstream':2000,'promoter_downstream':500},['region_annotations.tsv','genes.txt'],'GTF 转录起点最近基因注释',required=['peaks','gtf'],auto={'peaks':{'steps':[dmr],'artifact':'regions.bed'}})
            rep=report(flow);flow['defaultSteps']=[qc,sampleqc,heatmap,rep]
        enrichment=add(flow,'enrich','解释候选基因的功能与通路','community','enrichment',{'selected':'@auto','universe':'','gmt':'','min_size':3},['enrichment.tsv','enrichment.png'],'SciPy 超几何检验 / BH 校正',required=['selected','universe','gmt'],auto={'selected':{'steps':[annotation],'artifact':'genes.txt','fallback':''}})
        flow['nodes'].insert(len(flow['nodes'])-2,flow['nodes'].pop())
        flow['order']=[s['id'] for s in flow['nodes']];catalog['workflows'].append(flow)
    # Original upstream sub-phases are explanatory, not fake independently executable steps.
    phases={'singlecell_upstream':['测序与条形码质控','参考基因组与转录本准备','固定所选路线的比对与定量','过滤空液滴与输出细胞矩阵','汇总上游质量报告'],'spatial_segment':['导入组织图像与转录本','组织和细胞分割','按细胞汇总转录本','整理空间表达数据','生成分割与空间质量报告'],'metagenome_profile':['原始测序质控','可选接头与低质量过滤','可选去除宿主序列','按所选软件与数据库分类','汇总标准化物种丰度'],'metagenome_mag':['读段质控与宿主过滤','宏基因组组装','回贴比对与覆盖计算','基因组分箱与优化','MAG 质量、分类与功能注释']}
    for flow in catalog['workflows']:
        for step in flow['nodes']+flow['extraNodes']:
            if step.get('nextflowSource') and 'igenomes_ignore' in step.get('configSchema',{}).get('properties',{}):
                step['configDefaults'].setdefault('igenomes_ignore',True)
                if 'igenomes_base' in step['configSchema']['properties']:step['configDefaults'].setdefault('igenomes_base',None)
            if step['id'] in phases:step['substeps']=phases[step['id']]
    files={p.name:p.read_bytes() for p in BASE.iterdir() if p.suffix in ('.py','.R') and p.name!='catalog_extension.py'}
    files['stages.json']=json.dumps(registry,ensure_ascii=False,indent=2).encode()
    files['SOURCE.md']=b'OMaaX downstream extension; MIT. Original nf-core and PlantAI licenses remain in source bundle.\n'
    for name,raw in files.items():package.writestr('omaax_extensions/'+name,raw);hashes['omaax_extensions/'+name]=hashlib.sha256(raw).hexdigest()
    for file in sorted((BASE/'upstream_sources').rglob('*')):
        if file.is_file():
            name=file.relative_to(BASE).as_posix();raw=file.read_bytes();package.writestr(name,raw);hashes[name]=hashlib.sha256(raw).hexdigest()
    references=json.loads((BASE/'upstream-reference-assets.json').read_text(encoding='utf-8'))
    for asset in references['files']:
        if hashes.get(asset['path'])!=asset['sha256']:raise ValueError('Pinned runtime reference changed: '+asset['path'])
    package.writestr('omaax/reference-assets.json',json.dumps(references,ensure_ascii=False,indent=2))
    package.writestr('omaax/epigenetic-source-manifest.json',json.dumps(provenance,indent=2))
    return catalog
