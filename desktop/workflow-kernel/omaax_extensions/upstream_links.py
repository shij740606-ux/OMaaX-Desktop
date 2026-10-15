"""Translate pinned upstream result layouts into explicit downstream inputs."""
import csv, json, re
from pathlib import Path

def samples(sheet):
    with Path(sheet).open(encoding='utf-8-sig',newline='') as handle:
        rows=list(csv.DictReader(handle,delimiter='\t' if Path(sheet).suffix=='.tsv' else ','))
    if not rows:raise ValueError('样本表没有样本')
    result={}
    for row in rows:
        sid=str(row.get('sample_id') or row.get('sample') or '').strip()
        if not sid or not re.fullmatch(r'[\w.-]+',sid):raise ValueError('样本表需要有效的 sample_id 或 sample 列')
        meta={'sample_id':sid,**{k:row[k] for k in ('group','donor','batch') if row.get(k)}}
        if sid in result and result[sid]!=meta:raise ValueError('同一样本的分组信息不一致：'+sid)
        result[sid]=meta
    return list(result.values())

def files(root,patterns):
    root=Path(root).resolve();result=set()
    if not root.is_dir():raise ValueError('上游结果目录不存在')
    for pattern in patterns:
        for file in root.glob(pattern):
            resolved=file.resolve()
            if root not in resolved.parents:raise ValueError('上游结果链接指向本次结果目录之外')
            if resolved.is_file() and resolved.stat().st_size:result.add(resolved)
    return sorted(result)

def match_sample(file,ids):
    # Prefer the longest ID so S1 cannot capture S1_2's files.
    for sid in sorted(ids,key=len,reverse=True):
        if any(part==sid or part.startswith((sid+'_',sid+'.')) for part in file.parts):return sid
    return None

def unique_by_sample(paths,rows,root,aliases=None):
    ids=[row['sample_id'] for row in rows];found={sid:[] for sid in ids}
    aliases=aliases or {}
    for file in paths:
        sid=match_sample(file.relative_to(Path(root).resolve()),[*ids,*aliases]);sid=aliases.get(sid,sid)
        if sid:found[sid].append(file)
    for sid,choices in found.items():
        if len(choices)!=1:raise ValueError('上游样本 '+sid+' 找到 '+str(len(choices))+' 份候选结果；请在对话中明确选择结果文件。')
    return {sid:choices[0] for sid,choices in found.items()}

def replicate_aliases(rows):
    ids={row['sample_id'] for row in rows};aliases={}
    for sid in ids:
        match=re.fullmatch(r'(.+)_(R|REP)(\d+)',sid)
        if not match:continue
        alias=match[1]+('_REP' if match[2]=='R' else '_R')+match[3]
        if alias in ids:raise ValueError('样本编号在上游重复命名规则下存在歧义：'+sid+' / '+alias)
        aliases[alias]=sid
    return aliases

def cell_samples(root,sheet,spatial):
    rows=samples(sheet)
    patterns=['*.explorer/adata.h5ad'] if spatial else ['**/mtx_conversions/**/*_filtered_matrix.h5ad']
    paths=files(root,patterns)
    if not paths and not spatial:
        paths=files(root,['**/filtered_feature_bc_matrix.h5'])
    if not paths:raise ValueError('上游未输出可衔接的'+('空间 H5AD（需启用 Explorer 导出）' if spatial else '过滤后表达矩阵')+'；请提供已有矩阵路径。')
    found=unique_by_sample(paths,rows,root)
    return [{**row,'path':str(found[row['sample_id']])} for row in rows]

def bracken(root,sheet,database='',rank='S'):
    import pandas as pd
    rows=samples(sheet);paths=files(root,['bracken/**/*.bracken.tsv','bracken/*.bracken.tsv'])
    if database:paths=[p for p in paths if p.name.endswith('_'+database+'.bracken.tsv')]
    if not paths:raise ValueError('上游未输出 Bracken 丰度表；请启用该输出，或提供已有丰度表。')
    found=unique_by_sample(paths,rows,root);tables=[];names={}
    for sid,file in found.items():
        frame=pd.read_csv(file,sep='\t',dtype={'taxonomy_id':str})
        required={'taxonomy_id','taxonomy_lvl','name','new_est_reads'}
        if not required.issubset(frame) or frame.taxonomy_id.duplicated().any():raise ValueError('上游 Bracken 表格式无效')
        if set(frame.taxonomy_lvl.astype(str))!={rank}:raise ValueError('Bracken 分类层级不一致，请核对本次选择的分类层级')
        for taxid,name in zip(frame.taxonomy_id,frame['name']):
            if taxid in names and names[taxid]!=name:raise ValueError('上游物种编号与名称不一致')
            names[taxid]=name
        tables.append(frame.set_index('taxonomy_id').new_est_reads.rename(sid))
    return pd.concat(tables,axis=1).fillna(0),pd.DataFrame(rows),names

def mag_quality(root):
    paths=files(root,['**/checkm2/**/quality_report.tsv','**/checkm2/quality_report.tsv'])
    if len(paths)!=1:raise ValueError('需要唯一的 CheckM2 quality_report.tsv；请在对话中指定 MAG 质量表。')
    return str(paths[0])

def epigenetic_samples(root,sheet,kind,pattern=''):
    """Keep supplied groups; use nf-core's declared sample/replicate IDs."""
    import pandas as pd
    source=pd.read_csv(sheet,sep='\t' if Path(sheet).suffix=='.tsv' else ',').fillna('');rows=[]
    for row in source.to_dict('records'):
        sid=str(row.get('sample_id') or row.get('sample') or row.get('group') or '')
        if not row.get('sample_id') and str(row.get('replicate','')):sid+='_R'+str(row['replicate']).removesuffix('.0')
        if not sid:raise ValueError('样本表缺少样本名称')
        item={**row,'sample_id':sid}
        if not item.get('group') and row.get('sample'):item['group']=str(row['sample'])
        rows.append(item)
    if len({r['sample_id'] for r in rows})!=len(rows):raise ValueError('下游样本表的 sample_id 必须唯一')
    key='bam' if kind=='bam' else 'path'
    if rows and all(row.get(key) for row in rows):return pd.DataFrame(rows)
    if not root:raise ValueError('请提供已有结果文件，或选择对应测序上游')
    patterns=([pattern] if pattern else ['**/merged_library/*.mLb.clN.sorted.bam']) if kind=='bam' else ['**/*.bismark.cov.gz','**/*.bismark.cov']
    paths=files(root,patterns)
    if kind=='bam' and not paths and not pattern:paths=files(root,['**/*.bam'])
    found=unique_by_sample(paths,rows,root,replicate_aliases(rows))
    return pd.DataFrame([{**row,key:row.get(key) or str(found[row['sample_id']])} for row in rows])

def peak_files(root):
    return files(root,['**/*.narrowPeak','**/*.broadPeak','**/*.narrowPeak.gz','**/*.broadPeak.gz'])

def coverage_files(root,sheet=''):
    paths=files(root,['**/merged_library/bigwig/*.bigWig'])
    if not paths:paths=files(root,['03_peak_calling/03_bed_to_bigwig/*.bigWig'])
    if not paths:paths=files(root,['**/*.bigWig','**/*.bw'])
    if sheet:
        rows=samples(sheet);found=unique_by_sample(paths,rows,root,replicate_aliases(rows))
        return [str(found[row['sample_id']]) for row in rows]
    if not paths:raise ValueError('上游没有生成覆盖度轨迹，请提供 bigWig 文件')
    return [str(p) for p in paths]

def record(out,root,paths):
    (Path(out)/'upstream-inputs.json').write_text(json.dumps({'upstreamResult':str(root),'inputs':paths},ensure_ascii=False,indent=2),encoding='utf-8')
