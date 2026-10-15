suppressMessages(library(dplyr))
suppressMessages(library(clusterProfiler))

Args <- commandArgs(T)
setwd("/root/tropical_fruits/tools/kegg/sugarcane")


egg<-read.csv("sugarcane.emapper.annotations",sep="\t",header=T)

egg[egg==""]<-NA
gene2ko <- egg %>%
  dplyr::select(GID = query, Ko = KEGG_ko) %>%
  na.omit()

gene2ko[,2]<-gsub("ko:","",gene2ko[,2])

load(file = "kegg_info.RData")

pathway2gene <- gene2ko %>% left_join(ko2pathway, by = "Ko") %>% 
  dplyr::select(pathway=Pathway,gene=GID) %>%
  na.omit()  

contract_list <- read.csv(paste("sugarcane_", Args[1],".txt",sep = ""),
                               sep="\t",header=T)
contract_list <- as.factor(contract_list$gene)

df<-enricher(gene=contract_list,
             pvalueCutoff = 0.05,
             pAdjustMethod = "BH",
             TERM2GENE = pathway2gene,
             TERM2NAME = pathway2name)
df_final <- as.data.frame(df) %>% 
    select(c("Description","Count","p.adjust","GeneRatio","geneID","BgRatio","ID","pvalue"))
write.table(as.data.frame(df_final) ,paste("out_", Args[1],".txt",sep = ""),sep = "\t",quote = F,row.names=T)
