#!/usr/bin/env Rscript
#============================================================================
# PPI Network Visualization
# 蛋白互作网络可视化
#============================================================================

# Parse arguments using commandArgs (no external dependencies)
args <- commandArgs(trailingOnly = TRUE)

network_file <- NULL
output_file <- NULL
prefix <- "ppi"

# Parse arguments
i <- 1
while (i <= length(args)) {
  if (args[i] == "--network" && i < length(args)) {
    network_file <- args[i + 1]
    i <- i + 2
  } else if (args[i] == "--output" && i < length(args)) {
    output_file <- args[i + 1]
    i <- i + 2
  } else if (args[i] == "--prefix" && i < length(args)) {
    prefix <- args[i + 1]
    i <- i + 2
  } else {
    i <- i + 1
  }
}

# Validate arguments
if (is.null(network_file) || is.null(output_file)) {
  cat("Usage: Rscript draw_ppi_network.R --network <file> --output <file> [--prefix <name>]\n")
  quit(status = 1)
}

cat("=" ,rep("=", 60), "\n", sep="")
cat("PPI Network Visualization\n")
cat("=" ,rep("=", 60), "\n\n")

has_igraph <- requireNamespace("igraph", quietly = TRUE)
has_ggraph <- requireNamespace("ggraph", quietly = TRUE)
has_ggplot2 <- requireNamespace("ggplot2", quietly = TRUE)

if (has_igraph) {
  library(igraph)
}
if (has_ggraph && has_ggplot2) {
  library(ggraph)
  library(ggplot2)
}

#============================================================================
# Step 1: Read network data
#============================================================================
cat("Step 1: Reading network data...\n")

if (!file.exists(network_file)) {
  stop(paste("Network file not found:", network_file))
}

edges <- read.table(network_file, header = TRUE, sep = "\t", stringsAsFactors = FALSE)
cat("  Edges:", nrow(edges), "\n")
cat("  Columns:", paste(colnames(edges), collapse = ", "), "\n\n")

score_col <- if ("combined_score" %in% colnames(edges)) "combined_score" else "score"
if (!score_col %in% colnames(edges)) {
  stop("Network file must contain score or combined_score column")
}
edge_scores <- as.numeric(edges[[score_col]])
edge_weights <- ifelse(edge_scores > 1, edge_scores / 1000, edge_scores)

if (!has_igraph) {
  cat("igraph not installed; using base R fallback plot\n")
  nodes <- sort(unique(c(edges$protein1, edges$protein2)))
  n <- length(nodes)
  angle <- seq(0, 2 * pi, length.out = n + 1)[1:n]
  coords <- data.frame(name = nodes, x = cos(angle), y = sin(angle))
  rownames(coords) <- coords$name
  pdf(output_file, width = 12, height = 10)
  plot(coords$x, coords$y, type = "n", axes = FALSE, xlab = "", ylab = "",
       main = paste(prefix, "PPI Network\n", n, "proteins,", nrow(edges), "interactions"))
  for (idx in seq_len(nrow(edges))) {
    a <- coords[edges$protein1[idx], ]
    b <- coords[edges$protein2[idx], ]
    col <- ifelse(edge_weights[idx] >= 0.7, "red",
                  ifelse(edge_weights[idx] >= 0.4, "orange", "gray"))
    segments(a$x, a$y, b$x, b$y, col = col, lwd = 1 + edge_weights[idx] * 3)
  }
  points(coords$x, coords$y, pch = 21, bg = "lightblue", cex = 2)
  text(coords$x, coords$y, labels = coords$name, pos = 3, cex = 0.7)
  legend("bottomright",
         legend = c("High (>=0.7)", "Medium (0.4-0.7)", "Low (<0.4)"),
         col = c("red", "orange", "gray"), lty = 1, lwd = 2,
         title = "Confidence", cex = 0.8)
  dev.off()
  cat("Base R network plot generated:", output_file, "\n")
  quit(status = 0)
}

#============================================================================
# Step 2: Create graph
#============================================================================
cat("Step 2: Creating graph...\n")

# 创建边列表
edge_list <- as.matrix(edges[, c("protein1", "protein2")])

# 创建图
g <- graph_from_edgelist(edge_list, directed = FALSE)

# 添加边权重（score）
E(g)$weight <- edge_weights
E(g)$confidence <- cut(edge_weights,
                       breaks = c(0, 0.4, 0.7, 1.0),
                       labels = c("Low", "Medium", "High"))

cat("  Nodes:", vcount(g), "\n")
cat("  Edges:", ecount(g), "\n\n")

#============================================================================
# Step 3: Calculate network metrics
#============================================================================
cat("Step 3: Calculating network metrics...\n")

# 度中心性
V(g)$degree <- degree(g)

# 介数中心性
V(g)$betweenness <- betweenness(g)

# 聚类系数
V(g)$clustering <- transitivity(g, type = "local")
V(g)$clustering[is.na(V(g)$clustering)] <- 0  # 处理NA值

cat("  Average degree:", mean(V(g)$degree), "\n")
cat("  Network density:", edge_density(g), "\n")
cat("  Clustering coefficient:", transitivity(g), "\n\n")

#============================================================================
# Step 4: Create visualization
#============================================================================
cat("Step 4: Creating visualization...\n")

# 设置节点大小（基于度中心性）
V(g)$size <- sqrt(V(g)$degree) * 3 + 2

# 设置节点颜色（基于聚类系数）
node_colors <- colorRampPalette(c("lightblue", "darkblue"))(100)
color_indices <- cut(V(g)$clustering, breaks = 100, labels = FALSE)
color_indices[is.na(color_indices)] <- 1  # 处理NA值
V(g)$color <- node_colors[color_indices]

# 创建PDF
pdf(output_file, width = 12, height = 10)

# 布局算法（使用Fruchterman-Reingold）
set.seed(42)
layout <- layout_with_fr(g, niter = 1000)

# 绘制网络
plot(g,
     layout = layout,
     vertex.size = V(g)$size,
     vertex.color = V(g)$color,
     vertex.label = V(g)$name,
     vertex.label.cex = 0.6,
     vertex.label.color = "black",
     edge.width = E(g)$weight * 2,
     edge.color = ifelse(E(g)$weight >= 0.7, "red",
                        ifelse(E(g)$weight >= 0.4, "orange", "gray")),
     main = paste(prefix, "PPI Network\n", 
                  vcount(g), "proteins,", ecount(g), "interactions"),
     sub = "Edge color: Red (high confidence ≥0.7), Orange (medium 0.4-0.7), Gray (low <0.4)"
)

# 添加图例
legend("bottomright",
       legend = c("High (≥0.7)", "Medium (0.4-0.7)", "Low (<0.4)"),
       col = c("red", "orange", "gray"),
       lty = 1,
       lwd = 2,
       title = "Confidence",
       cex = 0.8
)

# 使用ggraph绘制更美观的版本（如果包可用）
if (has_ggraph && has_ggplot2) tryCatch({
  p <- ggraph(g, layout = "fr") +
    geom_edge_link(aes(edge_width = weight, edge_color = confidence), alpha = 0.6) +
    geom_node_point(aes(size = degree, color = clustering)) +
    geom_node_text(aes(label = name), repel = TRUE, size = 2) +
    scale_edge_width(range = c(0.5, 3)) +
    scale_color_gradient(low = "lightblue", high = "darkblue") +
    theme_graph() +
    labs(title = paste(prefix, "PPI Network"),
         subtitle = paste(vcount(g), "proteins,", ecount(g), "interactions"))
  
  print(p)
}, error = function(e) {
  cat("  ggraph plot skipped:", e$message, "\n")
})

dev.off()

cat("\n" ,rep("=", 60), "\n", sep="")
cat("Completed!\n")
cat("Output file:", output_file, "\n")
cat("=" ,rep("=", 60), "\n", sep="")

quit(status = 0)
