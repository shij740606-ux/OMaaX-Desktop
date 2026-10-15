// Display copy only: parameter keys, defaults and execution contracts stay unchanged.
const names=`
abundance|丰度表
abundance_engine|表达定量软件
abundance_type|丰度数据类型
action|报告操作
adapter_r1|第一端接头序列
adapter_r2|第二端接头序列
allow_family_download|允许下载全部候选序列
allow_missing|允许部分编号未找到
analysis_route|分析路线标识
annotation_scope|功能注释范围
annotations|细胞类型对应表
api_key|NCBI 访问密钥
applications|功能注释数据库
assay|实验数据类型
auto_annotate|兼容自动注释选项
auto_conserved|自动选择保守区域
background_mode|背景序列范围
background_source|背景基因来源
bioc_helper|功能注释辅助脚本
bioc_rscript|功能注释所用 R 环境
bioc_source|基因注释来源
blast_engine|蛋白序列检索软件
blast_evalue|序列匹配显著性上限
blast_identity_min|序列相似度下限（%）
categories|启动子元件分类
cds1|第一组编码序列文件
cds2|第二组编码序列文件
clean_all_headers|简化全部序列标题
collection|基因集集合
color_by|图形着色依据
comparison|需要分析的组间比较
config|样本与分析配置文件
control|对照组名称
count_matrix|基因计数矩阵
countdown_action|提示结束后的处理方式
counts|计数矩阵文件
counts_layer|使用的计数数据层
counts_source|计数数据来源
coverage|序列覆盖率下限
curated_only|只保留整理后的成员
cut_front|修剪序列前端低质量碱基
cut_ga|使用数据库推荐阈值
cut_mean_quality|滑动窗口平均质量下限
cut_tail|修剪序列末端低质量碱基
cut_window_size|质量检查窗口长度
database_version|参考数据库版本
databases|分类数据库清单
de_dir|差异分析结果目录
de_files|差异基因结果文件
design|组间比较设计
detect_adapter_for_pe|自动识别双端接头
dom_i_evalue_max|结构域匹配显著性上限
doublets|双细胞处理
edited_subfamily|修改后的亚家族分类表
element_desc|启动子元件说明表
elements|需要展示的启动子元件
email|外部服务接收邮箱
enabled|启用此选项
end_col|比对结束列
enrich_type|富集分析类型
evalue|匹配显著性上限
expected_rate|预期双细胞比例
expected_stages|报告包含的分析步骤
expression_file|表达数据文件
expression_matrix|表达量矩阵文件
family_ids|目标家族基因编号文件
family_label|图中家族名称
family_name|目标基因家族名称
fasta|序列文件
fimo_pvalue|结合位点显著性上限
fimo_qvalue|结合位点校正显著性上限
force|重新生成已有结果
foreground_mode|需要分析的基因范围
gene2go|基因与 GO 注释对应表
gene2ko|基因与 KEGG 编号对应表
gene_id|目标基因编号
gene_id_file|基因编号清单文件
gene_ids|目标基因编号列表
gene_list1|第一组基因编号清单
gene_list2|第二组基因编号清单
genome|参考基因组文件
genome_file|参考基因组文件
gff3|基因结构注释文件
gff3_file|基因结构注释文件
gmt|基因集定义文件
go_obo|GO 分类关系文件
gtf|基因注释文件
hisat2_index|比对参考索引
hmm_blast_merge_mode|结构域与序列检索结果合并方式
hmm_combine_mode|多个结构域结果合并方式
hmm_cov_min|结构域覆盖率下限
id|需要提取的序列编号
id_file|成员编号清单
id_transform|基因编号转换方式
id_type|输入编号类型
identity|序列相似度下限（%）
ids|需要提取的编号列表
ids_file|序列编号清单文件
input|输入数据文件
integration|样本整合方法
interproscan_tsv|已有功能注释结果表
invert_y|翻转图中的纵坐标
iqtree_params|进化树软件附加选项
jaspar_meme|结合位点基序数据库
jaspar_metadata|基序数据库说明表
kallisto_bias|校正定量时的序列偏好
kegg_cache_dir|KEGG 参考缓存目录
kegg_filter_scope|KEGG 通路筛选范围
kegg_highlight_mode|通路图突出显示方式
kegg_level|通路分类层级
kegg_mode|KEGG 参考数据模式
kegg_organism|KEGG 物种代码
kegg_species_cache_dir|物种专用 KEGG 缓存目录
key_type|基因编号类型
known_splice_site|已知剪接位点文件
kofam_config|KEGG 注释配置文件
label_gap_mb|染色体图标签间距
length_required|保留序列的最短长度
log2fc|表达变化幅度阈值
mafft_params|序列比对软件附加选项
manifest|已有分析记录文件
match_mode|启动子元件匹配方式
max_deg_proteins|最多分析的差异蛋白数
max_genes|最多用于建网的基因数
max_nearest_rank|允许参考的最近分类层级
max_nuccore|最多检索的核酸记录数
max_pct_mt|线粒体计数比例上限（%）
max_size|基因集大小上限
max_stored_scores|最多保存的匹配分数数目
max_target_seqs|每条序列最多保留的匹配数
maxw|基序最长长度
member_set_id|家族成员集合标识
merge_cut_height|相似模块合并阈值
merge_mode|候选成员合并方式
metadata|样本分组信息表
method|保守区域选择方法
min_cells|每组保留的最少细胞数
min_cells_per_gene|基因表达的最少细胞数
min_counts|细胞计数下限
min_genes|细胞检出基因数下限
min_mean|基因平均表达量下限
min_module_size|每个模块的最少基因数
min_nongap|保守区域非空位比例下限
min_size|基因集大小下限
minw|基序最短长度
mitochondrial_pattern|线粒体基因识别规则
mode|处理方式
module|差异分析模块
motif_db_mode|基序数据库来源
msa_file|多序列比对结果文件
n_base_limit|未知碱基数量上限
n_hvg|用于分析的高变基因数
n_jobs|同时处理的工作数
n_neighbors|每个细胞的邻居数量
n_pcs|用于分析的主成分数
n_permutations|随机置换次数
neighborhood_label|空间邻域分组字段
network_type|蛋白互作网络类型
neutral_rate|中性替换速率
nmotifs|寻找的基序数量
no_kegg_network|禁止联网获取 KEGG 数据
no_model|生成报告时不调用模型
nperm|随机置换次数
operation|数据整理操作
output_label|图形结果名称
output_prefix|结果文件名前缀
overwrite_gene_subfamily|替换已有亚家族分类表
padj|校正后的显著性上限
paired|按配对样本处理
pairs|基因配对关系表
panels|组合图包含的内容
pep1|第一组蛋白序列文件
pep2|第二组蛋白序列文件
permanova|组间组成差异检验
pfam_base|结构域数据库位置
pfam_dir|结构域数据库目录
plantcare_max_records|每批上传的最多序列数
plantcare_max_upload_bytes|每批上传的最大文件大小（字节）
plantcare_submit_timeout|启动子查询提交等待时间（秒）
plantcare_tab|已有启动子注释结果
planttfdb_root|植物转录因子数据库目录
plot_mode|共线性图形类型
plot_modes|需要生成的图形类型
plot_type|富集结果图形类型
plots|需要生成的统计图
point_size|空间图中的点大小
powers|候选软阈值列表
prefix_name|结果名称
profile|运行方式配置
profiler|物种分类软件
promoter_file|启动子分析结果文件
protein_fasta|蛋白序列文件
proteins|蛋白序列文件
pvalue_cutoff|原始显著性上限
qc|质量过滤条件
qc_tool|测序质量检查软件
qualified_quality_phred|合格碱基质量下限
quant_mode|表达定量方式
query|查询内容或查询序列
qvalue_cutoff|多重检验校正显著性上限
ref_file|参考蛋白序列文件
ref_protein|参考物种蛋白序列文件
ref_root|参考数据目录
ref_species|参考物种代码
reference_end_residue|参考序列结束位置
reference_id|参考序列编号
reference_prefix|参考物种编号前缀
reference_start_residue|参考序列起始位置
reference_subfamily|参考亚家族分类表
rename_mode|提取后序列命名方式
replot|重新生成组合图
require_kegg|要求生成 KEGG 注释
required_pfams|目标结构域编号
resolution|细胞分群精细程度
resume_existing|接着已有结果继续执行
retention_days|缓存保留天数
retmax|每次最多检索的记录数
reuse_tree|沿用已有进化树
rnaseq_scope_id|关联转录组分析标识
run_interproscan|重新进行蛋白功能注释
salmon_libtype|测序文库方向类型
sample_config|样本配置文件
sample_file|样本信息文件
sample_parallel_jobs|同时处理的样本数
sample_table|样本与分组信息表
samples|样本信息表
scan_both_strands|扫描正反两条链
scope_file|分析范围配置文件
scope_id|分析范围标识
score|互作证据分数下限
seed|随机结果复现编号
selected|待分析基因清单
seq_type|提取的序列类型
seq_types|下载的序列类型
show_progress_bar|显示处理进度
skip_existing|沿用已有功能注释
skip_existing_kegg|沿用已有 KEGG 注释
skip_go|跳过 GO 注释
skip_kegg|跳过 KEGG 注释
skip_meme|跳过基序重新查找
skip_pathview|不生成原生通路图
skip_ref_warning|跳过参考序列缺失提醒
source_fasta|成员来源序列文件
source_manifest|关联分析记录文件
source_prediction_manifest|已有亚家族预测记录
source_prediction_prefix|已有预测结果名称
spatial_graph|空间邻居构建方式
species|物种名称或代码
species_name|物种名称
splice_site_source|剪接位点信息来源
stage_statuses_json|已有步骤执行状态
start_col|比对起始列
subfamily|亚家族分类表
subject|被比较物种的蛋白序列
table|需要导出的数据表
target_prefix|目标物种编号前缀
target_protein|目标物种蛋白序列文件
target_species|目标物种代码
target_species_name|目标物种名称
taxdump|物种分类关系数据库
taxid|NCBI 物种分类编号
taxon_names|分类编号与名称对应表
taxonomic_rank|物种统计层级
threads_per_sample|每个样本使用的线程数
timeout|查询最长等待时间（秒）
top_motifs|最多展示的基序数
top_n|最多展示的条目数
top_pathways|每组最多展示的富集条目
top_taxa|最多展示的物种数量
tpm_file|标准化表达量矩阵
transcript_policy|下载转录本的选择规则
transcriptome_fasta|参考转录本序列文件
treated|处理组名称
tree|已有进化树文件
tree_layout|进化树展示方式
tree_subfamily_arc_enabled|显示亚家族圆弧
tree_subfamily_arc_gap|圆弧与基因标签间距
tree_subfamily_arc_label_gap|圆弧与亚家族名称间距
tree_subfamily_arc_label_size|亚家族名称字号
triggered_by|表达分析触发来源
tx2gene|转录本与基因对应表
tximport_counts_from_abundance|计数矩阵转换方式
universe|背景基因清单
unqualified_percent_limit|低质量碱基比例上限（%）
upstream_length|启动子上游提取长度
use_blastp|改用 BLASTP 检索
vst_file|方差稳定化表达矩阵
window_len|保守区域窗口长度
workspace_root|关联分析工作目录
write_gene_subfamily|保存基因亚家族对应表
`.trim().split('\n').map(line=>line.split('|'));

export const parameterNames:Record<string,string>=Object.fromEntries(names);
Object.assign(parameterNames,Object.fromEntries(`
adapterremoval_adapter1|接头修剪的第一端序列
adapterremoval_adapter2|接头修剪的第二端序列
adapterremoval_minquality|接头修剪的最低碱基质量
adapterremoval_trim_quality_stretch|按连续低质量区域修剪
aggregate_channels|汇总细胞内图像通道信号
aggregate_genes|汇总细胞内基因计数
ale_per_base_output|输出逐碱基组装评估
aligner|单细胞比对软件
allow_disconnected_polygon|允许不连通的细胞边界
alpha|空间分割形状调节系数
ancient_dna|使用古 DNA 处理流程
assembly_input|已有组装结果样本表
barcode_whitelist|允许的细胞条形码清单
baysor_scale|Baysor 细胞尺度
baysor_scale_std|Baysor 细胞尺度变化范围
bbnorm|标准化测序深度
bbnorm_min|保留的最低测序深度
bbnorm_target|目标最大测序深度
bcftools_view_high_variant_quality|高可信变异的质量下限
bcftools_view_medium_variant_quality|中等可信变异的质量下限
bcftools_view_minimal_allelesupport|支持变异的最少碱基数
bin_concoct_chunksize|分箱前的序列片段长度
bin_concoct_donotconcatlast|不合并末端短片段
bin_concoct_overlap|相邻分箱片段重叠长度
bin_domain_classification|按生物分类域筛选分箱
bin_domain_classification_tool|分类域判断软件
bin_max_size|保留分箱的最大长度
bin_metabinner_scale|MetaBinner 数据规模
bin_min_size|保留分箱的最小长度
bin_seqkit_stats_max_forks|分箱统计的并行任务上限
binning_map_mode|分箱丰度比对样本范围
bowtie2_mode|组装结果回贴比对选项
bracken_save_intermediatekraken2|保存 Bracken 使用的中间分类结果
busco_clean|清理本任务的 BUSCO 临时文件
busco_db|BUSCO 完整性参考数据库
busco_db_lineage|BUSCO 参考谱系
cat_allow_unofficial_lineages|允许使用非官方分类谱系
cat_classify_unbinned|对未分箱序列进行分类
cat_db|CAT 物种分类数据库
cat_db_generate|生成 CAT 分类数据库
cat_no_suggestive_asterisks|不标记推测性的分类结果
cellpose_channels|Cellpose 使用的图像通道
cellpose_diameter|Cellpose 预期细胞直径
cellpose_kwargs|Cellpose 附加分割选项
cellpose_model_type|Cellpose 分割模型
cellpose_use_gpu|Cellpose 使用 GPU
cellprob_threshold|细胞识别概率阈值
cellranger_index|Cell Ranger 参考索引
cellranger_multi_barcodes|Cell Ranger 多样本条形码清单
cellranger_vdj_index|免疫受体参考索引
cellrangerarc_config|多组学样本配置文件
cellrangerarc_reference|多组学参考数据
centrifuge_save_reads|保存 Centrifuge 分类序列
check_counts|检查计数矩阵
checkm2_db|CheckM2 完整性数据库
checkm2_db_version|CheckM2 数据库版本
checkm_db|CheckM 完整性数据库
checkm_download_url|CheckM 数据库下载地址
clahe_kernel_size|图像局部对比度窗口大小
clip_limit|图像对比度增强上限
clip_tool|测序序列修剪软件
coassemble_group|按样本组共同组装
command_line_suffix|软件命令显示附加文本
confidence_nn_id|最近邻判断可信度
diamond_output_format|DIAMOND 结果格式
diamond_save_reads|保存 DIAMOND 分类序列
email_on_fail|失败时发送通知的邮箱
exclude_unbins_from_postbinning|后续分析排除未分箱序列
expand_radius_ratio|细胞边界扩展比例
fastp_cut_mean_quality|fastp 修剪平均质量下限
fastp_qualified_quality|fastp 合格碱基质量下限
fastp_save_trimmed_fail|保存未通过质量过滤的序列
fastp_trim_polyg|修剪末端连续 G 碱基
fb_reference|抗体或特征条形码参考文件
filtlong_filtering_by_shortreads|借助短读段过滤长读段
flow_threshold|细胞分割流场阈值
fluorescence_cell_type_key|荧光细胞类型标注字段
force_2d|按二维图像处理
freebayes_min_basequality|变异检测最低碱基质量
freebayes_minallelefreq|变异检测最低等位基因频率
freebayes_ploidy|变异检测倍性
ganon_report_maxcount|分类报告最大计数
ganon_report_mincount|分类报告最小计数
ganon_report_rank|分类报告层级
ganon_report_toppercentile|分类报告保留百分位
ganon_report_type|分类报告类型
ganon_save_readclassifications|保存 Ganon 逐序列分类
gaussian_sigma|图像平滑程度
generate_bigmag_file|输出汇总的大型分箱文件
genomad_db|病毒识别数据库
genomad_min_score|病毒识别最低分数
genomad_splits|病毒数据库分块数
gex_barcode_sample_assignment|条形码与样本分配表
gex_cmo_set|细胞样本标签集合
gex_frna_probe_set|固定 RNA 探针集合
gex_reference_version|基因表达参考版本
gex_target_panel|目标基因面板
gtdb_db|GTDB 物种分类数据库
gtdbtk_max_contamination|物种分类允许的污染度上限
gtdbtk_min_af|物种分类最低比对比例
gtdbtk_min_completeness|物种分类最低完整性
gtdbtk_min_perc_aa|物种分类最低氨基酸比对比例
gtdbtk_place_species|进行物种位置判定
gtdbtk_pplacer_cpus|物种定位线程数
gtdbtk_pplacer_useram|物种定位优先使用内存
gtdbtk_single_job|物种分类单任务运行
gtdbtk_skip_aniscreen|跳过物种相似度预筛选
gtdbtk_use_full_tree|使用完整物种参考树
gunc_database_type|污染检测参考库类型
gunc_db|污染检测参考数据库
gunc_save_db|保存污染检测参考库
help|显示简要软件帮助
help_full|显示完整软件帮助
host_fasta|宿主参考序列文件
host_fasta_bowtie2index|宿主参考比对索引
host_genome|宿主参考基因组名称
host_removal_save_ids|保存宿主序列编号
host_removal_verysensitive|使用高灵敏度宿主过滤
hostremoval_reference|宿主过滤参考数据
hvg|高变基因筛选设置
igenomes_base|参考基因组集合目录
igenomes_ignore|不使用内置参考基因组集合
image_scale|图像缩放比例
infer_presets|自动推断分割预设
kaiju_expand_viruses|扩展病毒分类范围
kaiju_taxon_rank|Kaiju 分类层级
kallisto_index|Kallisto 参考索引
kb_t1c|第一端细胞条形码转换规则
kb_t2c|第二端细胞条形码转换规则
kb_workflow|Kallisto 单细胞处理方式
keep_lambda|保留噬菌体对照序列
keep_phix|保留测序对照序列
kmcp_save_search|保存 KMCP 检索结果
kraken2_save_minimizers|保存 Kraken2 最小子串统计
kraken2_save_readclassifications|保存 Kraken2 逐序列分类
kraken2_save_reads|保存 Kraken2 分类序列
krakenuniq_batch_size|KrakenUniq 每批数据量
krakenuniq_ram_chunk_size|KrakenUniq 内存分块大小
krakenuniq_save_readclassifications|保存 KrakenUniq 逐序列分类
krakenuniq_save_reads|保存 KrakenUniq 分类序列
krona_taxonomy_directory|分类可视化参考库目录
lambda_reference|噬菌体对照参考序列
lazy|延迟加载空间数据
level|空间图像层级
longread_adapterremoval_tool|长读段接头去除软件
longread_adaptertrimming_tool|长读段接头修剪软件
longread_filter_tool|长读段过滤软件
longread_filtering_tool|长读段质量过滤软件
longread_hostremoval_index|长读段宿主过滤索引
longread_percentidentity|长读段回贴相似度下限
longread_qc_adapterlist|长读段接头清单
longread_qc_predictadapters|自动预测长读段接头
longread_qc_qualityfilter_keeppercent|保留长读段的比例
longread_qc_qualityfilter_minlength|长读段质量过滤的最短长度
longread_qc_qualityfilter_minquality|长读段质量过滤的最低质量
longread_qc_qualityfilter_targetbases|长读段保留的目标碱基总量
longread_qc_skipadaptertrim|跳过长读段接头修剪
longread_qc_skipqualityfilter|跳过长读段质量过滤
longreads_keep_percent|长读段保留比例
longreads_length_weight|长读段长度权重
longreads_min_length|保留长读段的最短长度
longreads_min_quality|保留长读段的最低质量
malt_generate_megansummary|生成分类汇总报告
malt_mode|MALT 比对模式
malt_save_reads|保存 MALT 分类序列
marker_cell_dict|标记基因与细胞类型对应表
max_cell_radius|最大细胞半径
max_multiqc_email_size|邮件报告附件大小上限
max_unbinned_contigs|保留未分箱序列数量上限
mean_cell_diameter|平均细胞直径
megahit_fix_cpu_1|MEGAHIT 初始固定线程数
megahit_options|MEGAHIT 附加组装选项
melon_k2_db|Melon 分类参考库
metabat_rng_seed|MetaBAT 随机复现编号
metacache_abundances|MetaCache 丰度模式
metaeuk_db|真核基因预测参考库
metaeuk_mmseqs_db|真核基因预测的 MMseqs 参考库
metaphlan_save_samfiles|保存 MetaPhlAn 比对文件
min_area_microns2|细胞最小面积（平方微米）
min_area_pixels2|细胞最小面积（像素）
min_contig_size|组装序列最短长度
min_intensity_ratio|图像信号强度比例下限
min_length_unbinned_contigs|保留未分箱序列最短长度
min_molecules_per_cell|每个细胞的最少分子数
min_molecules_per_gene|每个基因的最少分子数
min_molecules_per_segment|每个分割区域的最少分子数
min_rna_per_cell|每个细胞的最少 RNA 数
min_transcripts|最少转录本数
monochrome_logs|使用纯色日志
motifs|基序筛选设置
motus_longprep_minlen|物种标记分析的长读段最短长度
motus_longprep_splittinglength|长读段切分长度
motus_remove_ncbi_ids|分类结果不输出 NCBI 编号
motus_save_mgc_read_counts|保存物种标记基因计数
motus_save_split_longreads|保存切分后的长读段
motus_use_relative_abundance|输出相对丰度
multiqc_config|汇总质量报告配置
multiqc_logo|汇总质量报告标志图
multiqc_methods_description|汇总质量报告方法说明
multiqc_title|汇总质量报告标题
nms_thresh|细胞候选区域重叠过滤阈值
norm_vector|图像归一化向量
patch_overlap_microns|图像块重叠宽度（微米）
patch_overlap_pixel|图像块重叠宽度（像素）
patch_width_microns|图像块宽度（微米）
patch_width_pixel|图像块宽度（像素）
perform_longread_hostremoval|过滤长读段宿主序列
perform_longread_qc|检查长读段质量
perform_runmerging|合并同一样本的测序批次
perform_shortread_complexityfilter|过滤低复杂度短读段
perform_shortread_hostremoval|过滤短读段宿主序列
perform_shortread_qc|检查短读段质量
perform_shortread_redundancyestimation|估计短读段重复程度
phix_reference|测序对照参考序列
pipelines_testdata_base_path|流程测试数据目录
pixel_size|图像像素对应的实际大小
plaintext_email|通知邮件使用纯文本
postbinning_input|已有分箱结果清单
preprocessing_qc_tool|预处理质量检查软件
pretrained_model|预训练细胞分割模型
prior_segmentation_confidence|已有细胞边界可信度
prior_shapes_key|已有细胞边界字段
prob_thresh|细胞候选区域概率阈值
prokka_compliance_centre|标准注释中的机构名称
prokka_fast_mode|使用快速基因组注释
prokka_with_compliance|使用标准提交注释格式
protocol|测序实验技术
publish_dir_mode|结果文件发布方式
pydamage_accuracy|古 DNA 损伤估计精度
qcatch_n_partitions|单细胞质控分块数
ram_threshold_gb|内存阈值（GiB）
reads_minlength|保留读段最短长度
refine_bins_dastool|使用 DAS Tool 整合分箱
refine_bins_dastool_savecontig2bin|保存组装序列与分箱对应表
refine_bins_dastool_threshold|分箱整合最低分数
remove_doublets|去除疑似双细胞
save_align_intermeds|保存比对中间文件
save_analysis_ready_fastqs|保存分析用测序文件
save_assembly_mapped_reads|保存回贴组装结果的读段
save_bbnorm_reads|保存深度标准化读段
save_busco_db|保存 BUSCO 参考库
save_cat_db|保存 CAT 分类参考库
save_checkm2_data|保存 CheckM2 中间数据
save_checkm_data|保存 CheckM 中间数据
save_clipped_reads|保存修剪后的读段
save_complexityfiltered_reads|保存复杂度过滤后的读段
save_filtered_longreads|保存过滤后的长读段
save_hostremoval_bam|保存宿主过滤比对文件
save_hostremoval_index|保存宿主过滤索引
save_hostremoval_unmapped|保存未匹配宿主的读段
save_hostremoved_reads|保存宿主过滤后的序列
save_lambdaremoved_reads|保存噬菌体对照过滤后的读段
save_mmseqs_db|保存序列检索参考库
save_phixremoved_reads|保存测序对照过滤后的读段
save_porechop_reads|保存长读段接头修剪结果
save_preprocessed_reads|保存预处理读段
save_reference|保存生成的参考索引
save_runmerged_reads|保存合并批次后的读段
save_untarred_databases|保存解压后的分类数据库
semibin_environment|分箱模型适用的环境类型
semibin_rng_seed|SemiBin 随机复现编号
seq_center|测序机构名称
shortread_complexityfilter_bbduk_mask|标记低复杂度区域而非丢弃序列
shortread_complexityfilter_bbduk_windowsize|复杂度检查窗口长度
shortread_complexityfilter_entropy|序列复杂度最低信息熵
shortread_complexityfilter_fastp_threshold|fastp 序列复杂度阈值
shortread_complexityfilter_prinseqplusplus_dustscore|低复杂度识别分数阈值
shortread_complexityfilter_prinseqplusplus_mode|低复杂度识别方式
shortread_complexityfilter_tool|短读段复杂度过滤软件
shortread_hostremoval_index|短读段宿主过滤索引
shortread_percentidentity|短读段回贴相似度下限
shortread_qc_adapter1|短读段第一端接头序列
shortread_qc_adapter2|短读段第二端接头序列
shortread_qc_adapterlist|短读段接头清单
shortread_qc_dedup|去除重复短读段
shortread_qc_includeunmerged|保留未能合并的双端读段
shortread_qc_mergepairs|合并重叠双端读段
shortread_qc_minlength|短读段质量过滤的最短长度
shortread_qc_skipadaptertrim|跳过短读段接头修剪
shortread_qc_tool|短读段质量检查软件
shortread_redundancyestimation_mode|重复读段估计方式
show_hidden|显示隐藏软件选项
simpleaf_index|Simpleaf 参考索引
simpleaf_umi_resolution|分子标签去重方式
single_end|使用单端测序数据
skip_adapter_trimming|跳过接头修剪
skip_ancient_damagecorrection|跳过古 DNA 损伤校正
skip_binning|跳过基因组分箱
skip_binqc|跳过分箱质量检查
skip_cellranger_renaming|跳过 Cell Ranger 文件重命名
skip_cellrangermulti_vdjref|跳过免疫受体参考准备
skip_clipping|跳过读段修剪
skip_emptydrops|跳过空液滴过滤
skip_longread_filtering|跳过长读段过滤
skip_longread_qc|跳过长读段质量检查
skip_preprocessing_qc|跳过原始数据质量检查
skip_shortread_qc|跳过短读段质量检查
spaceranger_probeset|Space Ranger 探针集合
spaceranger_reference|Space Ranger 参考数据
spades_downstreaminput|SPAdes 后续分析序列类型
spades_fix_cpus|SPAdes 固定线程数
spades_options|SPAdes 附加组装选项
spadeshybrid_fix_cpus|混合组装固定线程数
standardisation_motus_generatebiom|生成标准丰度交换文件
standardisation_taxpasta_format|标准分类表输出格式
star_feature|单细胞表达计数范围
star_ignore_sjdbgtf|忽略参考剪接注释
star_index|STAR 比对索引
stardist_channels|StarDist 使用的图像通道
stardist_kwargs|StarDist 附加分割选项
stardist_model_type|StarDist 分割模型
sylph_data_type|Sylph 丰度指标
sylph_taxonomy|Sylph 分类关系表
taxpasta_add_idlineage|输出完整分类编号路径
taxpasta_add_lineage|输出完整分类名称路径
taxpasta_add_name|输出分类名称
taxpasta_add_rank|输出分类层级
taxpasta_add_ranklineage|输出完整分类层级路径
taxpasta_ignore_errors|跳过无法处理的分类文件
taxpasta_taxonomy_dir|分类关系数据库目录
technology|空间数据实验技术
tiara_min_length|分类域识别最短序列长度
tissue_segmentation_kwargs|组织分割附加选项
trace_report_suffix|执行跟踪报告名称后缀
transcript_fasta|参考转录本序列文件
txp2gene|转录本与基因对应表
unassigned_value|未分配细胞的转录本标记值
use_fluorescence_annotation|使用荧光信号标注细胞类型
use_scanpy_preprocessing|使用 Scanpy 数据预处理
use_tissue_segmentation|进行组织区域分割
validate_params|检查软件参数格式
vdj_inner_enrichment_primers|免疫受体富集引物文件
version|显示软件版本
visium_hd_imread_page|读取的空间图像页
visium_hd_prior_shapes_key|高分辨率空间细胞边界字段
`.trim().split('\n').map(line=>line.split('|'))));

const tools:Record<string,string>={ale:'ALE 组装评估',bracken:'Bracken 丰度估计',busco:'BUSCO 完整性评估',centrifuge:'Centrifuge 物种分类',checkm:'CheckM 完整性评估',checkm2:'CheckM2 完整性评估',diamond:'DIAMOND 蛋白检索',ganon:'Ganon 物种分类',gunc:'GUNC 污染检查',kaiju:'Kaiju 物种分类',kmcp:'KMCP 物种检索',kraken2:'Kraken2 物种分类',krakenuniq:'KrakenUniq 物种分类',krona:'Krona 分类可视化',malt:'MALT 分类比对',melon:'Melon 丰度估计',metacache:'MetaCache 物种分类',metaphlan:'MetaPhlAn 物种组成',motus:'mOTUs 标记基因分析',profile_standardisation:'分类结果标准化',pypolca:'组装序列校正',sylph:'Sylph 丰度估计',virus_identification:'病毒识别',cellbender:'背景 RNA 去除',comebin:'COMEBin 分箱',concoct:'CONCOCT 分箱',fastqc:'FastQC 质量检查',flye:'Flye 长读段组装',gtdbtk:'GTDB 物种分类',maxbin2:'MaxBin2 分箱',megahit:'MEGAHIT 组装',metabat2:'MetaBAT2 分箱',metabinner:'MetaBinner 分箱',metaeuk:'MetaEuk 真核基因预测',metamdbg:'MetaMDBG 组装',multiqc:'MultiQC 质量报告',prodigal:'Prodigal 基因预测',prokka:'Prokka 基因组注释',qcatch:'Qcatch 单细胞质控',quast:'QUAST 组装评估',semibin:'SemiBin 分箱',spades:'SPAdes 短读段组装',spadeshybrid:'SPAdes 混合组装',baysor:'Baysor 细胞分割',cellpose:'Cellpose 细胞分割',comseg:'ComSeg 细胞分割',proseg:'ProSeg 细胞分割',stardist:'StarDist 细胞分割'};
for(const [key,label] of Object.entries(tools))for(const [prefix,verb] of [['run','运行'],['skip','跳过'],['use','使用']])parameterNames[prefix+'_'+key]=verb+label;

export const parameterHints:Record<string,string>={
 msa_file:'填写已有的多序列比对结果（例如 .fa 或 .fasta），不是未经比对的原始序列。',
 sample_table:'样本名称应与表达矩阵中的样本一致；分组信息填写在这个表中。',
 expression_matrix:'填写用于建网的表达矩阵文件，基因与样本排列按流程脚本要求准备。',
 count_matrix:'填写基因计数矩阵；不要用百分比或已取对数的表达量替代原始计数。',
 padj:'数值越小，保留的差异基因越少；是否调整应根据你的分析要求决定。',
 log2fc:'控制保留的表达变化幅度；阈值越大，变化幅度较小的基因越容易被过滤。',
 min_mean:'过滤平均表达量较低的基因。若改动，会改变进入共表达分析的基因集合。',
 max_genes:'限制用于建网的基因数量；增加数量通常需要更多内存和更长计算时间。',
 min_module_size:'限制模块的最少基因数；较大的值会减少很小的模块。',
 merge_cut_height:'控制相近模块是否合并。调整会改变最终模块划分。',
 powers:'从这组候选值中选择建网软阈值，多个数值用逗号分隔。',
 resolution:'控制分群精细程度；通常增大后会得到更多细胞群。',
 n_hvg:'选择参与降维分析的高变基因数量；改变它会影响后续分群。',
 n_neighbors:'控制构建邻居图时参考的邻居数量，会影响局部结构和分群结果。',
 seed:'相同数据和设置下使用相同编号，有助于复现含随机步骤的结果。',
 control:'填写样本表中实际存在的对照组名称，必须与表中的拼写一致。',
 treated:'填写样本表中实际存在的处理组名称，必须与表中的拼写一致。',
 sample_parallel_jobs:'同时处理更多样本会增加总 CPU 和内存需求，须与任务资源配额协调。',
 threads_per_sample:'每个样本占用的线程数；多个样本同时运行时要计算总线程需求。',
 start_col:'从第几列开始展示，第一列填写 1。',
 end_col:'展示到第几列结束，包含该列；应不小于起始列。',
 upstream_length:'从基因上游提取的碱基数，会改变用于启动子分析的序列范围。',
 required_pfams:'填写要搜索的 Pfam 结构域编号，多个编号按流程要求分隔。',
 pvalue_cutoff:'控制原始检验显著性筛选；最终结果还应结合校正显著性阈值。',
 qvalue_cutoff:'控制多重检验校正后的筛选范围；越小通常越严格。',
 neutral_rate:'用于将替换数量换算为时间；需要与你研究的物种及时间单位相匹配。',
 min_cells:'样本或分组是否保留取决于这个细胞数阈值，请结合输入数据调整。',
 max_pct_mt:'线粒体计数比例高于此阈值的细胞会被过滤。',
 profile:'填写服务器已支持的运行配置名称；不要在这里输入 shell 命令。',
 api_key:'只供该数据服务访问使用；不要填写模型密钥或 SSH 密码。',
 config:'已有配置文件时填写路径；不确定内容时先准备样本表，再在对话中补齐配置。',
 minw:'寻找的基序长度下限，应不大于长度上限。',
 maxw:'寻找的基序长度上限，应不小于长度下限。',
 nperm:'增加次数可提高随机检验的分辨率，但会增加计算时间。',
 n_permutations:'增加次数会增加计算时间；保持一致便于比较重复运行结果。',
 force:'仅在确实需要重新生成本任务结果时启用；不会赋予修改原始数据的权限。',
 no_kegg_network:'关闭在线获取时需要事先准备本地 KEGG 参考数据。',
 mafft_params:'软件专用附加选项。没有明确需要时留空或保留默认；这不是任意命令输入框。',
 iqtree_params:'软件专用附加选项。没有明确需要时留空或保留默认；这不是任意命令输入框。',
};

const inputKeys=new Set('input fasta proteins protein_fasta source_fasta msa_file config sample_config sample_table sample_file samples count_matrix expression_matrix expression_file counts metadata control treated annotations selected universe gmt gtf gff3 gff3_file genome genome_file hisat2_index ref_file ref_protein target_protein cds1 cds2 pep1 pep2 query subject pairs gene2go gene2ko go_obo gene_id_file id ids ids_file id_file family_ids promoter_file tree de_files interproscan_tsv databases abundance'.split(' '));
const commonKeys=new Set('padj log2fc species species_name family_name required_pfams dom_i_evalue_max hmm_cov_min blast_evalue blast_identity_min evalue coverage identity nmotifs minw maxw min_mean max_genes min_module_size merge_cut_height powers resolution n_hvg n_pcs n_neighbors min_genes min_counts max_pct_mt min_cells min_cells_per_gene upstream_length top_pathways comparison nperm n_permutations min_size max_size seed technology aligner protocol quant_mode sample_parallel_jobs threads_per_sample start_col end_col auto_conserved reference_id window_len'.split(' '));

export type ParameterSchema={type?:string;label?:string;description?:string;default?:unknown;enum?:unknown[];properties?:Record<string,ParameterSchema>;items?:ParameterSchema;required?:boolean|string[];userVisible?:boolean;necessity?:string};
export function parameterCopy(key:string,stepId:string,schema:ParameterSchema={},value?:unknown,required=false){
 let name=schema.label&&/[\u4e00-\u9fff]/.test(schema.label)?schema.label:parameterNames[key]||'扩展设置';
 const isRequired=required||schema.required===true;
 const input=inputKeys.has(key);
 const empty=value===null||value===undefined||value==='';
 const necessity=isRequired?'需要填写':schema.necessity||(input?'按数据填写':commonKeys.has(key)?'按需调整':'通常无需修改');
 let hint=schema.description&&/[\u4e00-\u9fff]/.test(schema.description)?schema.description:parameterHints[key]||(input?(isRequired?'请填写本次数据对应的文件或内容。':'按本次数据填写；留空是否可自动查找取决于这一步的配置。'):commonKeys.has(key)?'有明确分析要求时再调整；保留默认值即可沿用当前流程设置。':'没有明确需要时保留默认值；修改仅影响本步骤的设置。');
 if(key==='query'&&stepId==='ortholog')hint='填写查询物种的蛋白序列文件。';
 if(key==='input'&&/upstream|profile|mag/.test(stepId))hint='填写本次样本清单文件，具体列名按所用流程要求准备。';
 if(key==='min_cells'&&stepId==='singlecell_pseudobulk')hint='供者的每个细胞群至少需要的细胞数；不足的组合不会进入汇总。';
 if(key==='max_genes'&&/singlecell_analyze|spatial_analyze/.test(stepId)){name='检出基因数上限';hint='检出基因数量高于此阈值的细胞或空间位置会被过滤；留空表示不设置上限。';}
 if(key==='resolution'&&stepId==='spatial_analyze'){name='空间分群精细程度';hint='控制空间数据分群的精细程度；通常增大后会得到更多群组。';}
 if(key==='mode'&&stepId==='ncbi_fetch_sequences'){name='参考序列检索范围';hint='选择按基因名称还是按家族进行检索。';}
 if(key==='query'&&stepId==='ortholog')name='查询物种蛋白序列文件';
 const common=isRequired||input||commonKeys.has(key)||typeof value==='object'&&value!==null&&!Array.isArray(value);
 return {name,necessity,hint,common,empty};
}

const optionNames:Record<string,string>={auto:'自动选择',none:'不使用',off:'关闭',on:'启用',true:'启用',false:'关闭',both:'同时使用两种',all:'全部',manual:'手动指定',annotation:'从注释文件读取',generate:'生成报告',open:'打开已有报告',rectangular:'矩形图',circular:'圆形图',intersection:'取交集',union:'取并集',protein:'蛋白序列',pep:'蛋白序列',cds:'编码序列',refseq_preferred:'优先使用参考序列',canonical:'代表转录本',longest:'最长转录本',orgdb:'内置物种注释库',gene2go:'用户提供的 GO 注释表',gene2ko:'用户提供的 KEGG 注释表',go:'GO 功能富集',kegg:'KEGG 通路富集',global:'全部基因',family:'目标家族基因',bubble:'气泡图',bar:'条形图',species:'物种',genus:'属',no:'不做转换',paired:'配对处理',single:'单端数据',keep:'保留原名称'};
Object.assign(optionNames,{continue:'继续处理',cancel:'取消处理',gene:'按单个基因',geneid:'基因数据库编号',symbol:'基因名称',locus_tag:'基因座标签',query:'按查询编号命名',prefix_index:'前缀加顺序编号',simple:'简化名称',exact:'完整匹配',contains:'包含指定内容',regex:'按匹配规则',element:'按元件',category:'按分类',multi:'多物种图',pairwise:'两两比较图',functional:'功能关联',physical:'直接物理互作',dual:'计数与丰度同时计算',count_only:'只生成计数',scaledTPM:'按标准化丰度缩放',lengthScaledTPM:'同时按长度和丰度缩放',dtuScaledTPM:'按转录本使用量缩放',pathway:'通路层级',ko:'功能编号层级',plant:'植物相关通路',organism:'物种专用通路',svg:'矢量图',r_plot:'统计绘图',remove_T01:'去掉转录本后缀',add_T01:'添加转录本后缀',always:'始终执行',never:'不执行',standard:'标准流程',lamanno:'包含 RNA 速度分析',nac:'核内与细胞质模式',symlink:'创建符号链接',rellink:'创建相对链接',link:'创建硬链接',copy:'复制结果',copyNoFollow:'复制但不追踪链接',move:'移动本任务结果',toy_dataset:'小型示例数据',entropy:'按信息熵',dust:'按低复杂度分数',kmer:'按序列短片段',alignment:'按序列比对',Unknown:'自动判定',BlastN:'核酸序列比对',BlastP:'蛋白序列比对',BlastX:'翻译后蛋白比对',Classifier:'物种分类',abundance:'丰度报告',reads:'读段报告',matches:'匹配报告',dist:'分布报告',corr:'校正报告',relative_abundance:'相对丰度',sequence_abundance:'序列丰度',scaffolds:'拼接骨架序列',contigs:'连续组装序列',group:'同组样本',own:'本样本',small:'小型数据',large:'大型数据',huge:'超大型数据',human_gut:'人肠道',dog_gut:'犬肠道',ocean:'海洋',soil:'土壤',cat_gut:'猫肠道',human_oral:'人口腔',mouse_gut:'小鼠肠道',pig_gut:'猪肠道',built_environment:'建筑环境',wastewater:'废水',chicken_caecum:'鸡盲肠',raw_bins_only:'原始分箱',refined_bins_only:'优化分箱',test_data:'测试数据',phylum:'门',class:'纲',order:'目',genus:'属',Gene:'按基因外显子计数',GeneFull:'按完整基因计数', 'Gene Velocyto':'基因计数与 RNA 速度矩阵','cr-like':'按 Cell Ranger 方式去重','cr-like-em':'按 Cell Ranger 方式并估计歧义归属',parsimony:'按最简归属去重','parsimony-em':'按最简归属并估计歧义归属','parsimony-gene':'按基因最简归属去重','parsimony-gene-em':'按基因最简归属并估计歧义归属'});
export const optionLabel=(value:unknown,key='')=>{
 const text=String(value);
 if(key==='reference_point')return ({center:'区域中点',TSS:'转录起点（TSS）',TES:'转录终点（TES）'} as Record<string,string>)[text]||text;
 if(key==='salmon_libtype')return ({A:'自动识别文库布局',U:'单端 · 非链特异性',SF:'单端 · 正向链',SR:'单端 · 反向链',IU:'双端内向 · 非链特异性',ISF:'双端内向 · 正向链',ISR:'双端内向 · 反向链',OU:'双端外向 · 非链特异性',OSF:'双端外向 · 正向链',OSR:'双端外向 · 反向链',MU:'双端同向 · 非链特异性',MSF:'双端同向 · 正向链',MSR:'双端同向 · 反向链'} as Record<string,string>)[text]||text;
 if(key==='assay')return ({scRNA:'单细胞 RNA',snRNA:'单细胞核 RNA'} as Record<string,string>)[text]||text;
 if(text==='both')return ({seq_types:'蛋白与编码序列',tree_layout:'矩形图与圆形图',plot_mode:'多物种图与两两比较图',annotation_scope:'GO 与 KEGG',enrich_type:'GO 与 KEGG',motif_db_mode:'两个基序数据库',postbinning_input:'原始与优化分箱'} as Record<string,string>)[key]||'同时使用两种';
 if(text==='global'&&key==='semibin_environment')return '通用环境';
 if(text==='family'&&key==='kaiju_taxon_rank')return '科';
 if(text===''&&key==='module')return '自动选择分析模块';
 return optionNames[text]||text;
};
