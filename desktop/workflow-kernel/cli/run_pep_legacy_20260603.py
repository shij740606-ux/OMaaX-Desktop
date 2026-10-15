#!/usr/bin/env python3
"""
PEP阶段：蛋白特征分析（完整版）

输入：
  - Identify阶段的蛋白序列文件 (final/{prefix}.final_members.pep.fa)

输出：
  - final/{prefix}.pep_properties.pdf/png (理化性质图)
  - final/{prefix}.pep_tmhmm.pdf/png (跨膜结构图)
  - final/{prefix}.pep_subcellular.pdf/png (亚细胞定位图)
  - final/{prefix}.pep_report.md (分析报告)
  - final/{prefix}.pep.manifest.json (机器清单)
"""

import os
import argparse
import json
import hashlib
import subprocess
import sys
import time
import shutil
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.gene_family_scope import read_scope, sanitize_token

CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")

# Plant-mPLoc code is retained, but the default PEP subcellular localization
# method is WoLF PSORT because it preserves per-protein predictions reliably.
PLANT_MPLOC_ENABLED = False


def load_manifest(manifest_file: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def count_fasta_records(fasta_file: Path) -> int:
    count = 0
    with open(fasta_file, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.startswith(">"):
                count += 1
    return count


def ensure_protein_fasta_without_stop_markers(protein_fasta: Path) -> Path:
    has_stop = False
    with open(protein_fasta, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.startswith(">") and "*" in line:
                has_stop = True
                break
    if not has_stop:
        return protein_fasta
    clean_name = re.sub(r"(\.fa(?:sta)?)$", r".clean\1", protein_fasta.name, flags=re.IGNORECASE)
    if clean_name == protein_fasta.name:
        clean_name = protein_fasta.name + ".clean.fa"
    clean_fasta = protein_fasta.with_name(clean_name)
    if clean_fasta.exists() and clean_fasta.stat().st_mtime >= protein_fasta.stat().st_mtime:
        return clean_fasta
    with open(protein_fasta, "r", encoding="utf-8", errors="ignore") as src, open(clean_fasta, "w", encoding="utf-8") as dst:
        for line in src:
            dst.write(line if line.startswith(">") else line.replace("*", ""))
    print(f"[INFO] Created stop-marker-clean FASTA for PEP: {clean_fasta}")
    return clean_fasta


def resolve_identify_protein_fasta_for_scope(workspace: Path, prefix: str, scope_data: Dict[str, Any]) -> Optional[Path]:
    scope_mode = str((scope_data or {}).get("scope_mode") or "").strip()
    if scope_mode not in {"", "target_only"}:
        return None
    final_dir = workspace / "final"
    family = str((scope_data or {}).get("family") or (prefix.split(".")[-1] if "." in prefix else prefix)).strip()
    targets = [str(x).strip() for x in (scope_data or {}).get("target_species") or [] if str(x).strip()]
    candidates = [final_dir / f"{prefix}.final_members.pep.fa"]
    if len(targets) == 1:
        species = targets[0]
        candidates = [
            workspace / "identify" / species / "final" / f"{species}.final_gfmembers.pep.fa",
            final_dir / f"Target.{species}.{family}.final_members.pep.fa",
            final_dir / f"{species}.{family}.final_members.pep.fa",
            *candidates,
        ]
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return ensure_protein_fasta_without_stop_markers(candidate)
    return None


def resolve_scope_protein_fasta(workspace: Path, prefix: str, scope_data: Dict[str, Any], cli_fasta: Optional[str] = None) -> Path:
    if cli_fasta:
        fasta_path = Path(cli_fasta)
        if fasta_path.exists() and fasta_path.stat().st_size > 0:
            return ensure_protein_fasta_without_stop_markers(fasta_path)
        raise FileNotFoundError(f"Specified protein FASTA not found: {cli_fasta}")

    final_dir = workspace / "final"
    scope_id = str(scope_data.get("scope_id") or "")
    manifest_candidates = []
    if scope_id:
        manifest_candidates.append(final_dir / f"{prefix}.{sanitize_token(scope_id)}.phylogeny.manifest.json")
    manifest_candidates.append(final_dir / f"{prefix}.phylogeny.manifest.json")

    for manifest_file in manifest_candidates:
        if not manifest_file.exists():
            continue
        manifest_data = load_manifest(manifest_file)
        if not manifest_data:
            continue
        fasta_text = manifest_data.get("parameters", {}).get("scope_input_fasta")
        if fasta_text:
            fasta_path = Path(fasta_text)
            if fasta_path.exists() and fasta_path.stat().st_size > 0:
                print(f"[INFO] Found scoped protein FASTA from phylogeny manifest: {fasta_path}")
                return ensure_protein_fasta_without_stop_markers(fasta_path)

    fallback = resolve_identify_protein_fasta_for_scope(workspace, prefix, scope_data)
    if fallback:
        print(f"[INFO] Using target-only Identify protein FASTA: {fallback}")
        return fallback

    raise FileNotFoundError(
        "Cannot find scoped protein FASTA. Run phylogeny with the same scope first, "
        "or provide --fasta explicitly."
    )


def scope_parameters(scope_file: Optional[Path], scope_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not scope_file or not scope_data:
        return {}
    return {
        "scope_file": str(scope_file),
        "scope_id": scope_data.get("scope_id", ""),
        "scope_mode": scope_data.get("scope_mode", ""),
        "target_species": scope_data.get("target_species", []),
        "reference_species": scope_data.get("reference_species", []),
        "comparative_species": scope_data.get("comparative_species", []),
        "selected_species": scope_data.get("selected_species", []),
        "family": scope_data.get("family", ""),
    }


def compute_file_hash(filepath: Path) -> str:
    """计算文件的SHA256哈希值"""
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return ""


def get_tool_versions() -> Dict[str, str]:
    """获取工具版本"""
    versions = {}
    
    try:
        result = subprocess.run(["R", "--version"], capture_output=True, text=True, check=False, timeout=5)
        versions["R"] = result.stdout.split('\n')[0] if result.stdout else "available"
    except Exception:
        versions["R"] = "not_found"
    
    try:
        biolib_bin = str(shutil.which("biolib") or "biolib")
        result = subprocess.run([biolib_bin, "--version"], capture_output=True, text=True, check=False, timeout=5)
        versions["biolib"] = "available" if result.returncode == 0 else "not_found"
    except Exception:
        versions["biolib"] = "not_found"
    
    return versions


def prepare_directories(workspace: Path) -> Dict[str, Path]:
    """创建目录"""
    paths = {
        "analysis_dir": workspace / "pep" / "analysis",
        "final_dir": workspace / "final",
        "logs_dir": workspace / "logs",
    }
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def parse_fasta(fasta_file: Path) -> Dict[str, str]:
    """解析FASTA文件"""
    sequences = {}
    current_id = None
    current_seq = []
    
    with open(fasta_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('>'):
                if current_id:
                    sequences[current_id] = ''.join(current_seq)
                current_id = line[1:].split()[0]
                current_seq = []
            else:
                current_seq.append(line)
        
        if current_id:
            sequences[current_id] = ''.join(current_seq)
    
    return sequences


def external_tool_timeout(fasta_file: Path, seconds_per_sequence: int = 90, minimum: int = 300, maximum: int = 7200) -> int:
    """Estimate a conservative timeout for slow web/cloud predictors."""
    try:
        count = sum(1 for line in open(fasta_file, 'r') if line.startswith('>'))
    except Exception:
        count = 1
    return max(minimum, min(maximum, count * seconds_per_sequence))


def write_clean_fasta(fasta_file: Path, output_file: Path) -> Path:
    """Write DeepTMHMM-friendly FASTA with short IDs and canonical amino acids."""
    allowed = set("ACDEFGHIKLMNPQRSTVWY")
    sequences = parse_fasta(fasta_file)
    with open(output_file, "w") as f:
        for seq_id, seq in sequences.items():
            clean = "".join(aa for aa in seq.upper() if aa in allowed)
            f.write(f">{seq_id}\n{clean}\n")
    return output_file


def run_deeptmhmm_dtu_web(fasta_file: Path, output_dir: Path, timeout_sec: int) -> Dict[str, Any]:
    """Run DeepTMHMM through the DTU web form when BioLib file staging fails."""
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    geckodriver = shutil.which("geckodriver")
    if not geckodriver:
        return {"success": False, "stderr": "geckodriver not found"}

    clean_fasta = write_clean_fasta(fasta_file, output_dir / f"{fasta_file.stem}.deeptmhmm.clean.fa")
    html_file = output_dir / "deeptmhmm_dtu.html"
    topologies = output_dir / "predicted_topologies.3line"
    gff_file = output_dir / "deeptmhmm_results.gff3"

    options = Options()
    options.add_argument("-headless")
    driver = webdriver.Firefox(service=Service(geckodriver), options=options)
    try:
        driver.get("https://services.healthtech.dtu.dk/services/DeepTMHMM-1.0/")
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.NAME, "uploadfile")))
        driver.find_element(By.NAME, "uploadfile").send_keys(str(clean_fasta.resolve()))
        driver.find_element(By.CSS_SELECTOR, 'input[type="submit"]').click()
        start = time.time()
        while time.time() - start < timeout_sec:
            body = driver.find_element(By.TAG_NAME, "body").text
            if "Predicted Topologies" in body or "Predicted topologies" in body:
                html_file.write_text(driver.page_source, encoding="utf-8")
                lines = body.splitlines()
                start_idx = None
                for i, line in enumerate(lines):
                    if line.strip() == "Predicted Topologies":
                        start_idx = i + 1
                        break
                if start_idx is None:
                    return {"success": False, "stderr": "Predicted Topologies section not found"}
                topo_lines = []
                gff_lines = []
                in_gff = False
                for line in lines[start_idx:]:
                    if line.startswith("##gff-version"):
                        in_gff = True
                    if line.startswith("DTU Health Tech") or line.startswith("ADDRESS & INFO"):
                        break
                    if in_gff:
                        if line.strip():
                            gff_lines.append(line)
                    else:
                        if line.strip():
                            topo_lines.append(line)
                topologies.write_text("\n".join(topo_lines).strip() + "\n", encoding="utf-8")
                gff_file.write_text("\n".join(gff_lines).strip() + "\n", encoding="utf-8")
                return {
                    "success": topologies.exists() and topologies.stat().st_size > 0,
                    "results_md": html_file,
                    "topologies": topologies,
                    "gff": gff_file,
                    "method": "DTU DeepTMHMM web",
                }
            if "failed" in body.lower() or "error" in body.lower():
                html_file.write_text(driver.page_source, encoding="utf-8")
                return {"success": False, "stderr": body[:500]}
            time.sleep(20)
        html_file.write_text(driver.page_source, encoding="utf-8")
        return {"success": False, "stderr": "DTU DeepTMHMM web job timed out"}
    finally:
        driver.quit()


def calculate_physicochemical_properties(sequences: Dict[str, str]) -> List[Dict]:
    """计算理化性质"""
    print(f"  计算理化性质...")
    
    # 氨基酸分子量
    aa_weights = {
        'A': 89.09, 'R': 174.20, 'N': 132.12, 'D': 133.10, 'C': 121.16,
        'E': 147.13, 'Q': 146.15, 'G': 75.07, 'H': 155.16, 'I': 131.17,
        'L': 131.17, 'K': 146.19, 'M': 149.21, 'F': 165.19, 'P': 115.13,
        'S': 105.09, 'T': 119.12, 'W': 204.23, 'Y': 181.19, 'V': 117.15
    }
    
    # 氨基酸电荷
    aa_charge = {
        'D': -1, 'E': -1, 'K': 1, 'R': 1, 'H': 0.5
    }
    
    properties = []
    
    for seq_id, sequence in sequences.items():
        seq = sequence.upper()
        length = len(seq)
        
        # 分子量
        mw = sum(aa_weights.get(aa, 110) for aa in seq) - (length - 1) * 18.02
        
        # 等电点估算
        acidic = seq.count('D') + seq.count('E')
        basic = seq.count('K') + seq.count('R') + seq.count('H')
        if basic > acidic:
            pI = 7.0 + (basic - acidic) * 0.5
        elif acidic > basic:
            pI = 7.0 - (acidic - basic) * 0.3
        else:
            pI = 7.0
        
        # 疏水性 (简化的GRAVY)
        hydrophobic_aa = 'AILMFWV'
        hydrophilic_aa = 'RNDQEK'
        hydrophobic_count = sum(seq.count(aa) for aa in hydrophobic_aa)
        hydrophilic_count = sum(seq.count(aa) for aa in hydrophilic_aa)
        gravy = (hydrophobic_count - hydrophilic_count) / length if length > 0 else 0
        
        # 脂肪族指数
        aliphatic = (seq.count('A') + seq.count('V') + seq.count('I') + seq.count('L')) / length * 100
        
        properties.append({
            "id": seq_id,
            "length": length,
            "molecular_weight": mw,
            "isoelectric_point": pI,
            "gravy": gravy,
            "aliphatic_index": aliphatic
        })
    
    return properties


def run_deeptmhmm(fasta_file: Path, output_dir: Path) -> Dict[str, Any]:
    """运行DeepTMHMM跨膜预测"""
    print(f"  运行DeepTMHMM...")
    
    results_md = output_dir / "deeptmhmm_results.md"
    topologies = output_dir / "predicted_topologies.3line"
    for old_file in (results_md, topologies):
        if old_file.exists():
            old_file.unlink()
    timeout_sec = external_tool_timeout(fasta_file, seconds_per_sequence=120, minimum=900, maximum=10800)

    web_result = run_deeptmhmm_dtu_web(fasta_file, output_dir, timeout_sec)
    if web_result.get("success"):
        return web_result
    print(f"    DTU网页DeepTMHMM失败，尝试BioLib: {web_result.get('stderr', '')[:200]}")
    
    try:
        biolib_bin = str(shutil.which("biolib") or "biolib")
        cmd = [biolib_bin, "run", "DTU/DeepTMHMM", "--fasta", str(fasta_file)]
        result = subprocess.run(cmd, capture_output=True, text=True, 
                               cwd=str(output_dir), timeout=timeout_sec)
        
        if result.returncode != 0 or not topologies.exists():
            stderr = result.stderr[:300] if result.stderr else "DeepTMHMM did not generate topology output"
            print(f"    DeepTMHMM未生成有效结果: {stderr}")
            print("    尝试通过DTU官方网页运行DeepTMHMM...")
            return run_deeptmhmm_dtu_web(fasta_file, output_dir, timeout_sec)
        
        return {
            "success": True,
            "results_md": results_md,
            "topologies": topologies
        }
        
    except Exception as e:
        print(f"    DeepTMHMM错误: {e}")
        return {"success": False, "stderr": str(e)}


def run_plant_mploc(fasta_file: Path, output_file: Path) -> Dict[str, Any]:
    """运行Plant-mPLoc"""
    print(f"  运行Plant-mPLoc...")
    
    try:
        script = Path("scripts/get_Plant_mPLoc_out.py")
        
        if script.exists():
            cmd = [sys.executable, str(script), "--input", str(fasta_file), "--output", str(output_file)]
            if output_file.exists():
                output_file.unlink()
            started_at = time.time()
            timeout_sec = external_tool_timeout(fasta_file, seconds_per_sequence=90)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
            if result.returncode != 0:
                return {"success": False, "stderr": result.stderr or result.stdout}
        else:
            return {"success": False, "stderr": f"Plant-mPLoc script not found: {script}"}
        
        if not output_file.exists() or output_file.stat().st_size == 0 or output_file.stat().st_mtime < started_at:
            return {"success": False, "stderr": "Plant-mPLoc did not generate output"}
        
        return {"success": True, "output_file": output_file, "method": "Plant-mPLoc website automation"}
        
    except Exception as e:
        print(f"    Plant-mPLoc错误: {e}")
        return {"success": False, "stderr": str(e)}


def run_wolfpsort(fasta_file: Path, output_file: Path) -> Dict[str, Any]:
    """运行WoLF PSORT"""
    print(f"  运行WoLF PSORT...")
    
    try:
        script = Path("scripts/wolfpsort_batch.py")
        
        if script.exists():
            cmd = [sys.executable, str(script), "--input", str(fasta_file), "--output", str(output_file)]
            if output_file.exists():
                output_file.unlink()
            started_at = time.time()
            timeout_sec = external_tool_timeout(fasta_file, seconds_per_sequence=90)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
            if result.returncode != 0:
                return {"success": False, "stderr": result.stderr or result.stdout}
        else:
            return {"success": False, "stderr": f"WoLF PSORT script not found: {script}"}
        
        if not output_file.exists() or output_file.stat().st_size == 0 or output_file.stat().st_mtime < started_at:
            return {"success": False, "stderr": "WoLF PSORT did not generate output"}
        
        return {"success": True, "output_file": output_file, "method": "WoLF PSORT website automation"}
        
    except Exception as e:
        print(f"    WoLF PSORT错误: {e}")
        return {"success": False, "stderr": str(e)}


def plot_properties_r(properties: List[Dict], output_pdf: Path, output_png: Path) -> bool:
    """使用R绘制理化性质图"""
    print(f"  绘制理化性质图...")
    
    # 准备数据文件
    data_file = output_pdf.parent / "properties_data.tsv"
    with open(data_file, 'w') as f:
        f.write("id\tlength\tmw\tpI\tgravy\taliphatic\n")
        for p in properties:
            f.write(f"{p['id']}\t{p['length']}\t{p['molecular_weight']:.2f}\t"
                   f"{p['isoelectric_point']:.2f}\t{p['gravy']:.3f}\t{p['aliphatic_index']:.2f}\n")
    
    r_script = f'''
library(ggplot2)
library(patchwork)

data <- read.delim("{data_file}", sep="\t", header=TRUE)

# 分子量分布
p1 <- ggplot(data, aes(x=mw/1000)) +
    geom_histogram(bins=15, fill="steelblue", color="white") +
    labs(title="Molecular Weight Distribution", 
         x="Molecular Weight (kDa)", y="Count") +
    theme_minimal()

# 等电点分布
p2 <- ggplot(data, aes(x=pI)) +
    geom_histogram(bins=15, fill="coral", color="white") +
    labs(title="Isoelectric Point Distribution", x="pI", y="Count") +
    theme_minimal()

# 序列长度分布
p3 <- ggplot(data, aes(x=length)) +
    geom_histogram(bins=15, fill="seagreen", color="white") +
    labs(title="Sequence Length Distribution", x="Length (aa)", y="Count") +
    theme_minimal()

# 疏水性分布
p4 <- ggplot(data, aes(x=gravy)) +
    geom_histogram(bins=15, fill="purple", color="white") +
    labs(title="GRAVY (Hydrophobicity)", x="GRAVY Score", y="Count") +
    theme_minimal()

combined <- (p1 + p2) / (p3 + p4)

ggsave("{output_pdf}", combined, width=12, height=10)

png("{output_png}", width=1200, height=1000, res=150)
print(combined)
dev.off()
'''
    
    r_file = output_pdf.parent / "plot_properties.R"
    with open(r_file, 'w') as f:
        f.write(r_script)
    
    try:
        rscript = str(shutil.which("Rscript") or "Rscript")
        result = subprocess.run([rscript, str(r_file)], 
                               capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"    R绘图错误: {result.stderr[:300]}")
        return output_pdf.exists() and output_png.exists()
    except Exception as e:
        print(f"    R绘图错误: {e}")
        return False


def plot_tmhmm_results(topologies_file: Path, output_pdf: Path, output_png: Path) -> bool:
    """绘制跨膜结构图"""
    print(f"  绘制跨膜结构图...")
    
    # 解析拓扑结构
    transmembrane_count = {}
    
    if topologies_file.exists():
        with open(topologies_file, 'r') as f:
            current_id = None
            for line in f:
                line = line.strip()
                if line.startswith('>'):
                    current_id = line[1:]
                elif current_id and line and not line.startswith('>'):
                    # 统计M（跨膜）的数量
                    tm_count = line.count('M')
                    transmembrane_count[current_id] = tm_count
                    current_id = None
    
    # 准备数据
    data_file = output_pdf.parent / "tmhmm_data.tsv"
    with open(data_file, 'w') as f:
        f.write("id\ttm_count\n")
        for seq_id, count in transmembrane_count.items():
            f.write(f"{seq_id}\t{count}\n")
    
    r_script = f'''
library(ggplot2)

data <- read.delim("{data_file}", sep="\t", header=TRUE)

# 跨膜螺旋数量分布
p <- ggplot(data, aes(x=tm_count)) +
    geom_bar(fill="darkblue", color="white") +
    labs(title="Transmembrane Helices Distribution", 
         x="Number of TM Helices", y="Count") +
    theme_minimal()

ggsave("{output_pdf}", p, width=8, height=6)

png("{output_png}", width=800, height=600, res=150)
print(p)
dev.off()
'''
    
    r_file = output_pdf.parent / "plot_tmhmm.R"
    with open(r_file, 'w') as f:
        f.write(r_script)
    
    try:
        rscript = str(shutil.which("Rscript") or "Rscript")
        result = subprocess.run([rscript, str(r_file)], 
                               capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"    R绘图错误: {result.stderr[:300]}")
        return output_pdf.exists() and output_png.exists()
    except Exception as e:
        print(f"    R绘图错误: {e}")
        return False



LOCATION_LABELS = {
    "nucl": "Nucleus",
    "nucleus": "Nucleus",
    "cyto": "Cytoplasm",
    "cytoplasm": "Cytoplasm",
    "chlo": "Chloroplast",
    "chloroplast": "Chloroplast",
    "mito": "Mitochondrion",
    "mitochondrion": "Mitochondrion",
    "plas": "Plasma membrane",
    "plasma membrane": "Plasma membrane",
    "vacu": "Vacuole",
    "vacuole": "Vacuole",
    "golg": "Golgi apparatus",
    "golgi": "Golgi apparatus",
    "extr": "Extracellular",
    "extracellular": "Extracellular",
    "pero": "Peroxisome",
    "peroxisome": "Peroxisome",
    "e.r.": "Endoplasmic reticulum",
    "er": "Endoplasmic reticulum",
    "cysk": "Cytoskeleton",
    "cytoskeleton": "Cytoskeleton",
}


def normalize_location_label(label: str) -> str:
    clean = re.sub(r"\s+", " ", str(label or "").strip())
    if not clean:
        return ""
    key = clean.lower().replace("_", " ")
    if key in LOCATION_LABELS:
        return LOCATION_LABELS[key]
    if " " not in clean and "_" in clean:
        parts = [normalize_location_label(part) for part in clean.split("_")]
        parts = [part for part in parts if part]
        if parts:
            return " / ".join(parts)
    return clean[:1].upper() + clean[1:]


def parse_plant_mploc_counts(plant_mploc_file: Path) -> tuple[Dict[str, int], int]:
    counts: Dict[str, int] = {}
    rows = 0
    if not plant_mploc_file.exists() or plant_mploc_file.stat().st_size == 0:
        return counts, rows
    with open(plant_mploc_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            first = parts[0].strip().lower()
            second = parts[1].strip()
            if first in {"protein_id", "protein", "id", "gene_id", "sequence_id"}:
                continue
            loc = normalize_location_label(second)
            if not loc:
                continue
            counts[loc] = counts.get(loc, 0) + 1
            rows += 1
    return counts, rows


def parse_wolfpsort_counts(wolfpsort_file: Path) -> tuple[Dict[str, int], int]:
    counts: Dict[str, int] = {}
    rows = 0
    if not wolfpsort_file.exists() or wolfpsort_file.stat().st_size == 0:
        return counts, rows
    with open(wolfpsort_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2 or parts[0].strip().lower() in {"protein_id", "protein", "id"}:
                continue
            prediction = parts[1].strip()
            if not prediction:
                continue
            primary = prediction.split(",", 1)[0]
            primary = primary.split(":", 1)[0].strip()
            loc = normalize_location_label(primary)
            if not loc:
                continue
            counts[loc] = counts.get(loc, 0) + 1
            rows += 1
    return counts, rows


def resolve_subcellular_counts(plant_mploc_file: Path, wolfpsort_file: Path,
                               allow_plant_fallback: bool = False) -> tuple[Dict[str, int], str, int]:
    wolf_counts, wolf_rows = parse_wolfpsort_counts(wolfpsort_file)
    if wolf_rows:
        return wolf_counts, "WoLF PSORT", wolf_rows

    if allow_plant_fallback:
        plant_counts, plant_rows = parse_plant_mploc_counts(plant_mploc_file)
        if plant_rows:
            return plant_counts, "Plant-mPLoc", plant_rows

    return {}, "WoLF PSORT", 0

def plot_subcellular_results(plant_mploc_file: Path, wolfpsort_file: Path,
                             output_pdf: Path, output_png: Path) -> bool:
    """绘制亚细胞定位图"""
    print(f"  绘制亚细胞定位图...")

    location_counts, source, parsed_rows = resolve_subcellular_counts(plant_mploc_file, wolfpsort_file)
    if not location_counts:
        print("    亚细胞定位结果为空，跳过绘图")
        return False

    # 准备数据
    data_file = output_pdf.parent / "subcellular_data.tsv"
    with open(data_file, 'w') as f:
        f.write("location\tcount\tsource\n")
        for loc, count in sorted(location_counts.items(), key=lambda item: item[1], reverse=True):
            f.write(f"{loc}\t{count}\t{source}\n")

    r_script = f'''
library(ggplot2)

data <- read.delim("{data_file}", sep="\t", header=TRUE)

# 按count排序
data$location <- factor(data$location, levels=data$location[order(data$count, decreasing=TRUE)])
plot_source <- unique(data$source)[1]

p <- ggplot(data, aes(x=location, y=count, fill=location)) +
    geom_bar(stat="identity", width=0.55, color="white") +
    geom_text(aes(label=count), vjust=-0.3, size=4) +
    labs(title=paste0("Subcellular Localization (", plot_source, ")"),
         x="Location", y="Count") +
    theme_minimal() +
    theme(axis.text.x=element_text(angle=45, hjust=1), legend.position="none")

ggsave("{output_pdf}", p, width=8, height=6)

png("{output_png}", width=800, height=600, res=150)
print(p)
dev.off()
'''

    r_file = output_pdf.parent / "plot_subcellular.R"
    with open(r_file, 'w') as f:
        f.write(r_script)

    try:
        rscript = str(shutil.which("Rscript") or "Rscript")
        result = subprocess.run([rscript, str(r_file)],
                               capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"    R绘图错误: {result.stderr[:300]}")
        return output_pdf.exists() and output_png.exists()
    except Exception as e:
        print(f"    R绘图错误: {e}")
        return False

def write_report(workspace: Path, prefix: str, metrics: Dict, parameters: Dict,
                 tool_versions: Dict) -> Path:
    """写入报告"""
    report_path = workspace / "final" / f"{prefix}.pep_report.md"
    
    with open(report_path, "w") as f:
        f.write(f"# 蛋白特征分析报告 - {prefix}\n\n")
        f.write(f"**生成时间**: {datetime.now().isoformat()}\n\n")
        
        f.write("## 1. 分析概述\n")
        f.write("- **分析阶段**: PEP (Protein Features)\n")
        f.write("- **分析内容**: \n")
        f.write("  - 理化性质 (分子量、等电点、疏水性)\n")
        f.write("  - 跨膜结构预测 (DeepTMHMM)\n")
        f.write("  - 亚细胞定位 (WoLF PSORT；Plant-mPLoc代码保留但默认关闭)\n\n")
        
        if "protein_count" in metrics:
            f.write(f"- **蛋白数量**: {metrics['protein_count']}\n\n")

        subcellular_data = workspace / "final" / "subcellular_data.tsv"
        subcellular_rows = []
        if subcellular_data.exists() and subcellular_data.stat().st_size > 0:
            with open(subcellular_data, "r", encoding="utf-8", errors="ignore") as sf:
                header = sf.readline().strip().split("\t")
                for line in sf:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) < 2:
                        continue
                    row = dict(zip(header, parts))
                    subcellular_rows.append(row)
        if subcellular_rows:
            f.write("## 2. 亚细胞定位数量\n")
            f.write("| Location | Count | Source |\n")
            f.write("|---|---:|---|\n")
            for row in subcellular_rows:
                f.write(
                    f"| {row.get('location', '')} | {row.get('count', '')} | "
                    f"{row.get('source', '')} |\n"
                )
            f.write("\n")
        
        f.write("## 3. 工具版本\n")
        for tool, version in tool_versions.items():
            f.write(f"- **{tool}**: {version}\n")
        f.write("\n")
        
        f.write("## 4. 输出文件\n")
        f.write(f"- `{prefix}.pep_properties.pdf/png` - 理化性质分布图\n")
        f.write(f"- `{prefix}.pep_tmhmm.pdf/png` - 跨膜结构预测图\n")
        f.write(f"- `{prefix}.pep_subcellular.pdf/png` - 亚细胞定位图\n")
        f.write("\n")
    
    return report_path


def write_manifest(workspace: Path, job_id: str, run_id: str, prefix: str,
                  start_time: datetime, end_time: datetime, artifacts: Dict,
                  metrics: Dict, parameters: Dict, tool_versions: Dict) -> Path:
    """写入manifest"""
    elapsed_sec = (end_time - start_time).total_seconds()
    
    manifest_data = {
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "pep",
        "run_id": run_id,
        "prefix": prefix,
        "timestamp": end_time.isoformat(),
        "status": "success",
        "elapsed_sec": round(elapsed_sec, 2),
        "tool_versions": tool_versions,
        "artifacts": artifacts,
        "parameters": parameters,
        "metrics": metrics
    }
    
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    for key in (
        "scope_id",
        "scope_mode",
        "scope_file",
        "target_species",
        "reference_species",
        "comparative_species",
        "selected_species",
        "family",
    ):
        if key in parameters:
            manifest_data[key] = parameters[key]

    manifest_path = final_dir / f"{prefix}.pep.manifest.json"
    with open(manifest_path, "w", encoding='utf-8') as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    if parameters.get("scope_id"):
        scoped_manifest = final_dir / f"{prefix}.{sanitize_token(str(parameters['scope_id']))}.pep.manifest.json"
        manifest_data["scope_manifest"] = str(scoped_manifest.resolve())
        with open(scoped_manifest, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2, ensure_ascii=False)
    
    return manifest_path


def main():
    parser = argparse.ArgumentParser(description="蛋白特征分析")
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--input-dir")
    parser.add_argument("--fasta", help="蛋白FASTA文件；用于显式指定PEP输入")
    parser.add_argument("--scope-file", help="target/reference/comparative scope JSON")
    parser.add_argument("--cpu", type=int, default=8)
    parser.add_argument("--log-file", help="日志文件路径（用于前端兼容）")
    args = parser.parse_args()
    
    start_time = datetime.now()
    run_id = f"pep_{start_time.strftime('%Y%m%d_%H%M%S')}"
    
    workspace = Path(args.workspace)
    job_id = workspace.name
    
    print("=" * 60)
    print(f"🚀 蛋白特征分析")
    print("=" * 60)
    
    try:
        scope_file = Path(args.scope_file) if args.scope_file else None
        scope_data = None
        if scope_file:
            if not scope_file.exists():
                raise FileNotFoundError(f"Scope file not found: {scope_file}")
            scope_data = read_scope(scope_file)

        # 1. 准备目录
        print("\n📁 准备目录...")
        paths = prepare_directories(workspace)
        
        # 2. 获取工具版本
        print("\n🔧 检查工具...")
        tool_versions = get_tool_versions()
        
        # 3. 查找输入文件
        if scope_data:
            protein_file = resolve_scope_protein_fasta(workspace, args.prefix, scope_data, args.fasta)
        elif args.fasta:
            protein_file = Path(args.fasta)
            if not protein_file.exists() or protein_file.stat().st_size == 0:
                print(f"❌ 找不到蛋白文件: {protein_file}")
                return 1
            protein_file = ensure_protein_fasta_without_stop_markers(protein_file)
        else:
            protein_file = workspace / "final" / f"{args.prefix}.final_members.pep.fa"
            if not protein_file.exists() or protein_file.stat().st_size == 0:
                print(f"❌ 找不到蛋白文件: {protein_file}")
                return 1
            protein_file = ensure_protein_fasta_without_stop_markers(protein_file)
        
        print(f"  蛋白文件: {protein_file}")
        
        # 解析序列
        sequences = parse_fasta(protein_file)
        print(f"  包含 {len(sequences)} 个序列")
        
        artifacts = {}
        metrics = {"protein_count": len(sequences)}
        
        # 4. 理化性质分析
        print("\n🧮 理化性质分析...")
        properties = calculate_physicochemical_properties(sequences)
        
        # 绘制理化性质图
        props_pdf = paths["final_dir"] / f"{args.prefix}.pep_properties.pdf"
        props_png = paths["final_dir"] / f"{args.prefix}.pep_properties.png"
        
        if plot_properties_r(properties, props_pdf, props_png):
            print(f"    ✓ 理化性质图: {props_pdf}, {props_png}")
            artifacts["properties_pdf"] = {"path": f"final/{args.prefix}.pep_properties.pdf"}
            artifacts["properties_png"] = {"path": f"final/{args.prefix}.pep_properties.png"}
        
        # 5. 跨膜结构预测
        print("\n🌊 跨膜结构预测 (DeepTMHMM)...")
        tmhmm_result = run_deeptmhmm(protein_file, paths["analysis_dir"])
        
        metrics["tmhmm_success"] = bool(tmhmm_result.get("success"))
        if not tmhmm_result.get("success"):
            metrics["tmhmm_error"] = str(tmhmm_result.get("stderr") or tmhmm_result.get("error") or "DeepTMHMM unavailable")[:500]
        if tmhmm_result["success"]:
            # 绘制跨膜结构图
            tmhmm_pdf = paths["final_dir"] / f"{args.prefix}.pep_tmhmm.pdf"
            tmhmm_png = paths["final_dir"] / f"{args.prefix}.pep_tmhmm.png"
            
            if plot_tmhmm_results(tmhmm_result["topologies"], tmhmm_pdf, tmhmm_png):
                print(f"    ✓ 跨膜结构图: {tmhmm_pdf}, {tmhmm_png}")
                artifacts["tmhmm_pdf"] = {"path": f"final/{args.prefix}.pep_tmhmm.pdf"}
                artifacts["tmhmm_png"] = {"path": f"final/{args.prefix}.pep_tmhmm.png"}
        
        # 6. 亚细胞定位预测
        print("\n🎯 亚细胞定位预测...")
        
        # Plant-mPLoc code is retained but disabled by default.
        plant_mploc_file = paths["analysis_dir"] / f"{args.prefix}_plant_mploc.txt"
        if PLANT_MPLOC_ENABLED:
            plant_result = run_plant_mploc(protein_file, plant_mploc_file)
        else:
            print("  Plant-mPLoc已关闭；亚细胞定位默认使用WoLF PSORT")
            plant_result = {
                "success": False,
                "disabled": True,
                "method": "Plant-mPLoc website automation",
                "stderr": "Plant-mPLoc disabled; WoLF PSORT is the configured subcellular localization method",
            }
        
        # WoLF PSORT
        wolfpsort_file = paths["analysis_dir"] / f"{args.prefix}_wolfpsort.txt"
        wolf_result = run_wolfpsort(protein_file, wolfpsort_file)
        metrics["subcellular_method"] = "WoLF PSORT"
        metrics["plant_mploc_enabled"] = PLANT_MPLOC_ENABLED
        metrics["plant_mploc_success"] = bool(plant_result.get("success"))
        metrics["wolfpsort_success"] = bool(wolf_result.get("success"))
        if plant_result.get("disabled"):
            metrics["plant_mploc_status"] = "disabled"
        elif not plant_result.get("success"):
            metrics["plant_mploc_error"] = str(plant_result.get("stderr") or plant_result.get("error") or "Plant-mPLoc unavailable")[:500]
        if not wolf_result.get("success"):
            metrics["wolfpsort_error"] = str(wolf_result.get("stderr") or wolf_result.get("error") or "WoLF PSORT unavailable")[:500]
        
        # 绘制亚细胞定位图；默认只接受WoLF PSORT逐蛋白预测作为数量来源。
        if wolf_result["success"]:
            subcell_pdf = paths["final_dir"] / f"{args.prefix}.pep_subcellular.pdf"
            subcell_png = paths["final_dir"] / f"{args.prefix}.pep_subcellular.png"
            
            if plot_subcellular_results(plant_mploc_file, wolfpsort_file, subcell_pdf, subcell_png):
                sub_counts, sub_source, sub_rows = resolve_subcellular_counts(plant_mploc_file, wolfpsort_file)
                metrics["subcellular_source"] = sub_source
                metrics["subcellular_prediction_count"] = sub_rows
                metrics["subcellular_counts"] = sub_counts
                print(f"    ✓ 亚细胞定位图: {subcell_pdf}, {subcell_png}")
                artifacts["subcellular_pdf"] = {"path": f"final/{args.prefix}.pep_subcellular.pdf"}
                artifacts["subcellular_png"] = {"path": f"final/{args.prefix}.pep_subcellular.png"}
                artifacts["subcellular_data"] = {"path": "final/subcellular_data.tsv"}
        
        # 7. 生成报告
        end_time = datetime.now()
        
        print("\n📝 生成报告...")
        parameters = {
            "prefix": args.prefix,
            "cpu": args.cpu,
            "protein_fasta": str(protein_file),
            "protein_count": count_fasta_records(protein_file),
        }
        parameters.update(scope_parameters(scope_file, scope_data))
        
        report_path = write_report(workspace, args.prefix, metrics, parameters, tool_versions)
        artifacts["report"] = {"path": f"final/{args.prefix}.pep_report.md"}
        
        # 8. 生成manifest
        print("\n📋 生成Manifest...")
        manifest_path = write_manifest(
            workspace, job_id, run_id, args.prefix,
            start_time, end_time, artifacts, metrics, parameters, tool_versions
        )
        
        print("\n" + "=" * 60)
        print(f"✅ 蛋白特征分析完成!")
        print("=" * 60)
        
        return 0
        
    except Exception as e:
        print(f"\n❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
