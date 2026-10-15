args <- commandArgs(trailingOnly=TRUE)
suppressPackageStartupMessages(library(DESeq2))
counts <- as.matrix(read.delim(args[1], row.names=1, check.names=FALSE))
meta <- read.csv(args[2], stringsAsFactors=FALSE)
stopifnot(all(c('sample_id','group') %in% names(meta)), !anyDuplicated(meta$sample_id), all(colnames(counts) %in% meta$sample_id))
meta <- meta[match(colnames(counts), meta$sample_id), , drop=FALSE]
rownames(meta) <- meta$sample_id
meta$group <- factor(meta$group)
stopifnot(all(c(args[3],args[4]) %in% levels(meta$group)), all(is.finite(counts)), all(counts>=0), all(abs(counts-round(counts))<1e-6))
if(length(args)>=9 && args[9]=='paired'){stopifnot('donor' %in% names(meta));meta$donor<-factor(meta$donor);design<-~donor+group}else{design<-if ('batch' %in% names(meta) && length(unique(meta$batch))>1) ~ batch+group else ~ group}
dds <- DESeqDataSetFromMatrix(round(counts),meta,design=design)
dds <- dds[rowSums(counts(dds))>0,]
dds <- DESeq(dds, quiet=TRUE)
res <- as.data.frame(results(dds,contrast=c('group',args[4],args[3]),alpha=as.numeric(args[5])))
feature <- if(length(args)>=8 && args[8]=='gene') 'gene' else 'region'
res[[feature]] <- rownames(res)
write.table(res,file=file.path(args[7],paste0('differential_',feature,'s.tsv')),sep='\t',row.names=FALSE,quote=FALSE)
sig <- res[!is.na(res$padj)&res$padj<=as.numeric(args[5])&abs(res$log2FoldChange)>=as.numeric(args[6]),]
write.table(sig,file=file.path(args[7],paste0('selected_',feature,'s.tsv')),sep='\t',row.names=FALSE,quote=FALSE)
png(file.path(args[7],'volcano.png'),width=1200,height=900,res=150)
plot(res$log2FoldChange,-log10(pmax(res$padj,1e-300)),pch=16,cex=.6,col=ifelse(rownames(res)%in%rownames(sig),'#7562d6','#999999'),xlab='log2 fold change',ylab='-log10 adjusted p')
dev.off()
capture.output(sessionInfo(),file=file.path(args[7],'sessionInfo.txt'))
