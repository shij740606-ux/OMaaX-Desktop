"""
Identify Tool - 封装 run_identify.py 为 LLM 可调用的 Tool
"""
import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

# 项目路径配置
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLI_SCRIPT = PROJECT_ROOT / "cli" / "run_identify.py"
INPUT_ROOT = PROJECT_ROOT / "input"
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"
PFAM_DB_DIR = PROJECT_ROOT / "data" / "hmm"

# 常用基因家族的Pfam ID映射（作为RAG不可用时的fallback）
FAMILY_PFAM_MAP = {
    "spl": "PF03110",      # SBP domain
    "sbp": "PF03110",      # 同上
    "myb": "PF00249",      # MYB DNA-binding domain
    "wrky": "PF03106",     # WRKY domain
    "bzip": "PF00170",     # bZIP domain
    "nac": "PF02365",      # NAC domain
    "ap2": "PF00847",      # AP2 domain
    "erf": "PF00847",      # AP2/ERF family
    "mads": "PF00319",     # MADS-box
    "homeobox": "PF00046", # Homeodomain
    "bhlh": "PF00010",     # bHLH domain
    "c2h2": "PF00096",     # C2H2 zinc finger
    "dreb": "PF00847",     # AP2/ERF subfamily
}


@dataclass
class IdentifyResult:
    """Identify执行结果"""
    success: bool
    message: str
    manifest_path: Optional[Path] = None
    final_members_count: int = 0
    final_proteins_path: Optional[Path] = None
    report_path: Optional[Path] = None
    error: Optional[str] = None


def get_pfam_from_knowledge(family_name: str) -> Tuple[Optional[str], str]:
    """
    获取基因家族对应的Pfam ID
    
    策略：
    1. 查本地知识库 (FAMILY_PFAM_MAP)
    2. 如未来有RAG，可在此扩展
    
    返回: (pfam_id, source说明)
    """
    family_lower = family_name.lower().strip()
    
    # 1. 直接匹配
    if family_lower in FAMILY_PFAM_MAP:
        return FAMILY_PFAM_MAP[family_lower], f"本地知识库精确匹配: {family_lower}"
    
    # 2. 去除常见后缀再匹配 (如 SPL_family -> SPL)
    for suffix in ["_family", "family", "_genes", "genes"]:
        if family_lower.endswith(suffix):
            base_name = family_lower[:-len(suffix)]
            if base_name in FAMILY_PFAM_MAP:
                return FAMILY_PFAM_MAP[base_name], f"本地知识库匹配(去除后缀): {base_name}"
    
    # 3. 子串匹配
    for known_family, pfam_id in FAMILY_PFAM_MAP.items():
        if known_family in family_lower or family_lower in known_family:
            return pfam_id, f"本地知识库模糊匹配: {known_family}"
    
    return None, "未在知识库中找到，需要用户指定Pfam ID或上传HMM文件"


def parse_prefix(input_prefix: str) -> Tuple[str, str]:
    """
    解析prefix，提取物种和家族名称
    
    支持格式:
    - "Ft.SPL" -> ("Ft", "SPL")
    - "SPL" -> ("auto", "SPL")  # 需从输入文件推断物种
    - "Ft.SPL.n5" -> ("Ft", "SPL")
    """
    parts = input_prefix.split('.')
    
    if len(parts) >= 2:
        # 假设第一部分是物种，第二部分是家族
        species = parts[0]
        family = parts[1]
        return species, family
    else:
        # 只有家族名，物种需推断
        return "auto", parts[0]


def find_input_files(job_id: str) -> Dict[str, Path]:
    """
    在 input/<job_id>/ 中查找输入文件
    
    返回: {"pep": Path, "gff": Path, "hmm": Path, "reference": Path, ...}
    """
    input_dir = INPUT_ROOT / job_id
    if not input_dir.exists():
        raise FileNotFoundError(f"输入目录不存在: {input_dir}")
    
    files = {"pep": None, "gff": None, "hmm": None, "reference": None, "subfamily": None}
    
    for file_path in input_dir.iterdir():
        if not file_path.is_file():
            continue
        
        name_lower = file_path.name.lower()
        
        # 蛋白质序列: *.pep.fasta, *.protein.fa, *.faa
        if any(x in name_lower for x in [".pep.", ".protein.", ".faa"]):
            if name_lower.endswith(('.fasta', '.fa')):
                files["pep"] = file_path
        
        # GFF3注释
        elif name_lower.endswith('.gff3') or name_lower.endswith('.gff'):
            files["gff"] = file_path
        
        # HMM文件 (排除pfam系统文件)
        elif name_lower.endswith('.hmm') and 'pfam' not in name_lower:
            files["hmm"] = file_path
        
        # 亚家族分组文件
        elif 'subfamily' in name_lower or '.geneid.' in name_lower:
            files["subfamily"] = file_path
        
        # 参考序列 (用于BLAST)
        elif name_lower.endswith(('.fasta', '.fa', '.faa')):
            if not files["reference"]:  # 第一个非pep文件作为参考
                # 检查是否不是pep文件
                if not any(x in name_lower for x in [".pep.", ".protein."]):
                    files["reference"] = file_path
    
    return files


def run_identify(
    job_id: str,
    prefix: str,
    required_pfams: Optional[str] = None,
    cpu: int = 8,
    dom_i_evalue_max: float = 1e-3,
    hmm_cov_min: float = 0.3,
    cut_ga: bool = False,
    retention_days: int = 7,
) -> IdentifyResult:
    """
    执行基因家族鉴定
    
    参数:
        job_id: 任务ID (对应input/<job_id>/目录)
        prefix: 分析前缀，如 "Ft.SPL"
        required_pfams: Pfam ID，如 "PF03110"。如果为None，自动从知识库获取
        cpu: 线程数
        dom_i_evalue_max: domain i-Evalue阈值
        hmm_cov_min: HMM覆盖度阈值
        cut_ga: 是否使用Pfam GA阈值
        retention_days: 结果保留天数
    
    返回:
        IdentifyResult对象
    """
    
    try:
        # 1. 检查输入目录
        input_dir = INPUT_ROOT / job_id
        if not input_dir.exists():
            return IdentifyResult(
                success=False,
                message=f"输入目录不存在: {input_dir}",
                error="INPUT_NOT_FOUND"
            )
        
        # 2. 查找输入文件
        try:
            input_files = find_input_files(job_id)
        except Exception as e:
            return IdentifyResult(
                success=False,
                message=f"查找输入文件失败: {e}",
                error="INPUT_SCAN_ERROR"
            )
        
        if not input_files["pep"]:
            return IdentifyResult(
                success=False,
                message=f"在 {input_dir} 中未找到蛋白质序列文件 (*.pep.fasta/*.protein.fa)",
                error="PEP_FILE_NOT_FOUND"
            )
        
        # 3. 确定Pfam ID
        species, family_name = parse_prefix(prefix)
        
        pfam_source = ""
        if required_pfams:
            # 用户指定了Pfam ID
            pfam_ids = required_pfams
            pfam_source = f"用户指定: {required_pfams}"
        else:
            # 从知识库获取
            pfam_id, pfam_source = get_pfam_from_knowledge(family_name)
            if pfam_id:
                pfam_ids = pfam_id
            else:
                # 检查是否有用户上传的HMM
                if input_files["hmm"]:
                    pfam_ids = None  # 使用用户HMM，不需要指定Pfam ID
                    pfam_source = f"使用用户上传HMM: {input_files['hmm'].name}"
                else:
                    return IdentifyResult(
                        success=False,
                        message=f"无法确定基因家族 '{family_name}' 的Pfam ID。请:\n"
                               f"1. 指定 --required-pfams PFxxxxx\n"
                               f"2. 上传HMM文件到input/{job_id}/\n"
                               f"3. 使用已知家族名称: {list(FAMILY_PFAM_MAP.keys())}",
                        error="PFAM_NOT_FOUND"
                    )
        
        # 4. 准备workspace
        workspace_dir = WORKSPACE_ROOT / job_id
        workspace_dir.mkdir(parents=True, exist_ok=True)
        
        # 5. 构建命令
        cmd = [
            sys.executable,
            str(CLI_SCRIPT),
            "--input-dir", str(input_dir),
            "--workspace", str(workspace_dir),
            "--prefix", prefix,
            "--cpu", str(cpu),
            "--dom-i-evalue-max", str(dom_i_evalue_max),
            "--hmm-cov-min", str(hmm_cov_min),
            "--retention-days", str(retention_days),
        ]
        
        if pfam_ids:
            cmd.extend(["--required-pfams", pfam_ids])
        
        if cut_ga:
            cmd.append("--cut-ga")
        
        # 指定新的Pfam数据库位置
        cmd.extend(["--pfam-dir", str(PFAM_DB_DIR)])
        
        # 6. 执行
        print(f"[IdentifyTool] 执行命令: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT
        )
        
        # 7. 解析结果
        if result.returncode != 0:
            return IdentifyResult(
                success=False,
                message=f"Identify执行失败 (exit code: {result.returncode})\n"
                       f"stdout: {result.stdout[-2000:]}\n"  # 最后2000字符
                       f"stderr: {result.stderr[-2000:]}",
                error="EXECUTION_FAILED"
            )
        
        # 8. 读取manifest
        manifest_path = workspace_dir / "final" / f"{prefix}.identify.manifest.json"
        if not manifest_path.exists():
            return IdentifyResult(
                success=False,
                message=f"执行成功但manifest未生成: {manifest_path}",
                error="MANIFEST_NOT_FOUND"
            )
        
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
        
        final_members = manifest.get("metrics", {}).get("final_members", 0)
        
        # 提取产物路径
        artifacts = manifest.get("artifacts", {})
        final_proteins_path = None
        report_path = None
        
        if isinstance(artifacts, dict):
            if "final_proteins" in artifacts:
                final_proteins_path = Path(artifacts["final_proteins"].get("full_path", ""))
            if "identify_report" in artifacts:
                report_path = Path(artifacts["identify_report"].get("full_path", ""))
        
        return IdentifyResult(
            success=True,
            message=f"✅ Identify完成\n"
                   f"   家族: {family_name}\n"
                   f"   Pfam来源: {pfam_source}\n"
                   f"   输入蛋白: {input_files['pep'].name}\n"
                   f"   最终成员数: {final_members}\n"
                   f"   Manifest: {manifest_path}",
            manifest_path=manifest_path,
            final_members_count=final_members,
            final_proteins_path=final_proteins_path,
            report_path=report_path
        )
        
    except Exception as e:
        return IdentifyResult(
            success=False,
            message=f"执行异常: {e}",
            error="EXCEPTION"
        )


def get_identify_params_help() -> str:
    """返回Identify参数说明，用于LLM理解"""
    return """
Identify阶段参数说明:
- prefix: 分析前缀，格式为 "物种.家族名"，如 "Ft.SPL"
- required_pfams: Pfam ID，如 "PF03110"。如不指定，自动从知识库获取
- cpu: 线程数 (默认8)
- dom_i_evalue_max: domain i-Evalue阈值 (默认1e-3)
- hmm_cov_min: HMM覆盖度阈值 (默认0.3)
- cut_ga: 使用Pfam GA阈值 (默认False)

常用家族Pfam ID:
""" + "\n".join([f"  {k}: {v}" for k, v in FAMILY_PFAM_MAP.items()])


# 测试函数
if __name__ == "__main__":
    # 测试知识库查询
    print("=== 测试知识库查询 ===")
    for test_name in ["SPL", "MYB", "WRKY", "unknown"]:
        pfam, source = get_pfam_from_knowledge(test_name)
        print(f"{test_name}: {pfam} ({source})")
    
    # 测试文件查找
    print("\n=== 测试文件查找 ===")
    # 找一个存在的job_id测试
    if INPUT_ROOT.exists():
        for job_dir in INPUT_ROOT.iterdir():
            if job_dir.is_dir():
                files = find_input_files(job_dir.name)
                print(f"Job {job_dir.name}:")
                for k, v in files.items():
                    print(f"  {k}: {v.name if v else None}")
                break
