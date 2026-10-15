#!/usr/bin/env python3
"""
Genome Prepare V2: 多物种基因组数据预处理
支持批量处理多个物种的基因组数据

输入：input/job_id/ 下的多个物种原始基因组数据
输出：
  - input/job_id/{species}.cds.fasta
  - input/job_id/{species}.pep.fasta
  - input/job_id/{species}.genome.fasta
  - input/job_id/{species}_final.gff3
  - input/job_id/genome_prepare.manifest.json

多物种命名规则：
  - 文件名应包含物种标识，如：
    - Athaliana_167_TAIR10.protein_primaryTranscriptOnly.fa.gz (拟南芥)
    - FtChromosomeV2.fasta.gz (苦荞)
    - Osativa_323_v7.protein_primaryTranscriptOnly.fa.gz (水稻)
"""

import os
import argparse
import json
import hashlib
import logging
import re
import subprocess
import sys
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
# 添加conda环境bin目录到PATHCONDA_BIN = "/home/xia-lab/miniconda3/envs/test4py311/bin"if CONDA_BIN not in os.environ.get("PATH", ""):    os.environ["PATH"] = CONDA_BIN + ":" + os.environ.get("PATH", "")
class SpeciesFiles:
    """表示一个物种的文件集合"""
    species_code: str
    cds: Optional[Path] = None
    pep: Optional[Path] = None
    gff: Optional[Path] = None
    genome: Optional[Path] = None
    db_type: str = "unknown"
    
    def is_valid(self) -> bool:
        """检查是否有足够的文件进行分析"""
        return self.pep is not None and self.gff is not None
    
    def __str__(self) -> str:
        files = []
        if self.cds: files.append("CDS")
        if self.pep: files.append("PEP")
        if self.gff: files.append("GFF")
        if self.genome: files.append("GENOME")
        return f"{self.species_code}({','.join(files)})"


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
    """获取工具版本信息"""
    versions = {}
    versions["python"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    
    try:
        result = subprocess.run(['perl', '--version'], capture_output=True, text=True, check=False)
        output = result.stdout or result.stderr
        if output:
            lines = output.split('\n')
            for line in lines:
                if 'perl' in line.lower() and 'v' in line:
                    versions["perl"] = line.strip()
                    break
            if "perl" not in versions:
                versions["perl"] = lines[0] if lines else "unknown"
    except:
        versions["perl"] = "not_found"
    
    try:
        subprocess.run(['seqtk'], capture_output=True, check=False)
        versions["seqtk"] = "available"
    except:
        versions["seqtk"] = "not_found"
    
    try:
        result = subprocess.run(['gffread', '--version'], capture_output=True, text=True, check=False)
        versions["gffread"] = (result.stdout.strip() or result.stderr.strip() or "unknown").split('\n')[0]
    except:
        versions["gffread"] = "not_found"
    
    return versions


class MultiSpeciesDetector:
    """多物种文件检测器"""
    
    # 物种代码映射
    SPECIES_PATTERNS = {
        # 模式: (物种代码, 物种名称)
        r'^[Aa]th.*TAIR': ('Ath', 'Arabidopsis thaliana'),
        r'^[Ff]t.*Chromosome': ('Ft', 'Fagopyrum tataricum'),
        r'^[Oo]s.*': ('Os', 'Oryza sativa'),
        r'^[Zz]m.*': ('Zm', 'Zea mays'),
        r'^[Ss]l.*': ('Sl', 'Solanum lycopersicum'),
        r'^[Gg]m.*': ('Gm', 'Glycine max'),
        r'^[Bb]d.*': ('Bd', 'Brachypodium distachyon'),
        r'^[Hh]v.*': ('Hv', 'Hordeum vulgare'),
        r'^[Ss]b.*': ('Sb', 'Sorghum bicolor'),
        r'^[Pp]t.*': ('Pt', 'Populus trichocarpa'),
        r'^[Vv].*': ('Vv', 'Vitis vinifera'),
        r'^[Cc]r.*': ('Cr', 'Chlamydomonas reinhardtii'),
    }
    
    @classmethod
    def detect_species(cls, filename: str) -> Tuple[str, str]:
        """从文件名检测物种代码"""
        for pattern, (code, name) in cls.SPECIES_PATTERNS.items():
            if re.match(pattern, filename):
                return code, name
        
        # 默认：提取前两个大写字母作为物种代码
        match = re.match(r'^([A-Z][a-z])', filename)
        if match:
            return match.group(1), f"Unknown ({filename[:10]}...)"
        
        return "UNK", "Unknown"
    
    @classmethod
    def detect_db_type(cls, filename: str) -> str:
        """检测数据库类型"""
        name_lower = filename.lower()
        
        # JGI特征
        if 'primarytranscriptonly' in name_lower or '_tair' in name_lower:
            return "jgi"
        
        # Ensembl特征
        if '.cds.all.fa' in name_lower or '.pep.all.fa' in name_lower:
            return "ensembl"
        
        # NCBI特征
        if 'gcf_' in name_lower or '_cds_from_genomic' in name_lower:
            return "ncbi"
        
        return "unknown"
    
    @classmethod
    def scan_input_dir(cls, input_dir: Path) -> Dict[str, SpeciesFiles]:
        """扫描输入目录，识别所有物种"""
        species_map: Dict[str, SpeciesFiles] = {}
        
        for file in input_dir.iterdir():
            if not file.is_file():
                continue
            
            name = file.name
            name_lower = name.lower()
            
            # 跳过已生成的文件
            if '_final.gff3' in name or name.startswith('Ft.SPL'):
                continue
            
            # 检测物种
            species_code, species_name = cls.detect_species(name)
            
            if species_code not in species_map:
                species_map[species_code] = SpeciesFiles(
                    species_code=species_code,
                    db_type=cls.detect_db_type(name)
                )
            
            # 分类文件 - 更灵活的模式匹配
            # CDS文件
            if ('cds' in name_lower or 'cdna' in name_lower) and ('.fa' in name_lower or '.fasta' in name_lower):
                species_map[species_code].cds = file
            # Protein/PEP文件
            elif ('protein' in name_lower or 'pep' in name_lower or name_lower.endswith('.faa') or name_lower.endswith('.faa.gz')) and ('.fa' in name_lower or '.fasta' in name_lower):
                species_map[species_code].pep = file
            # GFF文件 - 更宽松的条件
            elif name_lower.endswith('.gff3') or name_lower.endswith('.gff') or '.gff3.' in name_lower or '.gff.' in name_lower:
                # 排除简单的 genome.gff 命名
                if not ('genome' in name_lower and 'gene' not in name_lower and 'exon' not in name_lower and 'coreset' not in name_lower):
                    species_map[species_code].gff = file
                else:
                    # Coreset GFF 也接受
                    species_map[species_code].gff = file
            # Genome文件
            elif ('genome' in name_lower or 'chromosome' in name_lower) and ('.fa' in name_lower or '.fasta' in name_lower):
                species_map[species_code].genome = file
        
        return species_map


class MultiSpeciesGenomePreparer:
    """多物种基因组预处理器"""
    
    def __init__(self, input_dir: Path, output_dir: Path, scripts_dir: Path):
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.scripts_dir = scripts_dir
        self.work_dir = output_dir / "genome_prepare"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        
        self.cmd_history = []
        self.results = []
    
    def run_command(self, cmd: List[str], description: str = "") -> Tuple[int, str, str]:
        """运行命令并记录"""
        cmd_str = ' '.join(str(c) for c in cmd)
        self.cmd_history.append(cmd_str)
        logger.info(f"Running: {description or cmd_str}")
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=self.work_dir, check=False)
            return result.returncode, result.stdout, result.stderr
        except Exception as e:
            logger.error(f"Command failed: {e}")
            return -1, "", str(e)
    
    def decompress_if_needed(self, filepath: Path) -> Path:
        """如果文件是.gz格式，解压并返回解压后的路径"""
        if filepath.suffix == '.gz':
            decompressed_path = self.work_dir / filepath.stem
            logger.info(f"Decompressing {filepath.name}...")
            
            # 使用pigz或gzip解压
            try:
                result = subprocess.run(
                    ['pigz', '-d', '-c', str(filepath)],
                    capture_output=True,
                    text=False,
                    check=False
                )
                if result.returncode == 0:
                    with open(decompressed_path, 'wb') as f:
                        f.write(result.stdout)
                    logger.info(f"Decompressed to {decompressed_path.name}")
                    return decompressed_path
            except FileNotFoundError:
                pass
            
            # 使用gzip解压
            result = subprocess.run(
                ['gzip', '-d', '-c', str(filepath)],
                capture_output=True,
                text=False,
                check=False
            )
            if result.returncode == 0:
                with open(decompressed_path, 'wb') as f:
                    f.write(result.stdout)
                logger.info(f"Decompressed to {decompressed_path.name}")
                return decompressed_path
        
        return filepath
    
    def process_species(self, species: SpeciesFiles) -> Dict[str, Any]:
        """处理单个物种"""
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing species: {species.species_code}")
        logger.info(f"{'='*60}")
        
        if not species.is_valid():
            return {
                "species": species.species_code,
                "success": False,
                "error": f"Insufficient files: {species}"
            }
        
        # 设置输出路径
        cds_out = self.output_dir / f"{species.species_code}.cds.fasta"
        pep_out = self.output_dir / f"{species.species_code}.pep.fasta"
        genome_out = self.output_dir / f"{species.species_code}.genome.fasta"
        gff_out = self.output_dir / f"{species.species_code}_final.gff3"
        
        try:
            # 解压文件
            gff_file = self.decompress_if_needed(species.gff)
            genome_file = self.decompress_if_needed(species.genome) if species.genome else None
            cds_file = self.decompress_if_needed(species.cds) if species.cds else None
            pep_file = self.decompress_if_needed(species.pep) if species.pep else None
            
            # 如果没有PEP文件，但有GENOME+GFF，尝试用gffread提取
            if not pep_file and genome_file and gff_file:
                logger.info(f"No protein file found, attempting to extract from genome+GFF using gffread...")
                pep_temp = self.work_dir / f"{species.species_code}_extracted.pep.fa"
                cmd = ['gffread', '-y', str(pep_temp), '-g', str(genome_file), str(gff_file)]
                rc, stdout, stderr = self.run_command(cmd, f"Extract protein from genome for {species.species_code}")
                
                if rc == 0 and pep_temp.exists() and pep_temp.stat().st_size > 0:
                    logger.info(f"✅ Successfully extracted protein sequences to {pep_temp.name}")
                    pep_file = pep_temp
                else:
                    logger.warning(f"⚠️ Failed to extract protein, will skip {species.species_code}")
                    return {
                        "species": species.species_code,
                        "success": False,
                        "error": "No protein file and failed to extract from genome+GFF"
                    }
            
            # 提取基因ID
            mrna_id_file = self.work_dir / f"{species.species_code}_mRNA.id"
            id_source = cds_file if cds_file else pep_file
            
            with open(id_source, 'r') as f:
                mrna_ids = [line[1:].split()[0] for line in f if line.startswith('>')]
            
            with open(mrna_id_file, 'w') as f:
                for mrna_id in mrna_ids:
                    f.write(f"{mrna_id}\n")
            
            logger.info(f"Extracted {len(mrna_ids)} gene IDs from {id_source.name}")
            
            # 处理GFF
            gff_tmp = self.work_dir / f"{species.species_code}.gff3.tmp"
            with open(gff_file, 'r') as infile, open(gff_tmp, 'w') as outfile:
                for line in infile:
                    # 去除TAIR版本号
                    line = re.sub(r'\.TAIR\d+', '', line)
                    # 去除Ensembl前缀
                    line = re.sub(r'ID=(gene|transcript|cds):', 'ID=', line, flags=re.IGNORECASE)
                    line = re.sub(r'Parent=(gene|transcript|cds):', 'Parent=', line, flags=re.IGNORECASE)
                    outfile.write(line)
            
            # 过滤GFF
            gene_table = self.work_dir / f"{species.species_code}_geneID_mrnaID.table"
            cmd = ['perl', str(self.scripts_dir / 'gff_filter_bymRNAID.pl'),
                   str(gff_tmp.resolve()), str(mrna_id_file.resolve()), 
                   str(gene_table.resolve()), str(gff_out.resolve())]
            rc, _, stderr = self.run_command(cmd, f"Filter GFF for {species.species_code}")
            
            if rc != 0:
                raise RuntimeError(f"GFF filtering failed: {stderr}")
            
            # 复制序列文件
            if cds_file:
                shutil.copy2(cds_file, cds_out)
            else:
                shutil.copy2(pep_file, cds_out)  # 占位
            
            shutil.copy2(pep_file, pep_out)
            
            if genome_file:
                shutil.copy2(genome_file, genome_out)
            
            logger.info(f"✅ {species.species_code} processing complete")
            
            return {
                "species": species.species_code,
                "success": True,
                "db_type": species.db_type,
                "gene_count": len(mrna_ids),
                "files": {
                    "cds": str(cds_out),
                    "pep": str(pep_out),
                    "gff": str(gff_out),
                    "genome": str(genome_out) if genome_file else None
                }
            }
            
        except Exception as e:
            logger.error(f"❌ {species.species_code} processing failed: {e}")
            return {
                "species": species.species_code,
                "success": False,
                "error": str(e)
            }
    
    def process_all(self) -> List[Dict[str, Any]]:
        """处理所有物种"""
        # 扫描输入目录
        species_map = MultiSpeciesDetector.scan_input_dir(self.input_dir)
        
        logger.info(f"\nDetected {len(species_map)} species:")
        for code, species in species_map.items():
            logger.info(f"  - {species}")
        
        # 处理每个物种
        results = []
        for species in species_map.values():
            result = self.process_species(species)
            results.append(result)
        
        return results


def write_manifest(
    output_dir: Path,
    prefix: str,
    results: List[Dict[str, Any]],
    tool_versions: Dict[str, str],
    cmd_history: List[str],
    start_time: datetime
) -> Path:
    """写入manifest文件"""
    
    success_count = sum(1 for r in results if r.get("success"))
    total_count = len(results)
    
    manifest = {
        "manifest_version": "1.0",
        "stage": "genome_prepare_v2",
        "prefix": prefix,
        "timestamp": datetime.now().isoformat(),
        "status": "success" if success_count > 0 else "failed",
        "elapsed_sec": (datetime.now() - start_time).total_seconds(),
        "tool_versions": tool_versions,
        "summary": {
            "total_species": total_count,
            "success_species": success_count,
            "failed_species": total_count - success_count
        },
        "species_results": results,
        "cmd": cmd_history
    }
    
    manifest_file = output_dir / f"{prefix}.genome_prepare.manifest.json"
    with open(manifest_file, 'w') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    
    return manifest_file


def write_report(output_dir: Path, prefix: str, results: List[Dict[str, Any]]) -> Path:
    """写入分析报告"""
    report_path = output_dir / f"{prefix}.genome_prepare_report.md"
    
    success_count = sum(1 for r in results if r.get("success"))
    
    with open(report_path, 'w') as f:
        f.write(f"# Genome Prepare V2 Report - {prefix}\n\n")
        f.write(f"**Generated**: {datetime.now().isoformat()}\n\n")
        f.write("## Summary\n")
        f.write(f"- **Status**: {'✅ Success' if success_count > 0 else '❌ Failed'}\n")
        f.write(f"- **Total Species**: {len(results)}\n")
        f.write(f"- **Success**: {success_count}\n")
        f.write(f"- **Failed**: {len(results) - success_count}\n\n")
        
        f.write("## Species Details\n\n")
        for result in results:
            species = result.get("species", "Unknown")
            success = result.get("success", False)
            
            f.write(f"### {species}\n")
            f.write(f"- **Status**: {'✅ Success' if success else '❌ Failed'}\n")
            
            if success:
                f.write(f"- **Database Type**: {result.get('db_type', 'unknown')}\n")
                f.write(f"- **Gene Count**: {result.get('gene_count', 'N/A')}\n")
                files = result.get("files", {})
                f.write(f"- **Output Files**:\n")
                for file_type, path in files.items():
                    if path:
                        f.write(f"  - {file_type}: `{Path(path).name}`\n")
            else:
                f.write(f"- **Error**: {result.get('error', 'Unknown error')}\n")
            
            f.write("\n")
    
    return report_path


def main():
    parser = argparse.ArgumentParser(description="Genome Prepare V2 - Multi-species genome data preprocessing")
    parser.add_argument("-i", "--input", required=True, help="Input directory with raw genome data")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("-p", "--prefix", default="multi_species", help="Output prefix")
    parser.add_argument("--scripts-dir", default="/home/xia-lab/wutx/00test/bbav5/scripts", help="Directory containing Perl scripts")
    
    args = parser.parse_args()
    
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir = Path(args.scripts_dir)
    
    start_time = datetime.now()
    tool_versions = get_tool_versions()
    
    # 创建预处理器
    preparer = MultiSpeciesGenomePreparer(input_dir, output_dir, scripts_dir)
    
    try:
        # 处理所有物种
        results = preparer.process_all()
        
        # 生成manifest和报告
        manifest_file = write_manifest(output_dir, args.prefix, results, tool_versions, preparer.cmd_history, start_time)
        report_file = write_report(output_dir, args.prefix, results)
        
        # 输出总结
        success_count = sum(1 for r in results if r.get("success"))
        logger.info(f"\n{'='*60}")
        logger.info(f"Genome Prepare V2 Complete!")
        logger.info(f"{'='*60}")
        logger.info(f"Total species: {len(results)}")
        logger.info(f"Success: {success_count}")
        logger.info(f"Failed: {len(results) - success_count}")
        logger.info(f"Manifest: {manifest_file}")
        logger.info(f"Report: {report_file}")
        
    except Exception as e:
        logger.error(f"Genome prepare failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
