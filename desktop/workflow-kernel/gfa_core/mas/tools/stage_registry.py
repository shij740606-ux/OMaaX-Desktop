"""
Stage-level Tool Registry for Multi-Agent System
将bbav20311的分析阶段脚本封装为Agent可调用的Tool
"""
import os
import sys
import subprocess
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime

from gfa_core.project_paths import PROJECT_ROOT

CLI_DIR = PROJECT_ROOT / "cli"
# 镜像标准形态: 阶段环境名仅作元数据标识(容器 PATH 已固化对应环境)
CONDA_ENV = "test4py311"


@dataclass
class StageTool:
    """分析阶段工具定义"""
    id: str
    name: str
    script: str  # 脚本文件名，如 run_identify.py
    category: str  # identification, phylogeny, motif, domain, etc.
    description: str
    required_inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    timeout: int = 3600
    conda_env: str = CONDA_ENV


class StageToolExecutor:
    """分析阶段工具执行器"""
    
    def __init__(self, project_root: Path = PROJECT_ROOT):
        self.project_root = project_root
        self.cli_dir = project_root / "apps" / "cli"
        self.workspace_root = project_root / "workspace"
        self.input_root = project_root / "input"
    
    def execute(self, stage_tool: StageTool, job_id: str, prefix: str, 
                extra_params: dict = None, input_files: dict = None) -> dict:
        """
        执行分析阶段
        
        Args:
            stage_tool: 阶段工具定义
            job_id: 任务ID
            prefix: 前缀 (如 Ft.SPL)
            extra_params: 额外参数
            input_files: 输入文件映射
        
        Returns:
            执行结果字典
        """
        script_path = self.cli_dir / stage_tool.script
        if not script_path.exists():
            return {
                "status": "error",
                "error": f"Script not found: {script_path}",
                "stage": stage_tool.id
            }
        
        # 构建工作目录
        workspace = self.workspace_root / job_id
        workspace.mkdir(parents=True, exist_ok=True)
        
        input_dir = self.input_root / job_id
        input_dir.mkdir(parents=True, exist_ok=True)
        
        # 构建基础命令(镜像标准形态: 当前解释器即阶段环境解释器)
        cmd = [
            sys.executable, str(script_path),
            "--prefix", prefix,
            "--workspace", str(workspace),
            "--input-dir", str(input_dir),
        ]
        
        # 添加额外参数
        if extra_params:
            for key, value in extra_params.items():
                cmd.extend([f"--{key}", str(value)])
        
        # 添加输入文件参数（根据阶段不同）
        if input_files:
            if stage_tool.id == "identify":
                if "hmm" in input_files:
                    cmd.extend(["--hmm", input_files["hmm"]])
                if "protein" in input_files:
                    cmd.extend(["--protein", input_files["protein"]])
            elif stage_tool.id == "phylogeny":
                if "protein" in input_files:
                    cmd.extend(["--protein", input_files["protein"]])
            # 其他阶段的输入文件处理...
        
        # 执行命令
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=stage_tool.timeout,
                cwd=str(self.project_root)
            )
            
            # 解析manifest
            manifest_path = workspace / "final" / f"{prefix}.{stage_tool.id}.manifest.json"
            manifest = None
            if manifest_path.exists():
                try:
                    with open(manifest_path) as f:
                        manifest = json.load(f)
                except Exception as e:
                    manifest = {"error": str(e)}
            
            return {
                "status": "success" if result.returncode == 0 else "error",
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "stage": stage_tool.id,
                "manifest": manifest,
                "manifest_path": str(manifest_path) if manifest_path.exists() else None,
                "timestamp": datetime.now().isoformat()
            }
            
        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "error": f"Stage {stage_tool.id} timed out after {stage_tool.timeout}s",
                "stage": stage_tool.id
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "stage": stage_tool.id
            }


class StageToolRegistry:
    """分析阶段工具注册表"""
    
    def __init__(self):
        self._stages: dict[str, StageTool] = {}
        self._executor = StageToolExecutor()
        self._register_all_stages()
    
    def _register_all_stages(self):
        """注册所有分析阶段"""
        
        # 1. Gene Identification
        self.register(StageTool(
            id="identify",
            name="Gene Family Identification",
            script="run_identify.py",
            category="identification",
            description="Identify target gene family members using HMM/Pfam and fixed BLAST evidence: reference family proteins query the target whole-proteome database",
            required_inputs=["protein"],
            outputs=["identified_members.pep.fa", "identified_members.id", "identify.manifest.json"],
            parameters={
                "cpu": 8,
                "dom_i_evalue_max": 0.001,
                "hmm_cov_min": 0.3,
                "blast_evalue": "1e-05",
                "blast_identity_min": 30.0,
                "blast_database_role": "target_proteome",
                "blast_query_role": "reference_family_pep",
                "blast_candidate_id_column": "sseqid"
            },
            timeout=7200
        ))
        
        # 2. Phylogeny
        self.register(StageTool(
            id="phylogeny",
            name="Phylogenetic Analysis",
            script="run_phylogeny.py",
            category="phylogeny",
            description="Build phylogenetic tree using MAFFT and IQ-TREE",
            required_inputs=["protein"],
            outputs=["tree.nwk", "phylogeny.png", "phylogeny.manifest.json"],
            parameters={
                "cpu": 8,
                "bootstrap": 1000,
                "model": "MFP"
            },
            timeout=14400
        ))

        self.register(StageTool(
            id="reference_classification",
            name="Reference-guided Subfamily Classification",
            script="run_reference_classification.py",
            category="phylogeny",
            description="Assign target genes to reference-defined subfamilies from a joint tree",
            required_inputs=["tree", "reference_subfamily"],
            outputs=["reference_classification.assignment.tsv", "reference_classification.subfamily.tsv", "reference_classification.manifest.json"],
            parameters={"cpu": 4, "referencePrefix": "At"},
            timeout=1800
        ))

        self.register(StageTool(
            id="subfamily_confirm",
            name="Confirmed Subfamily Labels",
            script="run_subfamily_confirm.py",
            category="phylogeny",
            description="Freeze user-reviewed subfamily labels for downstream phylogeny, motif, domain, structure and promoter plots",
            required_inputs=["reference_classification.subfamily.tsv or user-edited confirmed_subfamily table"],
            outputs=["confirmed_subfamily.tsv", "confirmed_subfamily.review.tsv", "subfamily_confirm.manifest.json"],
            parameters={"cpu": 4},
            timeout=600
        ))
        
        # 3. Motif Analysis
        self.register(StageTool(
            id="motif",
            name="Motif Analysis",
            script="run_motif.py",
            category="motif",
            description="Analyze protein motifs using MEME",
            required_inputs=["protein"],
            outputs=["motif_results", "motif.manifest.json"],
            parameters={
                "nmotifs": 10,
                "min_width": 6,
                "max_width": 200,
                "cpu": 8
            },
            timeout=7200
        ))
        
        # 4. Domain Analysis
        self.register(StageTool(
            id="domain",
            name="Domain Analysis",
            script="run_domain.py",
            category="domain",
            description="Analyze protein domains using Pfam",
            required_inputs=["protein"],
            outputs=["domain_results", "domain.manifest.json"],
            parameters={
                "cpu": 8,
                "e_value": 1e-5
            },
            timeout=7200
        ))
        
        # 5. Structure Analysis
        self.register(StageTool(
            id="structure",
            name="Structure Analysis",
            script="run_structure.py",
            category="structure",
            description="Predict protein structures",
            required_inputs=["protein"],
            outputs=["structure_results", "structure.manifest.json"],
            parameters={

                "cpu": 8
            },
            timeout=7200
        ))

        self.register(StageTool(
            id="tfbs",
            name="TF Binding Site Prediction",
            script="run_tfbs.py",
            category="promoter",
            description="Extract upstream promoters and scan plant TF motif databases with MEME Suite FIMO",
            required_inputs=["gff3", "genome_fasta", "family_members.id", "motif_database"],
            outputs=["tfbs.sites.tsv", "tfbs.enrichment.tsv", "tfbs.gene_motif_matrix.tsv", "tfbs.manifest.json"],
            parameters={
                "upstream_length": 2000,
                "fimo_pvalue": 1e-4,
                "fimo_qvalue": 0.05,
                "motif_db_mode": "auto",
                "background_mode": "none",
                "cpu": 4,
            },
            timeout=14400
        ))

    def register(self, stage_tool: StageTool) -> None:
        self._stages[stage_tool.id] = stage_tool

    def get_tool(self, stage_id: str) -> StageTool | None:
        return self._stages.get(stage_id)

    def list_tools(self) -> list[StageTool]:
        return list(self._stages.values())

    def list_stage_ids(self) -> list[str]:
        return list(self._stages.keys())

    def execute(self, stage_id: str, job_id: str, prefix: str, extra_params: dict | None = None, input_files: dict | None = None) -> dict:
        stage_tool = self.get_tool(stage_id)
        if stage_tool is None:
            return {"status": "error", "error": f"Unknown stage: {stage_id}", "stage": stage_id}
        return self._executor.execute(stage_tool, job_id, prefix, extra_params, input_files)


def create_stage_registry() -> "StageToolRegistry":
    return StageToolRegistry()
