"""Import reviewed metadata and three CLI files, never execute or install archive contents."""
import ast
import base64
import gzip
import hashlib
import json
from pathlib import Path
import zipfile

ARCHIVE_SHA = '6aead389416aa4acc5cd3abd536a851a9f69c68a4c773c4cfda0959f15dbb4ae'
REPO = Path(__file__).resolve().parents[2]
ARCHIVE = REPO / 'source-inputs/PlantAI_five_omics_scripts_20261013.zip'
OUT = REPO / 'desktop/resources/workflows'

# Only registered entry points are linked; this table groups scientific stages for viewing.
GROUPS = {
 'gene_family': ('基因家族分析', '寻找目标家族基因，查看结构、进化和功能。', [
  ('genome_prepare','准备基因组','Python', '基因组、注释与序列一致性'),
  ('identify','识别家族成员','HMMER / BLAST / InterProScan', 'HMM 模型、匹配阈值、保守结构域'),
  ('identify_refine','检查候选成员','Python', '异构体去重、完整性与边界候选'),
  ('phylogeny','构建进化树','MAFFT / IQ-TREE', '参考序列、比对质量、模型与支持率'),
  ('motif','查看保守基序','MEME', '基序数量、宽度范围'),
  ('domain','查看结构域','InterProScan', '数据库版本与匹配规则'),
  ('synteny','比较共线性','MCScanX', '参考基因组、ID 映射与共线性条件')]),
 'rnaseq': ('RNA-seq 分析', '比较实验条件下的基因表达，生成差异结果。', [
  ('rnaseq_qc','检查测序质量','fastp / FastQC', '过滤条件、质量阈值'),
  ('rnaseq_align','比对测序数据','HISAT2 / samtools', '参考基因组、索引与链特异性'),
  ('rnaseq_quant','汇总表达计数','featureCounts / Salmon', '所选定量路线、注释与计数语义'),
  ('rnaseq_sample_qc','检查样本与设计','R / Python', '计数类型、分组、独立重复与批次'),
  ('rnaseq_de','比较表达差异','DESeq2 / R', 'padj=0.05、|log2FC|=1；本次设计须确认'),
  ('rnaseq_go_enrich','解释差异基因','R / Python', '注释版本、背景基因与富集方法'),
  ('rnaseq_wgcna','WGCNA 共表达分析','WGCNA', '样本数、数据质量、软阈值与模块条件')]),
 'singlecell': ('单细胞分析', '查看细胞群体和表达特征，按供者进行可选组间分析。', [
  ('singlecell_upstream','可选测序上游','nf-core/scrnaseq 4.2.0', '平台、参考基因组与上游路线'),
  ('singlecell_analyze','质控、降维与聚类','Scanpy', '物种相关 QC、HVG、PC、邻居数与分辨率'),
  ('singlecell_annotate','确认细胞类型','Python', '人工确认的细胞类型映射'),
  ('singlecell_pseudobulk','汇总供者级计数','Python', '供者、组别、细胞类型与原始计数层'),
  ('singlecell_de','可选组间统计','DESeq2', '独立供者、配对信息与可估计设计')]),
 'spatial': ('空间转录组分析', '查看组织位置的表达特征与空间关系。', [
  ('spatial_segment','可选细胞分割','nf-core/sopa 1.0.1', '已验收的平台、图像与分割方法'),
  ('spatial_export','整理表达和坐标','Python', '坐标单位、bin/spot/cell 语义与切片身份'),
  ('spatial_analyze','空间表达分析','Scanpy / Squidpy', '切片 QC、空间图、置换次数与表达聚类')]),
 'metagenome': ('宏基因组分析', '比较微生物组成与多样性，可选组装分析。', [
  ('metagenome_profile','可选物种分类','nf-core/taxprofiler 2.0.1', '分类数据库、读取长度与分析路线'),
  ('metagenome_merge','合并物种计数','Bracken / Python', '数据库版本、计数与丰度语义'),
  ('metagenome_community','分析群落组成','R / Python', '多样性指标与样本元数据'),
  ('metagenome_differential','比较群落差异','ANCOM-BC2 / PERMANOVA', '原始计数、实验设计与组内离散度'),
  ('metagenome_mag','可选组装与 MAG','nf-core/mag 5.5.0', '分箱、完整度与污染度；独立验收')])
}

TITLES = {'subfamily_confirm':'确认亚家族','ncbi_fetch_sequences':'获取参考序列','sequence_extract_by_id':'按基因列表提取序列',
 'msa_region_plot':'查看局部序列比对','tree_plot_revise':'调整进化树图','promoter_plot_revise':'调整启动子图',
 'prepare_organize':'整理基因组文件','prepare_normalize_gff':'规范注释格式','prepare_longest_transcript':'选择最长转录本',
 'prepare_extract_pep':'提取蛋白序列','prepare_extract_cds':'提取编码序列','prepare_filter_fasta':'筛选序列',
 'prepare_simplify_ncbi':'整理 NCBI 注释','reference_classification':'参考家族分类','pep':'蛋白理化性质',
 'interspecies_synteny':'跨物种共线性','structure':'基因结构','promoter':'启动子调控元件','tfbs':'转录因子结合位点',
 'ortholog':'直系同源关系','karyotype':'染色体分布','ppi':'蛋白相互作用','kaks':'进化选择压力',
 'combo':'组合图表','annotation':'功能注释','gene_family_report':'汇总分析报告','synteny_plot_revise':'调整共线性图',
 'msa':'多序列比对','expression':'表达特征','singlecell_enrich':'功能富集','spatial_enrich':'空间表达功能富集',
 'metagenome_summarize':'汇总物种结果','rnaseq_kegg_enrich':'KEGG 富集','rnaseq_fgsea':'基因集富集',
 'rnaseq_enrich':'富集入口（历史别名）','rnaseq_candidate_family':'候选基因家族分析','rnaseq_report':'生成转录组报告','enrichment':'通用富集分析'}

def main():
    assert hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() == ARCHIVE_SHA, 'Archive changed: review required'
    OUT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ARCHIVE) as z:
        prefix = 'PlantAI_five_omics_source_20261013/'
        entries = json.loads(z.read(prefix+'ENTRYPOINTS.json'))
        # Actual stage identifiers are resolved from entry metadata; unmatched labels stay unavailable.
        by_stage = {e['stage']: e for e in entries}
        catalog = {'schemaVersion':1, 'archiveSha256':ARCHIVE_SHA, 'version':'20261013.1',
                   'scientificValidation':'pending', 'workflows':[]}
        for key, (name, desc, stages) in GROUPS.items():
            nodes = []
            primary={row[0] for row in stages}
            additional=[(e['stage'],TITLES.get(e['stage'],e['stage']),'正式 Python 入口；专项组件待环境验收','查看正式入口声明的参数，执行前逐项验收') for e in entries if e.get('workflow')==key and e['stage'] not in primary]
            for stage, title, tools, params in [*stages,*additional]:
                entry = by_stage.get(stage)
                if not entry:
                    candidates = [e for e in entries if e.get('workflow') == key and
                                  stage.removeprefix(key+'_') in e['stage']]
                    entry = candidates[0] if len(candidates) == 1 else None
                main_file = entry.get('main_file') if entry else None
                arguments=[]
                if main_file and entry.get('present') and main_file.endswith('.py'):
                    tree=ast.parse(z.read(prefix+main_file).decode('utf-8'))
                    for call in ast.walk(tree):
                        if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Attribute) or call.func.attr!='add_argument':continue
                        flags=[a.value for a in call.args if isinstance(a,ast.Constant) and isinstance(a.value,str)]
                        if not flags:continue
                        options={}
                        for option in call.keywords:
                            if option.arg in ('default','help','choices','required'):
                                try:options[option.arg]=ast.literal_eval(option.value)
                                except (ValueError,TypeError):pass
                        arguments.append({'flags':flags,**options})
                nodes.append({'id':stage, 'name':title, 'software':tools, 'parameters':params,
                              'arguments':arguments,
                              'entry':main_file, 'entryPresent':bool(entry and entry.get('present')),
                              'sourceSha256':hashlib.sha256(z.read(prefix+main_file)).hexdigest() if main_file and entry.get('present') else None,
                              'status':'pending', 'optional':title.startswith('可选')})
            catalog['workflows'].append({'id':key, 'name':name, 'description':desc,
                'status':'pending', 'version':catalog['version'], 'nodes':nodes[:len(stages)], 'extraNodes':nodes[len(stages):],
                'executionMode':'rnaseq-counts-v1' if key=='rnaseq' else None})
        (OUT/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        files = {}
        for rel in ['cli/run_rnaseq_de.py','gfa_core/rnaseq_context.py','gfa_core/rnaseq_de_design.py']:
            data = z.read(prefix+'project_source/bioflow_kernel/'+rel)
            ast.parse(data.decode('utf-8'))
            files[rel] = data.decode('utf-8')
        bundle = json.dumps(files,ensure_ascii=False).encode()
        (OUT/'rnaseq-source.b64').write_text(base64.b64encode(gzip.compress(bundle,mtime=0)).decode(),encoding='ascii')
        audit = {'archiveSha256':ARCHIVE_SHA, 'entryRecords':len(entries),
                 'missingEntries':[e['stage'] for e in entries if not e.get('present')],
                 'vendoredFiles':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in files.items()},
                 'entries':entries, 'executionTestPerformed':False}
        (OUT/'source-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (OUT/'SOURCE.md').write_text('# 流程来源\n\n来自用户提供的 PlantAI_five_omics_scripts_20261013.zip；来源提交 '
            '`214e3b9079c4aa807beb5340642d18969a1e6530`。只收录流程元数据与 RNA-seq 正式 CLI 的三个必要源文件，'
            '保留原文件说明和来源。当前尚未完成 OMaaX 科学验收，不默认启用全部工具。\n\n'
            '归档中的第三方 nf-core 代码未在本阶段打包执行。后续按分支核验原许可证、数据库与专有软件的许可，'
            '不能从项目主许可证推定所有依赖都可再分发。\n',encoding='utf-8')
    print(json.dumps({'workflowCount':len(catalog['workflows']),'entries':len(entries),'vendored':list(files),'bundleBytes':(OUT/'rnaseq-source.b64').stat().st_size}))

if __name__ == '__main__':
    main()
