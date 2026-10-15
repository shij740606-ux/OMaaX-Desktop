#!/usr/bin/env Rscript
# parse parameter ---------------------------------------------------------
Sys.setenv(PATH = paste("/home/userlab/bin", Sys.getenv("PATH"), sep = ":"))

library(argparser, quietly=TRUE)
# Create a parser
p <- arg_parser("draw Multiple sequence alignment for proteins")

# Add command line arguments
p <- add_argument(p, "fasta", help="input: protein sequences in fasta format", type="character")
p <- add_argument(p, "output", help="output prefix", type="character")

# Parse the command line arguments
argv <- parse_args(p)
fasta <- argv$fasta
output <- argv$output

library(msa)
library(Biostrings)

pdf.options(useDingbats = FALSE, family = "Helvetica") 

mySequenceFile <- fasta
mySequences <- readAAStringSet(mySequenceFile)

# 使用系统命令调用 MAFFT
# 创建临时文件
temp_input <- tempfile(fileext = ".fasta")
temp_output <- tempfile(fileext = ".fasta")

# 写入序列到临时文件
writeXStringSet(mySequences, temp_input)

# 调用 MAFFT
# --auto 让 MAFFT 自动选择最佳参数
# --reorder 按照输入顺序输出
# --thread -1 使用所有可用CPU核心
mafft_cmd <- sprintf("mafft --auto --reorder --thread -1 %s > %s", 
                     temp_input, temp_output)

# 执行 MAFFT 命令
system(mafft_cmd)

# 读取 MAFFT 比对结果
mafft_alignment <- readAAStringSet(temp_output)

# 将比对结果转换为 msa 对象（通过 as.MSA 函数）
# 注意：msaPrettyPrint 需要 MSA 对象，而不是 AAStringSet
# 我们需要创建一个 MSA 对象
myFirstAlignment <- msa(mafft_alignment, type = "protein")

# 或者，如果你需要保持原始顺序，可以使用：
# 先创建一个空的 MSA 对象，然后用比对序列填充
# 但 msa() 需要原始的未比对序列
# 所以更好的方法是直接使用系统命令得到比对，然后创建 MSA 对象

msaPrettyPrint(myFirstAlignment, 
               output = "pdf",
               file = paste(output, "msa_seqlogo.pdf", sep = "."),
               alFile = paste(output, "msa_seqlogo.fasta", sep = "."),
               showNames = "left",
               showLogo = "top",
               logoColors = "chemical",
               shadingMode = "similar",
               showLegend = FALSE,
               askForOverwrite = FALSE)

# 清理临时文件
unlink(c(temp_input, temp_output))
