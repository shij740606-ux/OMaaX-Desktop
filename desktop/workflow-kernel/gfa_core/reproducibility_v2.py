"""
可复现性自动化模块 V2
自动生成 commands.sh, environment.yml, checksums.sha256, provenance.json

使用方法:
from gfa_core.reproducibility_v2 import ReproducibilityManager

# 在 pipeline_backend.py 中使用
manager = ReproducibilityManager(job_id, prefix)
manager.start_stage("identify", cmd)
# ... 运行分析 ...
manager.end_stage("identify", status="success")
manager.finalize()  # 生成所有可复现性文件
"""

import os
import json
import hashlib
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

from gfa_core.project_paths import PROJECT_ROOT, WORKSPACE_ROOT


class StageRecorder:
    """单个阶段的记录器"""
    
    def __init__(self, stage: str, cmd: List[str], description: str = ""):
        self.stage = stage
        self.cmd = cmd
        self.description = description or f"Run {stage} analysis"
        self.start_time = datetime.now()
        self.end_time = None
        self.status = "running"
        self.log_file = None
        
    def complete(self, status: str = "success", log_file: Optional[str] = None):
        """标记阶段完成"""
        self.end_time = datetime.now()
        self.status = status
        self.log_file = log_file
        
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "stage": self.stage,
            "cmd": " ".join(self.cmd),
            "description": self.description,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "status": self.status,
            "log_file": self.log_file,
        }


class ReproducibilityManager:
    """
    可复现性管理器
    
    自动记录分析流程，生成可复现性文件
    """
    
    def __init__(
        self,
        job_id: str,
        prefix: str,
        workspace_root: str = str(WORKSPACE_ROOT),
        scope_file: str = "",
        scope_id: str = "",
    ):
        self.job_id = job_id
        self.prefix = prefix
        self.workspace = Path(workspace_root) / job_id
        self.final_dir = self.workspace / "final"
        self.reproducibility_dir = self.workspace / "reproducibility"
        self.scope_file = scope_file or ""
        self.scope_id = scope_id or ""
        self.scope = self._load_scope(self.scope_file)
        if not self.scope_id and self.scope:
            self.scope_id = str(self.scope.get("scope_id") or "")
        
        # 创建目录
        self.reproducibility_dir.mkdir(parents=True, exist_ok=True)
        
        # 记录信息
        self.stages: List[StageRecorder] = []
        self.current_stage: Optional[StageRecorder] = None
        self.start_time = datetime.now()
        
        # 收集系统信息
        self.system_info = self._collect_system_info()

    def _load_scope(self, scope_file: str) -> Dict[str, Any]:
        if not scope_file:
            return {}
        try:
            path = Path(scope_file)
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
        
    def _collect_system_info(self) -> Dict[str, Any]:
        """收集系统信息"""
        info = {
            "platform": os.uname().sysname if hasattr(os, 'uname') else "unknown",
            "hostname": os.uname().nodename if hasattr(os, 'uname') else "unknown",
            "python_version": os.sys.version,
            "timestamp": datetime.now().isoformat(),
        }
        
        # 尝试获取 conda 环境信息
        try:
            result = subprocess.run(
                ["conda", "info", "--envs"],
                capture_output=True,
                text=True,
                timeout=5
            )
            info["conda_envs"] = result.stdout
        except:
            info["conda_envs"] = "unknown"
            
        # 尝试获取当前激活的 conda 环境
        info["conda_active_env"] = os.environ.get("CONDA_DEFAULT_ENV", "unknown")
        
        return info
    
    def start_stage(self, stage: str, cmd: List[str], description: str = ""):
        """开始记录一个阶段"""
        # 如果之前有阶段在运行，先标记为完成
        if self.current_stage:
            self.current_stage.complete(status="interrupted")
            
        self.current_stage = StageRecorder(stage, cmd, description)
        self.stages.append(self.current_stage)
        
        # 实时保存进度
        self._save_progress()
        
    def end_stage(self, stage: str, status: str = "success", log_file: Optional[str] = None):
        """结束记录一个阶段"""
        if self.current_stage and self.current_stage.stage == stage:
            self.current_stage.complete(status=status, log_file=log_file)
            self.current_stage = None
            self._save_progress()
            
    def _save_progress(self):
        """保存当前进度到临时文件"""
        progress_file = self.reproducibility_dir / "progress.json"
        data = {
            "job_id": self.job_id,
            "prefix": self.prefix,
            "scope_id": self.scope_id,
            "scope_file": self.scope_file,
            "scope": self.scope,
            "start_time": self.start_time.isoformat(),
            "current_time": datetime.now().isoformat(),
            "stages": [s.to_dict() for s in self.stages],
            "system_info": self.system_info,
        }
        with open(progress_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
    def _calculate_sha256(self, file_path: Path) -> str:
        """计算文件 SHA-256"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    def _get_tool_versions(self) -> Dict[str, str]:
        """获取工具版本信息"""
        tools = {}
        
        tool_commands = {
            "python": ["python", "--version"],
            "hmmer": ["hmmsearch", "-h"],
            "blast": ["blastp", "-version"],
            "mafft": ["mafft", "--version"],
            "iqtree": ["iqtree", "--version"],
            "meme": ["meme", "-version"],
            "R": ["R", "--version"],
        }
        
        for name, cmd in tool_commands.items():
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                # 提取版本号（取第一行）
                first_line = result.stdout.split('\n')[0] if result.stdout else result.stderr.split('\n')[0]
                tools[name] = first_line.strip()
            except:
                tools[name] = "not found"
                
        return tools
    
    def generate_commands_sh(self) -> Path:
        """生成 commands.sh"""
        script_path = self.reproducibility_dir / "commands.sh"
        
        lines = [
            "#!/bin/bash",
            "# =============================================================================",
            "# bbav20311 Gene Family Analysis - Reproducibility Script",
            "# =============================================================================",
            f"# Job ID: {self.job_id}",
            f"# Prefix: {self.prefix}",
            f"# Scope ID: {self.scope_id or 'unscoped'}",
            f"# Scope file: {self.scope_file or 'none'}",
            f"# Generated: {datetime.now().isoformat()}",
            "# =============================================================================",
            "",
            "set -e  # Exit on error",
            "",
            f'JOB_ID="{self.job_id}"',
            f'PREFIX="{self.prefix}"',
            f'SCOPE_ID="{self.scope_id}"',
            f'SCOPE_FILE="{self.scope_file}"',
            f'WORKSPACE="{WORKSPACE_ROOT}/${{JOB_ID}}"',
            "",
            "# Check if running from project root",
            'if [ ! -f "SKILL.md" ]; then',
            '    echo "Error: Please run from project root directory"',
            '    exit 1',
            'fi',
            "",
            "echo \"==========================================\"",
            "echo \"bbav20311 Gene Family Analysis Pipeline\"",
            'echo "Job ID: ${JOB_ID}"',
            "echo \"==========================================\"",
            "",
        ]
        
        for i, stage in enumerate(self.stages, 1):
            lines.extend([
                "",
                f"# Stage {i}: {stage.stage}",
                f"echo \"[{i}/{len(self.stages)}] Running {stage.stage}...\"",
                str(stage.cmd),
                f'if [ $? -ne 0 ]; then echo "Error: {stage.stage} failed"; exit 1; fi',
            ])
        
        lines.extend([
            "",
            "echo \"\"",
            "echo \"==========================================\"",
            "echo \"Analysis Complete!\"",
            'echo "Results: ${WORKSPACE}/final/"',
            "echo \"==========================================\"",
            "",
        ])
        
        with open(script_path, 'w') as f:
            f.write("\n".join(lines))
        
        os.chmod(script_path, 0o755)
        return script_path
    
    def generate_environment_yml(self) -> Path:
        """生成 environment.yml"""
        env_path = self.reproducibility_dir / "environment.yml"
        
        # 获取当前 conda 环境
        conda_env = os.environ.get("CONDA_DEFAULT_ENV", "test4py311")
        
        content = f"""name: bbav20311
channels:
  - bioconda
  - conda-forge
  - defaults
dependencies:
  # Python
  - python=3.11
  - pip
  
  # Bioinformatics Tools
  - hmmer>=3.4
  - blast>=2.17.0
  - mafft
  - iqtree
  - meme>=4.11.2
  - seqkit>=2.12.0
  - bedtools
  - samtools
  
  # R
  - r-base>=4.4.3
  - r-ggplot2
  - r-ape
  
  # Python packages
  - biopython
  - numpy
  - pandas
  - matplotlib
  - fastapi
  - uvicorn
  - pydantic

# Current environment: {conda_env}
# Generated: {datetime.now().isoformat()}
"""
        
        with open(env_path, 'w') as f:
            f.write(content)
        
        return env_path
    
    def generate_checksums(self) -> Path:
        """生成 SHA-256 校验文件"""
        checksum_path = self.reproducibility_dir / "checksums.sha256"
        
        checksums = []
        
        if self.final_dir.exists():
            for file_path in self.final_dir.rglob("*"):
                if file_path.is_file():
                    sha256 = self._calculate_sha256(file_path)
                    rel_path = file_path.relative_to(self.final_dir)
                    checksums.append(f"{sha256}  {rel_path}")
        
        with open(checksum_path, 'w') as f:
            f.write(f"# SHA-256 Checksums for {self.job_id}\n")
            f.write(f"# Generated: {datetime.now().isoformat()}\n")
            f.write(f"# Prefix: {self.prefix}\n\n")
            if self.scope_id:
                f.write(f"# Scope ID: {self.scope_id}\n")
            if self.scope_file:
                f.write(f"# Scope file: {self.scope_file}\n")
            f.write("\n".join(sorted(checksums)))
        
        return checksum_path
    
    def generate_provenance(self) -> Path:
        """生成 provenance.json"""
        provenance_path = self.reproducibility_dir / "provenance.json"
        
        data = {
            "provenance_version": "1.0",
            "job_id": self.job_id,
            "prefix": self.prefix,
            "scope_id": self.scope_id,
            "scope_file": self.scope_file,
            "scope": self.scope,
            "start_time": self.start_time.isoformat(),
            "end_time": datetime.now().isoformat(),
            "system_info": self.system_info,
            "tool_versions": self._get_tool_versions(),
            "stages": [s.to_dict() for s in self.stages],
            "conda_environment": os.environ.get("CONDA_DEFAULT_ENV", "unknown"),
            "python_executable": os.sys.executable,
            "working_directory": str(self.workspace),
        }
        
        with open(provenance_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        return provenance_path
    
    def copy_to_project(self):
        """复制到项目级 reproducibility 目录"""
        project_repro_dir = PROJECT_ROOT / "reproducibility" / self.job_id
        project_repro_dir.mkdir(parents=True, exist_ok=True)
        
        for file in self.reproducibility_dir.iterdir():
            if file.is_file():
                shutil.copy2(file, project_repro_dir / file.name)
        
        return project_repro_dir
    
    def finalize(self):
        """完成分析，生成所有可复现性文件"""
        print(f"[INFO] Finalizing reproducibility for {self.job_id}")
        
        # 结束当前阶段
        if self.current_stage:
            self.current_stage.complete(status="completed")
            
        # 生成所有文件
        files = {
            "commands_sh": self.generate_commands_sh(),
            "environment_yml": self.generate_environment_yml(),
            "checksums_sha256": self.generate_checksums(),
            "provenance_json": self.generate_provenance(),
        }
        
        # 复制到项目目录
        files["project_copy"] = self.copy_to_project()
        
        print(f"[INFO] Reproducibility files generated:")
        for name, path in files.items():
            print(f"  - {name}: {path}")
        
        return files


# 向后兼容
ReproducibilityBundle = ReproducibilityManager
