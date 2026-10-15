"""Downstream interval/count/methylation analysis; fixed software, explicit reference files."""
import bisect, json, subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from community import bh,save_plot

def beds(path):
    if Path(path).is_dir():
        from upstream_links import peak_files
        files=peak_files(path)
        if not files:files=sorted(Path(path).glob('*.bed'))
    else:files=[Path(path)]
    if not files:raise ValueError('没有找到 BED/narrowPeak/broadPeak 区域文件')
    rows=[]
    for file in files:
        import gzip
        text=gzip.open(file,'rt').read() if file.suffix=='.gz' else file.read_text()
        for line in text.splitlines():
            if not line or line.startswith(('#','track','browser')):continue
            parts=line.split('\t')
            if len(parts)<3:raise ValueError('区域文件至少需要染色体、起点和终点')
            start,end=int(parts[1]),int(parts[2])
            if start<0 or end<=start:raise ValueError('BED 坐标需要 0-based、左闭右开且终点大于起点')
            rows.append((parts[0],start,end))
    return sorted(set(rows))
def execute(action,cfg,out):
    import matplotlib.pyplot as plt
    if action=='regions':
        rows=beds(cfg['peaks']);merged=[]
        for chrom,start,end in rows:
            if merged and merged[-1][0]==chrom and start<=merged[-1][2]+cfg['merge_distance']:merged[-1]=(chrom,merged[-1][1],max(end,merged[-1][2]))
            else:merged.append((chrom,start,end))
        pd.DataFrame([(c,s,e,'region_'+str(i+1),0,'.') for i,(c,s,e) in enumerate(merged)]).to_csv(out/'regions.bed',sep='\t',header=False,index=False)
        fig,ax=plt.subplots();ax.hist([e-s for c,s,e in merged],bins=30);ax.set(xlabel='Region width (bp)',ylabel='Region count');save_plot(fig,out,'region_widths.png')
    elif action=='count':
        from upstream_links import epigenetic_samples,record
        frame=epigenetic_samples(cfg.get('upstream_results',''),cfg['samples'],'bam',cfg.get('bam_pattern',''));required={'sample_id','bam'}
        if cfg.get('upstream_results'):record(out,cfg['upstream_results'],frame.to_dict('records'))
        if not required.issubset(frame) or frame.sample_id.duplicated().any():raise ValueError('样本表需要唯一 sample_id 与 bam 列')
        regions=beds(cfg['peaks']);saf=pd.DataFrame([('region_'+str(i+1),c,s+1,e,'.') for i,(c,s,e) in enumerate(regions)],columns=['GeneID','Chr','Start','End','Strand']);saf.to_csv(out/'regions.saf',sep='\t',index=False)
        cmd=['featureCounts','-F','SAF','-a',str(out/'regions.saf'),'-o',str(out/'featurecounts.tsv'),'-T',str(cfg.get('cpu',1)),'-s',str(cfg['strandedness'])]
        if cfg['paired_end']:cmd+=['-p','--countReadPairs']
        cmd+=frame.bam.tolist();subprocess.run(cmd,check=True)
        counts=pd.read_csv(out/'featurecounts.tsv',sep='\t',comment='#');counts=counts.set_index('Geneid').iloc[:,5:]
        if len(counts.columns)!=len(frame):raise ValueError('区域计数的样本数量不一致')
        counts.columns=frame.sample_id;counts.to_csv(out/'counts.tsv',sep='\t');frame.to_csv(out/'samples.csv',index=False)
    elif action=='sample_qc':
        counts=pd.read_csv(cfg['counts'],sep='\t',index_col=0).astype(float)
        if not np.isfinite(counts.to_numpy()).all() or (counts<0).any().any():raise ValueError('计数矩阵需要有限非负值')
        normalized=counts.div(counts.sum(axis=0).replace(0,np.nan),axis=1)*1e6
        if normalized.isna().any().any():raise ValueError('存在总计数为零的样本')
        x=np.log2(normalized+1).T;x=x-x.mean(axis=0);u,s,v=np.linalg.svd(x,full_matrices=False);coords=u[:,:2]*s[:2]
        if coords.shape[1]<2:raise ValueError('样本检查至少需要两个样本')
        pd.DataFrame(coords,index=counts.columns,columns=['PC1','PC2']).to_csv(out/'sample_pca.tsv',sep='\t');np.log2(normalized+1).corr().to_csv(out/'sample_correlation.tsv',sep='\t')
        fig,ax=plt.subplots();ax.scatter(coords[:,0],coords[:,1]);[ax.annotate(n,p) for n,p in zip(counts.columns,coords)];save_plot(fig,out,'sample_pca.png')
    elif action in ('differential','gene_differential'):
        count_path,sample_path=cfg['counts'],cfg['samples']
        if action=='gene_differential':
            samples=pd.read_csv(sample_path)
            if 'cell_type' not in samples:raise ValueError('供者汇总表缺少 cell_type 字段')
            samples=samples[samples.cell_type.astype(str)==str(cfg['cell_type'])]
            if samples.empty:raise ValueError('汇总表没有这个细胞群，请选择实际存在的群编号或类型')
            if not {cfg['control'],cfg['treated']}.issubset(set(samples.group.astype(str))):raise ValueError('选定细胞群缺少所选的对照组或处理组，请核对群名称和样本分组。')
            counts=pd.read_csv(count_path,sep='\t',index_col=0).loc[:,samples.sample_id]
            count_path=str(out/'selected_counts.tsv');sample_path=str(out/'selected_samples.csv');counts.to_csv(count_path,sep='\t');samples.to_csv(sample_path,index=False)
        subprocess.run(['Rscript',str(Path(__file__).with_name('differential.R')),count_path,sample_path,cfg['control'],cfg['treated'],str(cfg['padj']),str(cfg['log2fc']),str(out),'gene' if action=='gene_differential' else 'region','paired' if cfg.get('paired') else 'unpaired'],check=True)
    elif action=='annotate':
        genes={}
        import re
        for line in Path(cfg['gtf']).read_text().splitlines():
            if not line or line.startswith('#'):continue
            p=line.split('\t')
            if len(p)!=9 or p[2]!='gene':continue
            attrs=dict(re.findall(r'(\w+) "([^"]+)"',p[8]));gene=attrs.get('gene_id')
            if gene:genes.setdefault(p[0],[]).append((int(p[3])-1 if p[6]!='-' else int(p[4])-1,gene,p[6]))
        for chrom in genes:genes[chrom].sort()
        if not genes:raise ValueError('GTF 中没有可用的 gene 条目')
        rows=[]
        for i,(chrom,start,end) in enumerate(beds(cfg['peaks'])):
            values=genes.get(chrom,[]);mid=(start+end)//2
            if not values:rows.append(('region_'+str(i+1),chrom,start,end,'',np.nan,False));continue
            pos=bisect.bisect_left(values,(mid,''));candidates=values[max(0,pos-1):pos+1];tss,gene,strand=min(candidates,key=lambda v:abs(v[0]-mid));dist=(mid-tss)*(1 if strand!='-' else -1)
            rows.append(('region_'+str(i+1),chrom,start,end,gene,dist,-cfg['promoter_upstream']<=dist<=cfg['promoter_downstream']))
        frame=pd.DataFrame(rows,columns=['region','chrom','start','end','nearest_gene','distance_to_tss','promoter']);frame.to_csv(out/'region_annotations.tsv',sep='\t',index=False);(out/'genes.txt').write_text('\n'.join(sorted(set(frame.nearest_gene)-{''}))+'\n')
    elif action=='motifs':
        import shutil
        # bedtools may create an adjacent FASTA index. Keep that write in this task.
        reference=out/'reference.fa';shutil.copyfile(cfg['fasta'],reference)
        fasta=out/'regions.fa';subprocess.run(['bedtools','getfasta','-fi',str(reference),'-bed',cfg['peaks'],'-fo',str(fasta)],check=True)
        subprocess.run(['ame','--oc',str(out/'ame'),'--control',cfg['background_fasta'],'--method','fisher',str(fasta),cfg['motifs']],check=True)
        source=out/'ame/ame.tsv'
        if not source.is_file():raise ValueError('AME 没有生成基序富集结果')
        (out/'motif_enrichment.tsv').write_bytes(source.read_bytes())
    elif action=='coverage':
        bigwigs=cfg['bigwigs']
        if not bigwigs and cfg.get('upstream_results'):
            from upstream_links import coverage_files,record
            bigwigs=coverage_files(cfg['upstream_results'],cfg.get('samples',''));record(out,cfg['upstream_results'],bigwigs)
        if not bigwigs:raise ValueError('请提供覆盖度轨迹，或选择会生成 bigWig 的测序上游')
        subprocess.run(['computeMatrix','reference-point','--referencePoint',cfg.get('reference_point','center'),'-b',str(cfg['promoter_upstream']),'-a',str(cfg['promoter_downstream']),'-R',cfg['regions'],'-S',*bigwigs,'-p',str(cfg.get('cpu',1)),'-o',str(out/'coverage_matrix.gz')],check=True)
        subprocess.run(['plotHeatmap','-m',str(out/'coverage_matrix.gz'),'-out',str(out/'coverage_heatmap.png')],check=True)
        subprocess.run(['plotProfile','-m',str(out/'coverage_matrix.gz'),'-out',str(out/'coverage_profile.png')],check=True)
    elif action=='methylation_qc':
        from upstream_links import epigenetic_samples,record
        samples=epigenetic_samples(cfg.get('upstream_results',''),cfg['samples'],'coverage');samples.to_csv(out/'samples.csv',index=False);objects=[];summary=[]
        if cfg.get('upstream_results'):record(out,cfg['upstream_results'],samples.to_dict('records'))
        if not {'sample_id','path'}.issubset(samples):raise ValueError('甲基化样本表需要 sample_id、path 两列（Bismark coverage 文件）')
        for row in samples.to_dict('records'):
            frame=pd.read_csv(row['path'],sep='\t',header=None,names=['chrom','start','end','percent','methylated','unmethylated']);coverage=frame.methylated+frame.unmethylated
            frame=frame[coverage>=cfg['min_coverage']].copy();frame['site']=frame.chrom.astype(str)+':'+frame.start.astype(str);frame['beta']=frame.methylated/(frame.methylated+frame.unmethylated)
            if frame.site.duplicated().any():raise ValueError('甲基化位点重复')
            objects.append(frame.set_index('site').beta.rename(row['sample_id']));summary.append((row['sample_id'],len(frame),frame.beta.mean()))
        matrix=pd.concat(objects,axis=1,join='inner')
        if matrix.empty:raise ValueError('过滤后没有共同甲基化位点')
        matrix.to_csv(out/'methylation_beta.tsv',sep='\t');pd.DataFrame(summary,columns=['sample','sites','mean_methylation']).to_csv(out/'methylation_qc.tsv',sep='\t',index=False)
        fig,ax=plt.subplots();matrix.plot.box(ax=ax);ax.set_ylabel('Methylation fraction');save_plot(fig,out,'methylation_distribution.png')
    elif action=='dmr':
        subprocess.run(['Rscript',str(Path(__file__).with_name('dmr.R')),cfg['samples'],cfg['control'],cfg['treated'],str(cfg['delta']),str(cfg['pvalue_cutoff']),str(out)],check=True)
    elif action in ('methylation_samples','methylation_heatmap'):
        frame=pd.read_csv(cfg['input'],sep='\t',index_col=0).astype(float)
        if not np.isfinite(frame.to_numpy()).all() or ((frame<0)|(frame>1)).any().any():raise ValueError('甲基化比例需要在 0 到 1 之间')
        if action=='methylation_samples':
            x=frame.T-frame.mean(axis=1);u,s,v=np.linalg.svd(x,full_matrices=False);coords=u[:,:2]*s[:2]
            if coords.shape[1]<2:raise ValueError('样本检查至少需要两个样本')
            pd.DataFrame(coords,index=frame.columns,columns=['PC1','PC2']).to_csv(out/'sample_pca.tsv',sep='\t');frame.corr().to_csv(out/'sample_correlation.tsv',sep='\t')
            fig,ax=plt.subplots();ax.scatter(coords[:,0],coords[:,1]);[ax.annotate(n,p) for n,p in zip(frame.columns,coords)];save_plot(fig,out,'sample_pca.png')
        else:
            view=frame.loc[frame.var(axis=1).nlargest(cfg['top_taxa']).index];view.to_csv(out/'displayed_sites.tsv',sep='\t');fig,ax=plt.subplots(figsize=(9,7));image=ax.imshow(view,aspect='auto',vmin=0,vmax=1,cmap='viridis');ax.set_xticks(range(len(view.columns)),view.columns,rotation=90);fig.colorbar(image,ax=ax,label='Methylation fraction');save_plot(fig,out,'methylation_heatmap.png')
    else:raise ValueError('Unsupported epigenetic stage')
