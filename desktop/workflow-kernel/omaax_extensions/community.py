"""Explicit abundance-table analyses, plots and independently saved results."""
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

def bh(p):
    p=np.asarray(p,float);order=np.argsort(p);answer=np.empty(len(p));answer[order]=np.minimum.accumulate((p[order]*len(p)/np.arange(1,len(p)+1))[::-1])[::-1].clip(0,1);return answer
def matrix(path):
    frame=pd.read_csv(path,sep='\t',index_col=0)
    if frame.empty or not frame.index.is_unique or not frame.columns.is_unique:raise ValueError('丰度表为空或存在重复编号')
    x=frame.to_numpy(dtype=float)
    if not np.isfinite(x).all() or (x<0).any() or (x.sum(axis=0)<=0).any():raise ValueError('丰度需要有限非负值，每个样本的总量必须大于零')
    return frame
def save_plot(fig,out,name):
    import matplotlib.pyplot as plt
    fig.savefig(out/name,dpi=160,bbox_inches='tight');plt.close(fig)
def execute(action,cfg,out):
    import matplotlib.pyplot as plt
    from scipy.spatial.distance import pdist,squareform
    if action=='enrichment':
        foreground=set(Path(cfg['selected']).read_text().split());universe=set(Path(cfg['universe']).read_text().split());foreground &=universe
        if not foreground or not universe:raise ValueError('候选与背景基因没有交集')
        rows=[]
        for line in Path(cfg['gmt']).read_text().splitlines():
            parts=line.split('\t')
            if len(parts)<3:continue
            genes=set(parts[2:])&universe;overlap=genes&foreground
            if len(genes)<cfg['min_size']:continue
            p=stats.hypergeom.sf(len(overlap)-1,len(universe),len(genes),len(foreground));rows.append((parts[0],len(genes),len(overlap),p,','.join(sorted(overlap))))
        frame=pd.DataFrame(rows,columns=['gene_set','background_genes','overlap','pvalue','genes']);frame['padj']=bh(frame.pvalue) if len(frame) else [];frame.to_csv(out/'enrichment.tsv',sep='\t',index=False)
        fig,ax=plt.subplots(figsize=(9,6));view=frame.nsmallest(20,'padj');ax.barh(view.gene_set,-np.log10(view.padj.clip(lower=1e-300)));ax.set_xlabel('-log10 adjusted p');save_plot(fig,out,'enrichment.png');return
    if action=='mag_summary':
        if Path(cfg['quality_table']).is_dir():
            from upstream_links import mag_quality,record
            root=cfg['quality_table'];cfg['quality_table']=mag_quality(root);record(out,root,[cfg['quality_table']])
        frame=pd.read_csv(cfg['quality_table'],sep='\t')
        names={'completeness','contamination'}
        lower={str(c).lower():c for c in frame.columns}
        if not names.issubset(lower):raise ValueError('MAG 质量表需要 completeness、contamination 列')
        frame['selected']=(frame[lower['completeness']]>=cfg['min_completeness'])&(frame[lower['contamination']]<=cfg['max_contamination'])
        frame.to_csv(out/'mag_quality.tsv',sep='\t',index=False)
        fig,ax=plt.subplots();ax.scatter(frame[lower['completeness']],frame[lower['contamination']],c=frame.selected.astype(int));ax.set(xlabel='Completeness (%)',ylabel='Contamination (%)');save_plot(fig,out,'mag_quality.png');return
    linked=None
    if action=='prepare' and Path(cfg['abundance']).is_dir():
        root=Path(cfg['abundance'])
        if (root/'abundance.tsv').is_file():cfg['abundance']=str(root/'abundance.tsv')
        else:cfg['upstream_results']=str(root)
    if action=='prepare' and cfg.get('upstream_results'):
        from upstream_links import bracken,record
        linked,meta,names=bracken(cfg['upstream_results'],cfg['samples'],cfg['database'],cfg['taxonomic_rank'])
        linked.to_csv(out/'linked_abundance.tsv',sep='\t');cfg['abundance']=str(out/'linked_abundance.tsv')
        pd.Series(names,name='name').to_csv(out/'taxon_names.tsv',sep='\t',index_label='taxon_id')
        record(out,cfg['upstream_results'],meta.to_dict('records'))
    frame=matrix(cfg['abundance']);relative=frame.div(frame.sum(axis=0),axis=1)
    if action=='prepare':
        if linked is None:meta=pd.read_csv(cfg['samples'],sep='\t' if Path(cfg['samples']).suffix=='.tsv' else ',')
        if 'sample_id' not in meta or meta.sample_id.duplicated().any():raise ValueError('样本表需要唯一 sample_id 列')
        if set(meta.sample_id.astype(str))!=set(frame.columns):raise ValueError('样本表与丰度表的样本名称不一致')
        meta=meta.set_index('sample_id').loc[frame.columns].reset_index();meta.to_csv(out/'samples.csv',index=False);frame.to_csv(out/'abundance.tsv',sep='\t');relative.to_csv(out/'relative_abundance.tsv',sep='\t')
    elif action=='composition':
        top=relative.mean(axis=1).nlargest(cfg['top_taxa']).index
        view=relative.loc[top].copy();view.loc['Other']=relative.drop(top).sum(axis=0);view.to_csv(out/'composition.tsv',sep='\t')
        ax=view.T.plot.bar(stacked=True,figsize=(10,6));ax.set_ylabel('Relative abundance');ax.legend(bbox_to_anchor=(1,1));save_plot(ax.figure,out,'composition.png')
    elif action=='alpha':
        x=relative.to_numpy();logs=np.zeros_like(x);np.log(x,out=logs,where=x>0)
        result=pd.DataFrame({'observed_taxa':(frame>0).sum(),'shannon':-(x*logs).sum(axis=0),'simpson':1-(x*x).sum(axis=0)},index=frame.columns)
        result.to_csv(out/'alpha_diversity.tsv',sep='\t');fig,axes=plt.subplots(1,3,figsize=(12,4))
        for ax,key in zip(axes,result):ax.bar(result.index,result[key]);ax.set_title(key);ax.tick_params(axis='x',rotation=90)
        save_plot(fig,out,'alpha_diversity.png')
    elif action=='beta':
        d=squareform(pdist(relative.T,metric='braycurtis'));n=len(d);center=np.eye(n)-np.ones((n,n))/n;b=-.5*center@(d*d)@center;val,vec=np.linalg.eigh(b);idx=np.argsort(val)[::-1];val=val[idx];vec=vec[:,idx]
        coords=vec[:,:2]*np.sqrt(np.maximum(val[:2],0));pd.DataFrame(d,index=frame.columns,columns=frame.columns).to_csv(out/'bray_curtis.tsv',sep='\t');pd.DataFrame(coords,index=frame.columns,columns=['PCoA1','PCoA2'][:coords.shape[1]]).to_csv(out/'pcoa.tsv',sep='\t')
        pd.DataFrame({'eigenvalue':val}).to_csv(out/'pcoa_eigenvalues.tsv',sep='\t',index=False)
        if coords.shape[1]<2:raise ValueError('PCoA 绘图需要至少两个样本')
        fig,ax=plt.subplots();ax.scatter(coords[:,0],coords[:,1]);[ax.annotate(name,point) for name,point in zip(frame.columns,coords)];ax.set(xlabel='PCoA1',ylabel='PCoA2');save_plot(fig,out,'pcoa.png')
    elif action=='heatmap':
        view=relative.loc[relative.var(axis=1).nlargest(cfg['top_taxa']).index];z=np.log10(view+cfg['pseudocount']);fig,ax=plt.subplots(figsize=(10,8));image=ax.imshow(z,aspect='auto',cmap='viridis');ax.set_xticks(range(len(view.columns)),view.columns,rotation=90);ax.set_yticks(range(len(view.index)),view.index);fig.colorbar(image,ax=ax,label='log10 relative abundance');save_plot(fig,out,'abundance_heatmap.png');view.to_csv(out/'heatmap_abundance.tsv',sep='\t')
    elif action=='network':
        chosen=relative.loc[relative.mean(axis=1).nlargest(cfg['top_taxa']).index];rows=[]
        for i in range(len(chosen)):
            for j in range(i+1,len(chosen)):
                r,p=stats.spearmanr(chosen.iloc[i],chosen.iloc[j])
                if np.isfinite(r):rows.append((chosen.index[i],chosen.index[j],r,p))
        result=pd.DataFrame(rows,columns=['source','target','rho','pvalue']);result['padj']=bh(result.pvalue) if len(result) else [];result=result[(result.rho.abs()>=cfg['correlation_cutoff'])&(result.padj<=cfg['padj'])];result.to_csv(out/'correlation_network.tsv',sep='\t',index=False)
        fig,ax=plt.subplots(figsize=(10,10));ax.axis('off');nodes=sorted(set(result.source)|set(result.target));angles=np.linspace(0,2*np.pi,len(nodes),endpoint=False);positions={name:(np.cos(angle),np.sin(angle)) for name,angle in zip(nodes,angles)}
        for row in result.itertuples():
            x,y=positions[row.source];a,b=positions[row.target];ax.plot([x,a],[y,b],color='#7562d6' if row.rho>0 else '#da6d85',alpha=.5,linewidth=.5+2*abs(row.rho))
        for name,(x,y) in positions.items():ax.scatter([x],[y],s=70,color='#7562d6');ax.text(x*1.08,y*1.08,str(name),fontsize=8,ha='center',va='center')
        ax.set_title('Spearman associations (not causal interactions)');ax.set_xlim(-1.3,1.3);ax.set_ylim(-1.3,1.3)
        if not nodes:ax.text(0,0,'No associations meet the selected thresholds',ha='center')
        save_plot(fig,out,'network_summary.png')
    elif action=='functional':
        # HUMAnN/other functional tools supply this table; no substitute taxon-to-function inference.
        view=frame.loc[frame.mean(axis=1).nlargest(cfg['top_taxa']).index];view.to_csv(out/'functional_abundance.tsv',sep='\t');ax=view.T.plot.bar(stacked=True,figsize=(10,6));ax.set_ylabel('Supplied functional abundance');save_plot(ax.figure,out,'functional_profile.png')
    else:raise ValueError('Unsupported community stage')
