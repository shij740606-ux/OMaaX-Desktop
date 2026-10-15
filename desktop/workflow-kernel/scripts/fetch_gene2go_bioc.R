#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)

get_arg <- function(name, default = "") {
  hit <- which(args == name)
  if (length(hit) == 0 || hit[1] == length(args)) {
    return(default)
  }
  args[[hit[1] + 1]]
}

write_json <- function(path, values) {
  escape <- function(x) {
    x <- as.character(x)
    x <- gsub("\\\\", "\\\\\\\\", x)
    x <- gsub('"', '\\"', x)
    x <- gsub("\n", "\\\\n", x)
    x
  }
  scalar <- function(x) {
    if (is.logical(x)) {
      return(ifelse(isTRUE(x), "true", "false"))
    }
    if (is.numeric(x)) {
      return(as.character(x))
    }
    paste0('"', escape(x), '"')
  }
  lines <- c("{")
  keys <- names(values)
  for (idx in seq_along(keys)) {
    key <- keys[[idx]]
    comma <- if (idx < length(keys)) "," else ""
    lines <- c(lines, paste0('  "', key, '": ', scalar(values[[key]]), comma))
  }
  lines <- c(lines, "}")
  writeLines(lines, path)
}

species_hint <- trimws(get_arg("--species", ""))
source <- tolower(trimws(get_arg("--source", "auto")))
out_file <- get_arg("--out", "")
report_file <- get_arg("--report", "")
cache_dir <- get_arg("--cache-dir", "")

if (!nzchar(out_file)) {
  stop("--out is required")
}
if (!nzchar(report_file)) {
  report_file <- paste0(out_file, ".report.json")
}

dir.create(dirname(out_file), recursive = TRUE, showWarnings = FALSE)
dir.create(dirname(report_file), recursive = TRUE, showWarnings = FALSE)
if (nzchar(cache_dir)) {
  dir.create(cache_dir, recursive = TRUE, showWarnings = FALSE)
  if (requireNamespace("AnnotationHub", quietly = TRUE) &&
      exists("setAnnotationHubOption", asNamespace("AnnotationHub"))) {
    AnnotationHub::setAnnotationHubOption("CACHE", cache_dir)
  }
}

aliases <- c(
  "at" = "Arabidopsis thaliana",
  "ath" = "Arabidopsis thaliana",
  "arabidopsis" = "Arabidopsis thaliana",
  "arabidopsis_thaliana" = "Arabidopsis thaliana",
  "os" = "Oryza sativa",
  "osa" = "Oryza sativa",
  "rice" = "Oryza sativa",
  "oryza_sativa" = "Oryza sativa",
  "sl" = "Solanum lycopersicum",
  "sly" = "Solanum lycopersicum",
  "tomato" = "Solanum lycopersicum",
  "solanum_lycopersicum" = "Solanum lycopersicum",
  "hsa" = "Homo sapiens",
  "human" = "Homo sapiens",
  "mmu" = "Mus musculus",
  "mouse" = "Mus musculus"
)

normalize_species <- function(x) {
  key <- tolower(gsub(" ", "_", trimws(x)))
  if (key %in% names(aliases)) {
    aliases[[key]]
  } else {
    trimws(x)
  }
}

species <- normalize_species(species_hint)
result <- data.frame(gene_id = character(), go_id = character(), stringsAsFactors = FALSE)
method <- ""
resource <- ""
error_message <- ""

try_biomart <- function(species) {
  if (!requireNamespace("biomaRt", quietly = TRUE)) {
    stop("biomaRt is not installed in this R environment")
  }
  datasets <- NULL
  mart <- NULL
  mart <- tryCatch(
    biomaRt::useEnsemblGenomes(biomart = "plants_mart"),
    error = function(e) biomaRt::useEnsembl(biomart = "genes")
  )
  datasets <- biomaRt::listDatasets(mart)
  species_lower <- tolower(species)
  ds <- datasets[grepl(gsub(" ", ".*", species_lower), tolower(datasets$description)), , drop = FALSE]
  if (nrow(ds) == 0) {
    compact <- gsub("[^a-z]", "", species_lower)
    ds <- datasets[grepl(compact, gsub("[^a-z]", "", tolower(datasets$description))), , drop = FALSE]
  }
  if (nrow(ds) == 0) {
    stop(paste("No biomaRt dataset matched species", species))
  }
  mart <- biomaRt::useDataset(ds$dataset[[1]], mart = mart)
  attrs <- biomaRt::listAttributes(mart)$name
  gene_candidates <- attrs[grepl("gene.*id", attrs)]
  go_candidates <- attrs[grepl("^go", attrs)]
  gene_attr <- if ("ensembl_gene_id" %in% attrs) "ensembl_gene_id" else if (length(gene_candidates)) gene_candidates[[1]] else ""
  go_attr <- if ("go_id" %in% attrs) "go_id" else if (length(go_candidates)) go_candidates[[1]] else ""
  if (!nzchar(gene_attr) || !nzchar(go_attr)) {
    stop("Could not identify gene/GO attributes in selected biomaRt dataset")
  }
  tab <- biomaRt::getBM(attributes = c(gene_attr, go_attr), mart = mart)
  names(tab) <- c("gene_id", "go_id")
  tab <- tab[nzchar(tab$gene_id) & grepl("^GO:[0-9]{7}$", tab$go_id), , drop = FALSE]
  list(data = unique(tab), resource = ds$dataset[[1]])
}

try_annotationhub <- function(species) {
  if (!requireNamespace("AnnotationHub", quietly = TRUE)) {
    stop("AnnotationHub is not installed in this R environment")
  }
  if (!requireNamespace("AnnotationDbi", quietly = TRUE)) {
    stop("AnnotationDbi is not installed in this R environment")
  }
  ah <- AnnotationHub::AnnotationHub()
  q <- AnnotationHub::query(ah, c(species, "OrgDb"))
  if (length(q) == 0) {
    q <- AnnotationHub::query(ah, c(species, "OrgDb", "plants"))
  }
  if (length(q) == 0) {
    stop(paste("No AnnotationHub OrgDb resource matched species", species))
  }
  db <- q[[1]]
  keytypes <- AnnotationDbi::keytypes(db)
  preferred <- c("TAIR", "ENSEMBL", "GID", "ENTREZID", "SYMBOL")
  key_candidates <- preferred[preferred %in% keytypes]
  if (!length(key_candidates)) {
    stop("Selected OrgDb resource has no supported gene key type")
  }
  keytype <- key_candidates[[1]]
  keys <- AnnotationDbi::keys(db, keytype = keytype)
  cols <- intersect(c("GO", "ONTOLOGY"), AnnotationDbi::columns(db))
  if (!"GO" %in% cols) {
    stop("Selected OrgDb resource has no GO column")
  }
  tab <- AnnotationDbi::select(db, keys = keys, keytype = keytype, columns = cols)
  tab <- tab[!is.na(tab$GO) & grepl("^GO:[0-9]{7}$", tab$GO), , drop = FALSE]
  tab <- data.frame(gene_id = as.character(tab[[keytype]]), go_id = as.character(tab$GO), stringsAsFactors = FALSE)
  list(data = unique(tab), resource = paste0("AnnotationHub:", names(q)[[1]], ":", keytype))
}

attempts <- character()
if (source %in% c("auto", "biomart")) {
  attempts <- c(attempts, "biomart")
}
if (source %in% c("auto", "annotationhub")) {
  attempts <- c(attempts, "annotationhub")
}

for (candidate in attempts) {
  fetched <- tryCatch(
    {
      if (candidate == "biomart") {
        try_biomart(species)
      } else {
        try_annotationhub(species)
      }
    },
    error = function(e) {
      error_message <<- paste(candidate, conditionMessage(e), sep = ": ")
      NULL
    }
  )
  if (!is.null(fetched) && nrow(fetched$data) > 0) {
    result <- fetched$data
    method <- candidate
    resource <- fetched$resource
    break
  }
}

if (nrow(result) > 0) {
  result <- unique(result[order(result$gene_id, result$go_id), , drop = FALSE])
  write.table(result, out_file, sep = "\t", quote = FALSE, row.names = FALSE, col.names = FALSE)
  write_json(report_file, list(
    status = "success",
    species = species,
    source = method,
    resource = resource,
    rows = nrow(result),
    genes = length(unique(result$gene_id)),
    go_terms = length(unique(result$go_id)),
    error = ""
  ))
  quit(status = 0)
}

writeLines(character(), out_file)
write_json(report_file, list(
  status = "failed",
  species = species,
  source = source,
  resource = resource,
  rows = 0,
  genes = 0,
  go_terms = 0,
  error = error_message
))
quit(status = 2)
