#!/usr/bin/env python3
"""Check RNA-seq library strandness from a small aligned read subset.

Primary method:
  1. sample a small number of paired FASTQ reads
  2. align to the genome with HISAT2
  3. sort/index BAM with samtools
  4. run RSeQC infer_experiment.py when available

Fallback method:
  When RSeQC is not installed, use an internal BED12/SAM orientation counter
  that mirrors the common paired-end interpretation:
    A: 1++,1--,2+-,2-+  -> forward / HISAT2 FR / featureCounts -s 1
    B: 1+-,1-+,2++,2--  -> reverse / HISAT2 RF / featureCounts -s 2

The fallback is useful for local diagnosis, but RSeQC remains the recommended
reporting source when available.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


FASTA_EXTENSIONS = (".fa", ".fasta", ".fna", ".fa.gz", ".fasta.gz", ".fna.gz")
ANNOTATION_EXTENSIONS = (".gtf", ".gff", ".gff3", ".gtf.gz", ".gff.gz", ".gff3.gz")
FASTQ_EXTENSIONS = (".fastq", ".fq", ".fastq.gz", ".fq.gz")
BED_BIN_SIZE = 10_000


@dataclass(frozen=True)
class FastqPair:
    sample: str
    r1: Path
    r2: Path


def open_text(path: Path, mode: str = "rt"):
    if str(path).endswith(".gz"):
        return gzip.open(path, mode)
    return path.open(mode)


def strip_fastq_extension(path: Path) -> str:
    name = path.name
    for ext in FASTQ_EXTENSIONS:
        if name.endswith(ext):
            return name[: -len(ext)]
    return path.stem


def fastq_mate_key(path: Path) -> tuple[str, int] | None:
    base = strip_fastq_extension(path)
    patterns = [
        (r"^(?P<sample>.+?)(?:[_\.-])R(?P<mate>[12])$", True),
        (r"^(?P<sample>.+?)(?:[_\.-])(?P<mate>[12])$", True),
        (r"^(?P<sample>.+?)(?:[_\.-])read(?P<mate>[12])$", True),
    ]
    for pattern, _ in patterns:
        match = re.match(pattern, base, flags=re.IGNORECASE)
        if match:
            return match.group("sample"), int(match.group("mate"))
    return None


def discover_fastq_pairs(fastq_dir: Path) -> list[FastqPair]:
    files = sorted(p for p in fastq_dir.iterdir() if p.is_file() and p.name.endswith(FASTQ_EXTENSIONS))
    grouped: dict[str, dict[int, Path]] = {}
    for path in files:
        key = fastq_mate_key(path)
        if not key:
            continue
        sample, mate = key
        grouped.setdefault(sample, {})[mate] = path
    pairs = [
        FastqPair(sample=sample, r1=mates[1], r2=mates[2])
        for sample, mates in grouped.items()
        if 1 in mates and 2 in mates
    ]
    return sorted(pairs, key=lambda item: item.sample)


def discover_first(root: Path, extensions: tuple[str, ...], preferred_dir: str | None = None) -> Path | None:
    candidates = sorted(p for p in root.rglob("*") if p.is_file() and p.name.endswith(extensions))
    if preferred_dir:
        preferred = [p for p in candidates if preferred_dir in p.parts]
        if preferred:
            return preferred[0]
    return candidates[0] if candidates else None


def parse_attributes(text: str, is_gff3: bool) -> dict[str, str]:
    attrs: dict[str, str] = {}
    if is_gff3:
        for chunk in text.strip().split(";"):
            if not chunk or "=" not in chunk:
                continue
            key, value = chunk.split("=", 1)
            attrs[key.strip()] = value.strip().split(",")[0]
    else:
        for match in re.finditer(r'(\S+)\s+"([^"]+)"', text):
            attrs[match.group(1)] = match.group(2)
    return attrs


def convert_annotation_to_bed12(annotation: Path, bed12: Path) -> int:
    """Convert simple GTF/GFF3 transcript exon models to BED12."""
    is_gff3 = any(str(annotation).endswith(ext) for ext in (".gff", ".gff3", ".gff.gz", ".gff3.gz"))
    transcripts: dict[str, dict[str, object]] = {}

    with open_text(annotation) as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 9:
                continue
            chrom, _source, feature, start_s, end_s, _score, strand, _phase, attr_s = fields
            feature_l = feature.lower()
            if strand not in {"+", "-"}:
                continue
            start = int(start_s)
            end = int(end_s)
            attrs = parse_attributes(attr_s, is_gff3=is_gff3)

            if feature_l in {"transcript", "mrna", "rna"}:
                tx_id = attrs.get("transcript_id") or attrs.get("ID") or attrs.get("Name")
                if not tx_id:
                    continue
                transcripts.setdefault(
                    tx_id,
                    {"chrom": chrom, "strand": strand, "start": start, "end": end, "exons": []},
                )
                transcripts[tx_id].update({"chrom": chrom, "strand": strand, "start": start, "end": end})
            elif feature_l == "exon":
                tx_id = attrs.get("transcript_id") or attrs.get("Parent") or attrs.get("ID")
                if not tx_id:
                    continue
                tx = transcripts.setdefault(
                    tx_id,
                    {"chrom": chrom, "strand": strand, "start": start, "end": end, "exons": []},
                )
                tx["chrom"] = chrom
                tx["strand"] = strand
                tx["start"] = min(int(tx.get("start", start)), start)
                tx["end"] = max(int(tx.get("end", end)), end)
                tx["exons"].append((start, end))  # type: ignore[index]

    written = 0
    bed12.parent.mkdir(parents=True, exist_ok=True)
    with bed12.open("w") as out:
        for tx_id, tx in sorted(transcripts.items()):
            exons = sorted(set(tx.get("exons", [])))  # type: ignore[arg-type]
            if not exons:
                continue
            tx_start0 = min(start for start, _end in exons) - 1
            tx_end = max(end for _start, end in exons)
            block_sizes = [end - start + 1 for start, end in exons]
            block_starts = [start - 1 - tx_start0 for start, _end in exons]
            fields = [
                str(tx["chrom"]),
                str(tx_start0),
                str(tx_end),
                tx_id,
                "0",
                str(tx["strand"]),
                str(tx_start0),
                str(tx_end),
                "0",
                str(len(exons)),
                ",".join(map(str, block_sizes)),
                ",".join(map(str, block_starts)),
            ]
            out.write("\t".join(fields) + "\n")
            written += 1
    if written == 0:
        raise RuntimeError(f"No transcript exon models were converted from {annotation}")
    return written


def parse_bed12_index(bed12: Path) -> dict[str, dict[int, list[tuple[int, int, str]]]]:
    index: dict[str, dict[int, list[tuple[int, int, str]]]] = {}
    with bed12.open() as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 12:
                continue
            chrom = fields[0]
            tx_start = int(fields[1])
            strand = fields[5]
            sizes = [int(x) for x in fields[10].rstrip(",").split(",") if x]
            starts = [int(x) for x in fields[11].rstrip(",").split(",") if x]
            for size, rel_start in zip(sizes, starts):
                start = tx_start + rel_start
                end = start + size
                first_bin = start // BED_BIN_SIZE
                last_bin = max(start, end - 1) // BED_BIN_SIZE
                chrom_bins = index.setdefault(chrom, {})
                for bin_id in range(first_bin, last_bin + 1):
                    chrom_bins.setdefault(bin_id, []).append((start, end, strand))
    return index


def cigar_ref_len(cigar: str) -> int:
    total = 0
    for length, op in re.findall(r"(\d+)([MIDNSHP=X])", cigar):
        if op in {"M", "D", "N", "=", "X"}:
            total += int(length)
    return total


def overlapping_strands(
    index: dict[str, dict[int, list[tuple[int, int, str]]]],
    chrom: str,
    start0: int,
    end0: int,
) -> set[str]:
    strands: set[str] = set()
    chrom_bins = index.get(chrom)
    if not chrom_bins:
        return strands
    first_bin = start0 // BED_BIN_SIZE
    last_bin = max(start0, end0 - 1) // BED_BIN_SIZE
    seen: set[tuple[int, int, str]] = set()
    for bin_id in range(first_bin, last_bin + 1):
        for exon_start, exon_end, strand in chrom_bins.get(bin_id, []):
            key = (exon_start, exon_end, strand)
            if key in seen:
                continue
            seen.add(key)
            if start0 < exon_end and end0 > exon_start:
                strands.add(strand)
    return strands


def classify_orientation(read_num: int, read_strand: str, gene_strand: str) -> str:
    same = read_strand == gene_strand
    if read_num == 1:
        return "A" if same else "B"
    if read_num == 2:
        return "A" if not same else "B"
    return "A" if same else "B"


def infer_orientation_from_sam_lines(
    sam_lines: Iterable[str],
    bed12: Path,
    threshold: float = 0.8,
    max_alignments: int = 200_000,
) -> dict[str, object]:
    index = parse_bed12_index(bed12)
    category_a = 0
    category_b = 0
    failed = 0
    scanned = 0

    for line in sam_lines:
        if not line or line.startswith("@"):
            continue
        fields = line.rstrip("\n").split("\t")
        if len(fields) < 6:
            continue
        flag = int(fields[1])
        if flag & 4 or flag & 256 or flag & 2048:
            continue
        chrom = fields[2]
        if chrom == "*":
            continue
        pos0 = int(fields[3]) - 1
        ref_len = cigar_ref_len(fields[5])
        if ref_len <= 0:
            continue
        scanned += 1
        strands = overlapping_strands(index, chrom, pos0, pos0 + ref_len)
        if len(strands) != 1:
            failed += 1
        else:
            gene_strand = next(iter(strands))
            read_strand = "-" if flag & 16 else "+"
            read_num = 1 if flag & 64 else 2 if flag & 128 else 0
            category = classify_orientation(read_num, read_strand, gene_strand)
            if category == "A":
                category_a += 1
            else:
                category_b += 1
        if scanned >= max_alignments:
            break

    informative = category_a + category_b
    total = informative + failed
    frac_a = category_a / informative if informative else 0.0
    frac_b = category_b / informative if informative else 0.0
    failed_fraction = failed / total if total else 1.0

    if informative == 0:
        classification = "undetermined"
        hisat2 = ""
        featurecounts = ""
    elif frac_a >= threshold:
        classification = "forward"
        hisat2 = "FR"
        featurecounts = "1"
    elif frac_b >= threshold:
        classification = "reverse"
        hisat2 = "RF"
        featurecounts = "2"
    elif 0.35 <= frac_a <= 0.65 and 0.35 <= frac_b <= 0.65:
        classification = "non_stranded"
        hisat2 = ""
        featurecounts = "0"
    else:
        classification = "ambiguous"
        hisat2 = ""
        featurecounts = ""

    return {
        "method": "internal",
        "classification": classification,
        "hisat2_rna_strandness": hisat2,
        "featurecounts_s": featurecounts,
        "scanned_alignments": scanned,
        "informative_alignments": informative,
        "failed_alignments": failed,
        "failed_fraction": round(failed_fraction, 6),
        "category_A_1pp_1mm_2pm_2mp": category_a,
        "category_B_1pm_1mp_2pp_2mm": category_b,
        "fraction_A": round(frac_a, 6),
        "fraction_B": round(frac_b, 6),
        "threshold": threshold,
    }


def run(cmd: list[str], log: Path | None = None, stdout_path: Path | None = None) -> subprocess.CompletedProcess:
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as handle:
            handle.write("$ " + " ".join(cmd) + "\n")
    stdout_handle = stdout_path.open("w") if stdout_path else subprocess.PIPE
    try:
        result = subprocess.run(cmd, text=True, stdout=stdout_handle, stderr=subprocess.PIPE, check=False)
    finally:
        if stdout_path:
            stdout_handle.close()
    if log:
        with log.open("a") as handle:
            if result.stderr:
                handle.write(result.stderr + "\n")
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr}")
    return result


def subsample_fastq_pair(pair: FastqPair, outdir: Path, reads: int) -> FastqPair:
    outdir.mkdir(parents=True, exist_ok=True)
    out_r1 = outdir / f"{pair.sample}_subset_1.fastq.gz"
    out_r2 = outdir / f"{pair.sample}_subset_2.fastq.gz"

    def copy_records(src: Path, dest: Path) -> None:
        with open_text(src, "rt") as inp, gzip.open(dest, "wt") as out:
            for idx, line in enumerate(inp):
                if idx >= reads * 4:
                    break
                out.write(line)

    copy_records(pair.r1, out_r1)
    copy_records(pair.r2, out_r2)
    return FastqPair(sample=pair.sample, r1=out_r1, r2=out_r2)


def build_hisat2_index(hisat2_build: str, genome: Path, index_prefix: Path, threads: int, log: Path) -> None:
    expected = Path(str(index_prefix) + ".1.ht2")
    expected_large = Path(str(index_prefix) + ".1.ht2l")
    if expected.exists() or expected_large.exists():
        return
    index_prefix.parent.mkdir(parents=True, exist_ok=True)
    run([hisat2_build, "-p", str(threads), str(genome), str(index_prefix)], log=log)


def prepare_genome_for_hisat2(genome: Path, outdir: Path, log: Path) -> Path:
    """Return a FASTA path suitable for hisat2-build.

    Some HISAT2 builds do not read gzipped FASTA reliably.  Keep the user's
    source file untouched and create a reusable decompressed copy in outdir.
    """
    if not str(genome).endswith(".gz"):
        return genome

    prepared_dir = outdir / "prepared_reference"
    prepared_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(str(genome.resolve()).encode("utf-8")).hexdigest()[:12]
    base = genome.name[:-3]
    prepared = prepared_dir / f"{digest}_{base}"
    if prepared.exists() and prepared.stat().st_size > 0:
        return prepared

    with log.open("a") as handle:
        handle.write(f"Decompressing genome FASTA for hisat2-build: {genome} -> {prepared}\n")
    with gzip.open(genome, "rb") as src, prepared.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
    return prepared


def align_pair(
    hisat2: str,
    samtools: str,
    index_prefix: Path,
    pair: FastqPair,
    bam: Path,
    threads: int,
    log: Path,
) -> None:
    bam.parent.mkdir(parents=True, exist_ok=True)
    hisat2_cmd = [
        hisat2,
        "-x",
        str(index_prefix),
        "-1",
        str(pair.r1),
        "-2",
        str(pair.r2),
        "-p",
        str(threads),
        "--new-summary",
    ]
    sort_cmd = [samtools, "sort", "-@", str(threads), "-o", str(bam), "-"]
    with log.open("a") as handle:
        handle.write("$ " + " ".join(hisat2_cmd) + " | " + " ".join(sort_cmd) + "\n")
    hisat2_proc = subprocess.Popen(hisat2_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
    sort_proc = subprocess.Popen(sort_cmd, stdin=hisat2_proc.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
    assert hisat2_proc.stdout is not None
    hisat2_proc.stdout.close()
    _sort_stdout, sort_stderr = sort_proc.communicate()
    hisat2_stderr = hisat2_proc.stderr.read() if hisat2_proc.stderr else b""
    hisat2_return = hisat2_proc.wait()
    with log.open("ab") as handle:
        handle.write(hisat2_stderr)
        handle.write(sort_stderr)
    if hisat2_return != 0:
        raise RuntimeError(f"HISAT2 failed for {pair.sample}; see {log}")
    if sort_proc.returncode != 0:
        raise RuntimeError(f"samtools sort failed for {pair.sample}; see {log}")
    run([samtools, "index", str(bam)], log=log)


def run_internal_infer(samtools: str, bam: Path, bed12: Path, threshold: float, max_alignments: int, log: Path) -> dict[str, object]:
    cmd = [samtools, "view", "-F", "2308", str(bam)]
    with log.open("a") as handle:
        handle.write("$ " + " ".join(cmd) + "\n")
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    with log.open("a") as handle:
        if proc.stderr:
            handle.write(proc.stderr + "\n")
    if proc.returncode != 0:
        raise RuntimeError(f"samtools view failed for {bam}: {proc.stderr}")
    return infer_orientation_from_sam_lines(proc.stdout.splitlines(), bed12, threshold=threshold, max_alignments=max_alignments)


def parse_rseqc_output(text: str, threshold: float) -> dict[str, object]:
    frac_a = None
    frac_b = None
    failed = None
    for line in text.splitlines():
        lower = line.lower()
        number = re.findall(r"[-+]?\d*\.\d+|\d+", line)
        if not number:
            continue
        value = float(number[-1])
        if "failed to determine" in lower:
            failed = value
        elif "1++,1--,2+-,2-+" in line:
            frac_a = value
        elif "1+-,1-+,2++,2--" in line:
            frac_b = value
    frac_a = frac_a if frac_a is not None else 0.0
    frac_b = frac_b if frac_b is not None else 0.0
    if frac_a >= threshold:
        classification, hisat2, featurecounts = "forward", "FR", "1"
    elif frac_b >= threshold:
        classification, hisat2, featurecounts = "reverse", "RF", "2"
    elif 0.35 <= frac_a <= 0.65 and 0.35 <= frac_b <= 0.65:
        classification, hisat2, featurecounts = "non_stranded", "", "0"
    else:
        classification, hisat2, featurecounts = "ambiguous", "", ""
    return {
        "method": "rseqc",
        "classification": classification,
        "hisat2_rna_strandness": hisat2,
        "featurecounts_s": featurecounts,
        "failed_fraction": failed,
        "fraction_A": frac_a,
        "fraction_B": frac_b,
        "raw_output": text,
        "threshold": threshold,
    }


def run_rseqc(infer_experiment: str, bam: Path, bed12: Path, threshold: float, log: Path) -> dict[str, object]:
    cmd = [infer_experiment, "-r", str(bed12), "-i", str(bam)]
    result = run(cmd, log=log)
    text = result.stdout or ""
    with log.open("a") as handle:
        handle.write(text + "\n")
    return parse_rseqc_output(text, threshold=threshold)


def revcomp(seq: str) -> str:
    return seq.translate(str.maketrans("ACGTNacgtn", "TGCANtgcan"))[::-1]


def create_simulation_dataset(base_dir: Path, library: str = "reverse", read_count: int = 40) -> dict[str, Path]:
    if library not in {"forward", "reverse"}:
        raise ValueError("--simulate-library must be forward or reverse")
    base_dir.mkdir(parents=True, exist_ok=True)
    genome = base_dir / "sim_genome.fa"
    annotation = base_dir / "sim_annotation.gtf"
    fastq_dir = base_dir / "rnaseq"
    fastq_dir.mkdir(exist_ok=True)

    random.seed(13)
    chrom = "".join(random.choice("ACGT") for _ in range(2000))
    genome.write_text(">chr1\n" + "\n".join(chrom[i : i + 80] for i in range(0, len(chrom), 80)) + "\n")
    annotation.write_text(
        "\n".join(
            [
                'chr1\tsim\ttranscript\t501\t1300\t.\t+\t.\tgene_id "g1"; transcript_id "t1";',
                'chr1\tsim\texon\t501\t1300\t.\t+\t.\tgene_id "g1"; transcript_id "t1";',
            ]
        )
        + "\n"
    )

    read_len = 60
    insert = 180
    r1_path = fastq_dir / f"SIM_{library}_1.fastq.gz"
    r2_path = fastq_dir / f"SIM_{library}_2.fastq.gz"
    with gzip.open(r1_path, "wt") as r1, gzip.open(r2_path, "wt") as r2:
        for i in range(read_count):
            start = 560 + (i % 120)
            fragment = chrom[start : start + insert]
            if library == "reverse":
                seq1 = revcomp(fragment[-read_len:])
                seq2 = fragment[:read_len]
            else:
                seq1 = fragment[:read_len]
                seq2 = revcomp(fragment[-read_len:])
            qual = "I" * read_len
            r1.write(f"@SIM_{i}/1\n{seq1}\n+\n{qual}\n")
            r2.write(f"@SIM_{i}/2\n{seq2}\n+\n{qual}\n")

    return {"genome": genome, "annotation": annotation, "fastq_dir": fastq_dir}


def find_tool(name: str, explicit: str | None = None) -> str | None:
    if explicit:
        return explicit if Path(explicit).exists() else explicit
    return shutil.which(name)


def resolve_tools(args: argparse.Namespace) -> dict[str, str | None]:
    return {
        "hisat2": find_tool("hisat2", args.hisat2),
        "hisat2_build": find_tool("hisat2-build", args.hisat2_build),
        "samtools": find_tool("samtools", args.samtools),
        "infer_experiment": find_tool("infer_experiment.py", args.infer_experiment),
    }


def write_reports(outdir: Path, results: list[dict[str, object]]) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = outdir / "strandness_report.json"
    tsv_path = outdir / "strandness_report.tsv"
    txt_path = outdir / "strandness_summary.txt"
    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    columns = [
        "sample",
        "method",
        "classification",
        "hisat2_rna_strandness",
        "featurecounts_s",
        "fraction_A",
        "fraction_B",
        "failed_fraction",
        "informative_alignments",
    ]
    with tsv_path.open("w") as out:
        out.write("\t".join(columns) + "\n")
        for result in results:
            out.write("\t".join(str(result.get(col, "")) for col in columns) + "\n")
    with txt_path.open("w") as out:
        for result in results:
            out.write(
                f"{result.get('sample')}: {result.get('classification')} "
                f"(HISAT2={result.get('hisat2_rna_strandness') or 'none'}, "
                f"featureCounts -s {result.get('featurecounts_s') or 'NA'}, "
                f"method={result.get('method')})\n"
            )


def run_pipeline(args: argparse.Namespace) -> list[dict[str, object]]:
    if args.simulate:
        sim = create_simulation_dataset(args.outdir / "simulation_input", library=args.simulate_library, read_count=args.simulate_reads)
        input_dir = args.outdir / "simulation_input"
        genome = sim["genome"]
        annotation = sim["annotation"]
        fastq_dir = sim["fastq_dir"]
    else:
        input_dir = args.input_dir
        genome = args.genome or discover_first(input_dir, FASTA_EXTENSIONS, preferred_dir="genome")
        annotation = args.annotation or discover_first(input_dir, ANNOTATION_EXTENSIONS, preferred_dir="genome")
        fastq_dir = args.fastq_dir or (input_dir / "rnaseq")

    if not genome or not Path(genome).exists():
        raise SystemExit("Genome FASTA not found. Provide --genome or place *.fa/*.fa.gz under input-dir/genome.")
    if not annotation or not Path(annotation).exists():
        raise SystemExit("Annotation GTF/GFF3 not found. Provide --annotation or place *.gtf/*.gff3 under input-dir/genome.")
    if not fastq_dir.exists():
        raise SystemExit(f"FASTQ directory not found: {fastq_dir}")

    tools = resolve_tools(args)
    missing = [name for name in ("hisat2", "hisat2_build", "samtools") if not tools[name]]
    if missing:
        raise SystemExit(
            "Missing required tools: "
            + ", ".join(missing)
            + ". Use --hisat2/--hisat2-build/--samtools or activate an environment containing them."
        )
    if args.mode == "rseqc" and not tools["infer_experiment"]:
        raise SystemExit("RSeQC infer_experiment.py not found. Install RSeQC or use --mode internal/auto.")

    pairs = discover_fastq_pairs(fastq_dir)
    if args.sample:
        selected = [pair for pair in pairs if pair.sample in set(args.sample)]
    else:
        selected = pairs[: args.max_samples]
    if not selected:
        raise SystemExit(f"No paired FASTQ files found under {fastq_dir}")

    log = args.outdir / "logs" / "strandness_check.log"
    args.outdir.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    bed12 = args.outdir / "annotation.bed12"
    tx_count = convert_annotation_to_bed12(Path(annotation), bed12)
    with log.open("a") as handle:
        handle.write(f"Converted transcripts to BED12: {tx_count}\n")

    prepared_genome = prepare_genome_for_hisat2(Path(genome), args.outdir, log)
    index_prefix = args.index_prefix or (args.outdir / "hisat2_index" / "genome")
    build_hisat2_index(str(tools["hisat2_build"]), prepared_genome, index_prefix, args.threads, log)

    results: list[dict[str, object]] = []
    for pair in selected:
        subset = subsample_fastq_pair(pair, args.outdir / "subsampled_fastq", reads=args.reads)
        bam = args.outdir / "bam" / f"{pair.sample}.sorted.bam"
        align_pair(str(tools["hisat2"]), str(tools["samtools"]), index_prefix, subset, bam, args.threads, log)
        if args.mode in {"auto", "rseqc"} and tools["infer_experiment"]:
            result = run_rseqc(str(tools["infer_experiment"]), bam, bed12, args.threshold, log)
        else:
            result = run_internal_infer(
                str(tools["samtools"]),
                bam,
                bed12,
                threshold=args.threshold,
                max_alignments=args.max_alignments,
                log=log,
            )
            if args.mode == "auto" and not tools["infer_experiment"]:
                result["warning"] = "RSeQC infer_experiment.py not found; used internal fallback."
        result["sample"] = pair.sample
        result["bam"] = str(bam)
        result["bed12"] = str(bed12)
        results.append(result)

    write_reports(args.outdir, results)
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check RNA-seq library strandness from a small HISAT2 alignment subset.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input-dir", type=Path, default=Path("."), help="Project directory containing genome/ and rnaseq/.")
    parser.add_argument("--genome", type=Path, help="Genome FASTA; auto-detected from input-dir/genome when omitted.")
    parser.add_argument("--annotation", type=Path, help="GTF/GFF3 annotation; auto-detected from input-dir/genome when omitted.")
    parser.add_argument("--fastq-dir", type=Path, help="Directory with paired FASTQ files; defaults to input-dir/rnaseq.")
    parser.add_argument("--outdir", type=Path, default=Path("strandness_check"), help="Output directory.")
    parser.add_argument("--sample", action="append", help="Sample name(s) to test; may be repeated. Defaults to first max-samples.")
    parser.add_argument("--max-samples", type=int, default=2, help="Number of samples to test when --sample is not given.")
    parser.add_argument("--reads", type=int, default=200_000, help="Read pairs to sample from each FASTQ pair.")
    parser.add_argument("--threads", type=int, default=4, help="Threads for HISAT2/samtools.")
    parser.add_argument("--index-prefix", type=Path, help="Existing or desired HISAT2 index prefix.")
    parser.add_argument("--mode", choices=["auto", "rseqc", "internal"], default="auto", help="Inference method.")
    parser.add_argument("--threshold", type=float, default=0.8, help="Dominant orientation fraction needed for stranded calls.")
    parser.add_argument("--max-alignments", type=int, default=200_000, help="Max alignments used by internal fallback.")
    parser.add_argument("--hisat2", help="Path to hisat2.")
    parser.add_argument("--hisat2-build", dest="hisat2_build", help="Path to hisat2-build.")
    parser.add_argument("--samtools", help="Path to samtools.")
    parser.add_argument("--infer-experiment", help="Path to RSeQC infer_experiment.py.")
    parser.add_argument("--simulate", action="store_true", help="Create and run a tiny simulated dataset.")
    parser.add_argument("--simulate-library", choices=["forward", "reverse"], default="reverse", help="Simulated library type.")
    parser.add_argument("--simulate-reads", type=int, default=40, help="Read pairs in simulated dataset.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results = run_pipeline(args)
    for result in results:
        print(
            f"{result.get('sample')}\t{result.get('classification')}\t"
            f"HISAT2={result.get('hisat2_rna_strandness') or 'none'}\t"
            f"featureCounts_s={result.get('featurecounts_s') or 'NA'}\t"
            f"method={result.get('method')}"
        )
    print(f"Reports written to: {args.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
