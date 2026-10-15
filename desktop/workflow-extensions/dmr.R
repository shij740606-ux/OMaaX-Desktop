args <- commandArgs(trailingOnly=TRUE)
suppressPackageStartupMessages(library(DSS))
meta <- read.csv(args[1],stringsAsFactors=FALSE)
stopifnot(all(c('sample_id','path','group') %in% names(meta)), !anyDuplicated(meta$sample_id))
values <- lapply(meta$path,function(path){x<-read.delim(path,header=FALSE);stopifnot(ncol(x)>=6);data.frame(chr=x[[1]],pos=x[[2]],N=x[[5]]+x[[6]],X=x[[5]])})
bs <- makeBSseqData(values,meta$sample_id)
group1 <- meta$sample_id[meta$group==args[2]]
group2 <- meta$sample_id[meta$group==args[3]]
stopifnot(length(group1)>0,length(group2)>0)
test <- DMLtest(bs,group1=group1,group2=group2,smoothing=TRUE)
write.table(test,file=file.path(args[6],'differential_sites.tsv'),sep='\t',row.names=FALSE,quote=FALSE)
regions <- callDMR(test,delta=as.numeric(args[4]),p.threshold=as.numeric(args[5]))
if(is.null(regions)){regions<-data.frame(chr=character(),start=integer(),end=integer())}
write.table(regions,file=file.path(args[6],'differential_methylated_regions.tsv'),sep='\t',row.names=FALSE,quote=FALSE)
if(nrow(regions)>0){bed<-data.frame(regions$chr,pmax(regions$start-1,0),regions$end);write.table(bed,file=file.path(args[6],'regions.bed'),sep='\t',row.names=FALSE,col.names=FALSE,quote=FALSE)}else{writeLines('# no differential regions selected',file.path(args[6],'regions.bed'))}
capture.output(sessionInfo(),file=file.path(args[6],'sessionInfo.txt'))
