"""Scanpy/Squidpy analyses with persisted state after every selected operation."""
import json, re
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import sparse

def table(path):return pd.read_csv(path,sep='\t' if Path(path).suffix=='.tsv' else ',')
def require(data,*keys):
    for location,key in keys:
        if key not in getattr(data,location):raise ValueError('缺少 '+key+'，请先选择对应前置步骤或提供已处理的数据。')
def counts(data):
    if 'counts' not in data.layers:raise ValueError('缺少原始 counts 计数层。请在导入步骤指定计数来源。')
    return data.layers['counts']
def labels(data,cfg):
    name=cfg.get('groupby','cluster')
    if name not in data.obs:raise ValueError('细胞分组字段不存在：'+name)
    data.obs[name]=data.obs[name].astype('category');return name
def plot(fig,out,name):
    import matplotlib.pyplot as plt
    fig.savefig(out/name,dpi=160,bbox_inches='tight');plt.close(fig)
def write(data,out):
    # Pandas 3 infers nullable string indexes that older H5AD readers cannot read.
    for frame in [data.obs,data.var,*([data.raw.var] if data.raw is not None else [])]:
        frame.index=pd.Index(frame.index.to_numpy(dtype=object),dtype=object,name=frame.index.name)
        for key in frame:
            if isinstance(frame[key].dtype,pd.StringDtype):frame[key]=frame[key].astype(object)
    data.write_h5ad(out/'processed.h5ad',compression='gzip')

def prepare(cfg,out):
    import anndata as ad
    import scanpy as sc
    upstream=cfg.get('upstream_results','')
    if upstream:
        from upstream_links import cell_samples,record
        frame=pd.DataFrame(cell_samples(upstream,cfg['samples'],cfg.get('spatial',False)))
        frame.to_csv(out/'linked_samples.csv',index=False)
        record(out,upstream,frame.to_dict('records'))
    else:frame=table(cfg['samples'])
    if not {'sample_id','path'}.issubset(frame):raise ValueError('样本表需要 sample_id、path 两列；group、donor、batch 可由用户补充。')
    if frame.sample_id.duplicated().any():raise ValueError('样本名称不能重复')
    objects={}
    for row in frame.to_dict('records'):
        path=Path(row['path'])
        if not path.is_absolute():path=Path(cfg['samples']).parent/path
        if path.is_dir():a=sc.read_10x_mtx(path,var_names='gene_ids')
        elif path.suffix=='.h5ad':
            a=sc.read_h5ad(path)
            layer=cfg.get('counts_layer','counts')
            if upstream and layer=='counts' and layer not in a.layers:layer='count' if 'count' in a.layers else 'X'
            if layer!='X':
                if layer not in a.layers:raise ValueError('数据没有指定的计数层：'+layer)
                a.X=a.layers[layer].copy()
        elif path.suffix=='.h5':a=sc.read_10x_h5(path)
        else:raise ValueError('支持 10x MTX、10x H5 或 H5AD 表达矩阵')
        x=a.X.data if sparse.issparse(a.X) else np.asarray(a.X)
        if not np.isfinite(x).all() or (x<0).any() or not np.allclose(x,np.round(x)):raise ValueError('导入需要有限、非负的原始计数')
        if not a.obs_names.is_unique or not a.var_names.is_unique:raise ValueError('细胞或基因编号重复，请提供唯一编号')
        a.layers['counts']=a.X.copy()
        if cfg.get('spatial',False):
            if 'spatial' not in a.obsm or np.shape(a.obsm['spatial'])!=(a.n_obs,2) or not np.isfinite(a.obsm['spatial']).all():raise ValueError('空间输入需要每个位置的二维 spatial 坐标')
        for key in ('donor','group','batch'):a.obs[key]=str(row.get(key,row['sample_id'] if key!='group' else '未分组'))
        objects[str(row['sample_id'])]=a
    data=ad.concat(objects,label='sample_id',index_unique='::',join='inner',merge='same')
    if data.n_obs<3 or data.n_vars<3:raise ValueError('共同表达矩阵过小，无法执行后续分析')
    write(data,out);data.obs.to_csv(out/'cells.csv')

def execute(action,cfg,out):
    import scanpy as sc
    import matplotlib.pyplot as plt
    if action=='prepare':return prepare(cfg,out)
    data=sc.read_h5ad(cfg['input']);seed=int(cfg.get('seed',42));sc.settings.n_jobs=int(cfg.get('cpu',1))
    if action=='qc':
        data.X=counts(data).copy();pattern=cfg.get('mitochondrial_pattern','')
        if pattern:
            symbols=data.var.get('gene_symbols',pd.Series(data.var_names,index=data.var_names))
            data.var['mt']=[bool(re.search(pattern,str(v))) for v in symbols]
            if not data.var.mt.any():raise ValueError('线粒体规则未匹配任何基因，请更改规则或取消线粒体过滤')
        sc.pp.calculate_qc_metrics(data,qc_vars=['mt'] if pattern else [],percent_top=None,inplace=True)
        data.obs.to_csv(out/'qc_metrics.csv')
        keep=(data.obs.n_genes_by_counts>=cfg['min_genes'])&(data.obs.total_counts>=cfg['min_counts'])
        if cfg.get('max_genes',0)>0:keep &= data.obs.n_genes_by_counts<=cfg['max_genes']
        if cfg.get('max_pct_mt',0)>0:
            if not pattern:raise ValueError('线粒体比例过滤需要先填写基因名称规则')
            keep &=data.obs.pct_counts_mt<=cfg['max_pct_mt']
        data=data[keep].copy();sc.pp.filter_genes(data,min_cells=cfg['min_cells_per_gene'])
        if data.n_obs<3 or data.n_vars<3:raise ValueError('过滤后细胞或基因不足，请调整过滤阈值')
    elif action=='doublets':
        data.X=counts(data).copy()
        sc.pp.scrublet(data,batch_key='sample_id',expected_doublet_rate=cfg['expected_doublet_rate'],random_state=seed)
        data.obs[['doublet_score','predicted_doublet']].to_csv(out/'doublets.csv')
        if cfg['remove_doublets']:data=data[~data.obs.predicted_doublet].copy()
    elif action=='normalize':
        data.X=counts(data).copy();sc.pp.normalize_total(data,target_sum=cfg['target_sum']);sc.pp.log1p(data)
        sc.pp.highly_variable_genes(data,n_top_genes=min(cfg['n_hvg'],data.n_vars),flavor='seurat')
        data.var.to_csv(out/'variable_genes.csv');data.raw=data.copy()
    elif action=='pca':
        require(data,('var','highly_variable'))
        n=min(cfg['n_pcs'],data.n_obs-1,int(data.var.highly_variable.sum())-1)
        if n<2:raise ValueError('高变基因或细胞太少，无法计算主成分')
        sc.pp.pca(data,n_comps=n,mask_var='highly_variable',random_state=seed)
        pd.DataFrame(data.obsm['X_pca'],index=data.obs_names).to_csv(out/'pca.csv')
    elif action=='harmony':
        import harmonypy as hm
        import inspect
        require(data,('obsm','X_pca'))
        if cfg['batch_key'] not in data.obs:raise ValueError('批次列不存在')
        options={'random_state':seed}
        if 'ncores' in inspect.signature(hm.run_harmony).parameters:options['ncores']=int(cfg.get('cpu',1))
        result=hm.run_harmony(data.obsm['X_pca'],data.obs,[cfg['batch_key']],**options)
        corrected=np.asarray(result.Z_corr)
        # harmonypy 0.0.10 returns PCs x cells; newer releases return cells x PCs.
        if corrected.shape==data.obsm['X_pca'].T.shape:corrected=corrected.T
        if corrected.shape!=data.obsm['X_pca'].shape or not np.isfinite(corrected).all():raise ValueError('Harmony 返回的矩阵维度或数值无效')
        data.obsm['X_pca_harmony']=corrected
        pd.DataFrame(data.obsm['X_pca_harmony'],index=data.obs_names).to_csv(out/'integrated_pca.csv')
    elif action=='embedding':
        basis='X_pca_harmony' if 'X_pca_harmony' in data.obsm else 'X_pca';require(data,('obsm',basis))
        sc.pp.neighbors(data,n_neighbors=min(cfg['n_neighbors'],data.n_obs-1),use_rep=basis,random_state=seed)
        sc.tl.umap(data,min_dist=cfg['min_dist'],random_state=seed)
        pd.DataFrame(data.obsm['X_umap'],index=data.obs_names,columns=['UMAP1','UMAP2']).to_csv(out/'umap.csv')
    elif action=='cluster':
        require(data,('obsp','connectivities'))
        sc.tl.leiden(data,resolution=cfg['resolution'],key_added='cluster',flavor='igraph',n_iterations=2,random_state=seed)
        data.obs.to_csv(out/'clusters.csv')
    elif action=='markers':
        group=labels(data,cfg)
        if data.obs[group].nunique()<2:raise ValueError('标记基因比较至少需要两个细胞群；可调整聚类分辨率')
        sc.tl.rank_genes_groups(data,group,method='wilcoxon',use_raw=True,key_added='markers')
        sc.get.rank_genes_groups_df(data,group=None,key='markers',pval_cutoff=cfg['padj'],log2fc_min=cfg['log2fc']).to_csv(out/'markers.csv',index=False)
    elif action=='annotate':
        group=labels(data,cfg);mapping=table(cfg['annotations'])
        if not {'cluster','cell_type'}.issubset(mapping) or mapping.cluster.astype(str).duplicated().any():raise ValueError('注释表需要唯一 cluster 与 cell_type 列')
        lookup=dict(zip(mapping.cluster.astype(str),mapping.cell_type.astype(str)))
        data.obs['cell_type']=data.obs[group].astype(str).map(lookup).fillna('未注释').astype('category')
        data.obs.to_csv(out/'cell_annotations.csv')
    elif action=='composition':
        group=labels(data,cfg);frame=pd.crosstab(data.obs['sample_id'],data.obs[group]);frame.to_csv(out/'cell_counts.csv')
        fractions=frame.div(frame.sum(axis=1),axis=0);fractions.to_csv(out/'cell_fractions.csv')
        ax=fractions.plot.bar(stacked=True,figsize=(9,5));ax.set_ylabel('Cell fraction');plot(ax.figure,out,'composition.png')
    elif action=='pseudobulk':
        group=labels(data,cfg)
        if 'donor' not in data.obs or 'group' not in data.obs:raise ValueError('供者汇总需要 donor 与 group 字段')
        matrix=counts(data);columns=[];samples=[];vectors=[]
        for keys,indices in data.obs.groupby(['donor','group',group],observed=True).indices.items():
            if len(indices)<cfg['min_cells']:continue
            name='bulk_'+str(len(columns)+1);columns.append(name);vectors.append(np.asarray(matrix[indices].sum(axis=0)).ravel());samples.append((name,*map(str,keys),len(indices)))
        if not columns:raise ValueError('没有细胞群达到最低细胞数')
        pd.DataFrame(np.stack(vectors,axis=1),index=data.var_names,columns=columns).to_csv(out/'counts.tsv',sep='\t')
        pd.DataFrame(samples,columns=['sample_id','donor','group','cell_type','cells']).to_csv(out/'samples.csv',index=False)
    elif action=='communication':
        from scipy import stats
        from community import bh
        group=labels(data,cfg);pairs=table(cfg['ligand_receptor'])
        if not {'ligand','receptor'}.issubset(pairs):raise ValueError('配体受体表需要 ligand、receptor 列，请提供适用于该物种的表')
        pairs=pairs[pairs.ligand.isin(data.var_names)&pairs.receptor.isin(data.var_names)].drop_duplicates()
        if pairs.empty:raise ValueError('配体受体基因未匹配表达矩阵')
        genes=sorted(set(pairs.ligand)|set(pairs.receptor));matrix=data[:,genes].X
        x=matrix.toarray() if sparse.issparse(matrix) else np.asarray(matrix);categories=list(data.obs[group].cat.categories);names=data.obs[group].astype(str).to_numpy();rng=np.random.default_rng(seed)
        means=np.stack([x[names==str(cat)].mean(axis=0) for cat in categories]);lookup={gene:i for i,gene in enumerate(genes)};rows=[]
        for pair in pairs.itertuples():
            li,ri=lookup[pair.ligand],lookup[pair.receptor];score=means[:,li,None]*means[None,:,ri];exceed=np.zeros_like(score)
            for _ in range(cfg['n_permutations']):
                shuffled=rng.permutation(names);permuted=np.stack([x[shuffled==str(cat)].mean(axis=0) for cat in categories]);exceed+=permuted[:,li,None]*permuted[None,:,ri]>=score
            for i,source in enumerate(categories):
                for j,target in enumerate(categories):rows.append((source,target,pair.ligand,pair.receptor,score[i,j],(exceed[i,j]+1)/(cfg['n_permutations']+1)))
        frame=pd.DataFrame(rows,columns=['source','target','ligand','receptor','mean_product_score','pvalue']);frame['padj']=bh(frame.pvalue);frame.to_csv(out/'ligand_receptor_scores.tsv',sep='\t',index=False)
        # This is an explicit mean-product permutation analysis, not a causal claim or LIANA substitute.
        selected=frame[frame.padj<=cfg['padj']];summary=pd.crosstab(selected.source,selected.target).reindex(index=categories,columns=categories,fill_value=0)
        fig,ax=plt.subplots();image=ax.imshow(summary,cmap='Purples');ax.set_xticks(range(len(categories)),categories,rotation=90);ax.set_yticks(range(len(categories)),categories);fig.colorbar(image,ax=ax,label='Significant supplied LR pairs');plot(fig,out,'communication.png')
    elif action=='gene_sets':
        scores=[]
        for line in Path(cfg['gmt']).read_text().splitlines():
            parts=line.split('\t')
            if len(parts)<3:continue
            genes=sorted(set(parts[2:])&set(data.var_names))
            if len(genes)<cfg['min_size']:continue
            name='score_'+parts[0];sc.tl.score_genes(data,genes,score_name=name,random_state=seed,use_raw=False);scores.append(name)
        if not scores:raise ValueError('基因集没有足够的匹配基因，请检查编号和最小基因数')
        data.obs[scores].to_csv(out/'gene_set_scores.csv')
    elif action=='trajectory':
        require(data,('obsp','connectivities'));group=labels(data,cfg)
        matches=np.flatnonzero(data.obs[group].astype(str).to_numpy()==str(cfg['root_group']))
        if not len(matches):raise ValueError('起始细胞群不存在，请填写数据中实际的群名称')
        data.uns['iroot']=int(matches[0]);sc.tl.paga(data,groups=group);sc.tl.diffmap(data,n_comps=min(15,data.n_obs-1),random_state=seed)
        sc.tl.dpt(data,n_dcs=min(10,data.obsm['X_diffmap'].shape[1]))
        data.obs[['dpt_pseudotime',group]].to_csv(out/'pseudotime.csv')
        pd.DataFrame(data.uns['paga']['connectivities'].toarray(),index=data.obs[group].cat.categories,columns=data.obs[group].cat.categories).to_csv(out/'trajectory_graph.csv')
    elif action=='spatial_graph':
        import squidpy as sq
        require(data,('obsm','spatial'));parts=[]
        for sample in data.obs.sample_id.unique():
            a=data[data.obs.sample_id==sample].copy();kwargs={'coord_type':'grid' if cfg['spatial_mode']=='grid' else 'generic','n_neighs':min(cfg['n_neighbors'],a.n_obs-1)}
            if cfg['spatial_mode']=='radius':kwargs['radius']=cfg['radius']
            sq.gr.spatial_neighbors(a,**kwargs);parts.append(a)
        # Restore original cell ordering; block-diagonal graphs never connect different sections.
        import anndata as ad
        ordered=ad.concat(parts,pairwise=True,merge='same',uns_merge='first')
        ordered=ordered[data.obs_names].copy();data.obsp['spatial_connectivities']=ordered.obsp['spatial_connectivities'];data.obsp['spatial_distances']=ordered.obsp['spatial_distances']
        data.uns['spatial_neighbors']=ordered.uns['spatial_neighbors']
        pd.DataFrame({'sample':data.obs.sample_id,'neighbor_count':np.asarray(data.obsp['spatial_connectivities'].getnnz(axis=1)).ravel()},index=data.obs_names).to_csv(out/'spatial_neighbors.csv')
    elif action=='spatial_domains':
        require(data,('obsp','spatial_connectivities'),('obsp','connectivities'))
        graph=cfg['spatial_weight']*data.obsp['spatial_connectivities']+(1-cfg['spatial_weight'])*data.obsp['connectivities']
        sc.tl.leiden(data,adjacency=graph,resolution=cfg['resolution'],key_added='spatial_domain',flavor='igraph',n_iterations=2,random_state=seed)
        data.obs.to_csv(out/'spatial_domains.csv')
    elif action=='spatial_genes':
        import squidpy as sq
        require(data,('obsp','spatial_connectivities'));frames=[]
        genes=data.var_names[data.var.get('highly_variable',pd.Series(True,index=data.var_names))].tolist()
        for sample in data.obs.sample_id.unique():
            a=data[data.obs.sample_id==sample].copy();sq.gr.spatial_autocorr(a,mode='moran',genes=genes,n_perms=cfg['n_permutations'],n_jobs=int(cfg.get('cpu',1)),seed=seed,use_raw=False)
            frame=a.uns['moranI'].copy();frame.insert(0,'sample',sample);frames.append(frame)
        pd.concat(frames).to_csv(out/'spatial_variable_genes.csv')
    elif action=='neighborhood':
        import squidpy as sq
        require(data,('obsp','spatial_connectivities'));group=labels(data,cfg);frames=[]
        for sample in data.obs.sample_id.unique():
            a=data[data.obs.sample_id==sample].copy();a.obs[group]=a.obs[group].cat.remove_unused_categories()
            if a.obs[group].nunique()<2:raise ValueError('邻域分析需要至少两个细胞群')
            sq.gr.nhood_enrichment(a,cluster_key=group,n_perms=cfg['n_permutations'],seed=seed,n_jobs=int(cfg.get('cpu',1)))
            z=a.uns[group+'_nhood_enrichment']['zscore'];names=a.obs[group].cat.categories
            frames.append(pd.DataFrame([(sample,str(names[i]),str(names[j]),float(z[i,j])) for i in range(len(names)) for j in range(len(names))],columns=['sample','source','target','zscore']))
        pd.concat(frames).to_csv(out/'neighborhood_enrichment.csv',index=False)
    elif action=='plots':
        group=labels(data,cfg);require(data,('obsm','X_umap'))
        sc.pl.umap(data,color=group,show=False);plot(plt.gcf(),out,'umap.png')
        if 'spatial' in data.obsm:
            fig,axes=plt.subplots(1,data.obs.sample_id.nunique(),figsize=(6*data.obs.sample_id.nunique(),5),squeeze=False)
            codes=data.obs[group].cat.codes
            for ax,sample in zip(axes[0],data.obs.sample_id.unique()):
                mask=(data.obs.sample_id==sample).to_numpy();xy=data.obsm['spatial'][mask];ax.scatter(xy[:,0],xy[:,1],c=codes[mask],s=cfg['point_size'],cmap='tab20');ax.set_title(str(sample));ax.set_aspect('equal');ax.invert_yaxis()
            plot(fig,out,'spatial_map.png')
        if 'markers' in data.uns:
            # Keep cluster order; automatic dendrograms fail on small sparse gene panels.
            sc.pl.rank_genes_groups_dotplot(data,n_genes=cfg['top_genes'],key='markers',dendrogram=False,show=False);plot(plt.gcf(),out,'marker_dotplot.png')
        if 'dpt_pseudotime' in data.obs:
            sc.pl.umap(data,color='dpt_pseudotime',show=False);plot(plt.gcf(),out,'trajectory.png')
    else:raise ValueError('Unsupported cell analysis stage')
    write(data,out)
