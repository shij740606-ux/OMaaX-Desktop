#!/usr/bin/env Rscript
# ============================================
# 基于已有比对结果绘制多序列比对图
# 用法: Rscript msa_figure_from_aligned.R aligned.fasta output_prefix --method mafft
# ============================================

Sys.setenv(PATH = paste("/home/userlab/bin", Sys.getenv("PATH"), sep = ":"))

library(argparser, quietly=TRUE)
library(msa)
library(Biostrings)

# Create a parser
p <- arg_parser("从已有比对结果绘制多序列比对图")

# Add command line arguments
p <- add_argument(p, "aligned_fasta", 
                  help="已比对的多序列文件 (FASTA格式)", 
                  type="character")
p <- add_argument(p, "output", 
                  help="输出文件前缀", 
                  type="character")
p <- add_argument(p, "--method", 
                  default="msa",
                  help="比对方法 (用于报告标注, 可选: msa, mafft)", 
                  type="character")

# Parse the command line arguments
argv <- parse_args(p)
aligned_fasta <- argv$aligned_fasta
output <- argv$output
method <- argv$method

cat("========================================\n")
cat("多序列比对图绘制程序 (基于已有比对)\n")
cat("输入文件:", aligned_fasta, "\n")
cat("比对方法:", method, "(已预比对)\n")
cat("输出前缀:", output, "\n")
cat("开始时间:", format(Sys.time(), "%Y-%m-%d %H:%M:%S"), "\n")
cat("========================================\n\n")

# 检查输入文件
if (!file.exists(aligned_fasta)) {
  stop(paste("错误: 文件 '", aligned_fasta, "' 不存在！", sep = ""))
}

# 读取比对结果
cat("1. 读取比对文件...\n")
tryCatch({
  aligned_sequences <- readAAStringSet(aligned_fasta)
  n_seqs <- length(aligned_sequences)
  aln_length <- width(aligned_sequences)[1]
  
  cat("   - 序列数量:", n_seqs, "\n")
  cat("   - 比对长度:", aln_length, "\n")
  
}, error = function(e) {
  stop(paste("错误: 无法读取比对文件:", e$message))
})

# 转换比对结果为MSA对象
cat("2. 准备MSA对象...\n")
tryCatch({
  myAlignment <- msa(aligned_sequences, type = "protein")
  cat("   - MSA对象创建成功\n")
}, error = function(e) {
  cat("   ⚠️  警告: 创建MSA对象失败:", e$message, "\n")
  cat("   尝试直接使用比对序列...\n")
  myAlignment <- aligned_sequences
})

# 设置PDF参数
pdf.options(useDingbats = FALSE, family = "Helvetica") 

# 绘图参数根据序列数量调整
cat("3. 设置绘图参数...\n")
if (n_seqs < 20) {
  show_sequences <- n_seqs
  cat("   - 分类: 小规模比对 (<20序列)，显示所有序列\n")
} else if (n_seqs < 50) {
  show_sequences <- 20
  cat("   - 分类: 中等规模比对 (20-50序列)，显示前20条序列\n")
} else {
  show_sequences <- 30
  cat("   - 分类: 大规模比对 (>50序列)，显示前30条序列\n")
}

# 限制比对显示长度（防止文件过大）
max_display_length <- 200
if (aln_length > max_display_length) {
  display_end <- max_display_length
  cat("   - 比对长度超过", max_display_length, "，仅显示前", max_display_length, "个位点\n")
} else {
  display_end <- aln_length
}

# 绘制比对图
cat("4. 绘制比对图...\n")
pdf_file <- paste0(output, "_msa.pdf")
tex_file <- paste0(output, "_msa.tex")
tryCatch({
  # msaPrettyPrint生成.tex文件
  msaPrettyPrint(myAlignment, 
                 output = "pdf",
                 file = pdf_file,
                 subset = c(1:min(show_sequences, n_seqs)),
                 showNames = "left",
                 showLogo = "top",
                 logoColors = "chemical",
                 shadingMode = "similar",
                 showLegend = FALSE, 
                 askForOverwrite = FALSE,
                 verbose = FALSE)
  
  # 检查PDF是否生成，若未生成则用pdflatex编译.tex
  if (!file.exists(pdf_file) && file.exists(tex_file)) {
    cat("   - PDF未自动生成，尝试pdflatex编译...\n")
    # 获取tex文件中的临时序列文件路径并替换为实际比对文件
    tex_content <- readLines(tex_file)
    # 找到texshade输入文件行并替换
    for (i in seq_along(tex_content)) {
      if (grepl("\\\\begin\\{texshade\\}", tex_content[i])) {
        # 替换为实际比对文件路径
        tex_content[i] <- paste0("\\begin{texshade}{", aligned_fasta, "}")
        break
      }
    }
    writeLines(tex_content, tex_file)
    # 编译
    work_dir <- dirname(tex_file)
    system2("pdflatex", 
            args = c("-interaction=nonstopmode", "-output-directory", work_dir, tex_file),
            stdout = TRUE, stderr = TRUE)
    # 检查PDF是否生成
    generated_pdf <- file.path(work_dir, paste0(basename(output), "_msa.pdf"))
    if (file.exists(generated_pdf)) {
      file.rename(generated_pdf, pdf_file)
    }
  }
  
  if (file.exists(pdf_file)) {
    cat("   - PDF文件:", pdf_file, "\n")
  } else {
    cat("   ⚠️  PDF生成失败\n")
  }
  
}, error = function(e) {
  cat("   ❌ PDF绘图失败:", e$message, "\n")
})

# 生成PNG格式 - 从PDF转换
cat("5. 生成PNG格式...\n")
png_file <- paste0(output, "_msa.png")
tryCatch({
  # 使用pdftoppm将PDF转换为PNG（避免LaTeX/png设备问题）
  if (file.exists(pdf_file)) {
    system2("pdftoppm", args = c("-png", "-r", "300", pdf_file, output), stdout = TRUE, stderr = TRUE)
    # pdftoppm生成的文件名格式为 output-1.png，需要重命名
    pdftoppm_output <- paste0(output, "-1.png")
    if (file.exists(pdftoppm_output)) {
      file.rename(pdftoppm_output, png_file)
      cat("   - PNG文件:", png_file, "\n")
    } else {
      cat("   ⚠️  PNG转换失败，请检查pdftoppm输出\n")
    }
  } else {
    cat("   ⚠️  PDF不存在，无法转换PNG\n")
  }
}, error = function(e) {
  cat("   ⚠️  PNG生成失败:", e$message, "\n")
})

cat("\n========================================\n")
cat("✅ 多序列比对图绘制完成！\n")
cat("输出文件:\n")
cat("  ✓ ", basename(pdf_file), "\n", sep = "")
cat("  ✓ ", basename(png_file), "\n", sep = "")
cat("\n请检查输出目录查看结果。\n")
cat("========================================\n")
