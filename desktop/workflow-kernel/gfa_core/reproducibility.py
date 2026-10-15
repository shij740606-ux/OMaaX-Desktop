"""
可复现性支持模块
自动生成 commands.sh, environment.yml, checksums.sha256
"""

import os
import json
import hashlib
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any

from gfa_core.project_paths import PROJECT_ROOT, WORKSPACE_ROOT


class ReproducibilityBundle:
    """可复现性包生成器"""
    
    def __init__(self, job_id: str, prefix: str, workspace_root: str):
        self.job_id = job_id
        self.prefix = prefix
        self.workspace = Path(workspace_root) / job_id
        self.final_dir = self.workspace / "final"
        self.reproducibility_dir = self.workspace / "reproducibility"
        self.commands = []
        
    def add_command(self, stage: str, cmd: List[str], description: str = ""):
        """记录命令"""
        self.commands.append({
            "stage": stage,
            "cmd": " ".join(cmd),
            "description": description,
            "timestamp": datetime.now().isoformat()
        })
        
    def generate_commands_sh(self) -> Path:
        """生成 commands.sh"""
        self.reproducibility_dir.mkdir(exist_ok=True)
        
        script_path = self.reproducibility_dir / "commands.sh"
        
        lines = [
            "#!/bin/bash",
            "# =============================================================================",
            f"# bbav20311 Gene Family Analysis - Reproducibility Script",
            "# =============================================================================",
            f"# Job ID: {self.job_id}",
            f"# Prefix: {self.prefix}",
            f"# Generated: {datetime.now().isoformat()}",
            "# =============================================================================",
            "",
            "set -e  # Exit on error",
            "",
            f"JOB_ID=\"{self.job_id}\"",
            f"PREFIX=\"{self.prefix}\"",
            f"WORKSPACE=\"{WORKSPACE_ROOT}/${{JOB_ID}}\"",
            "",
            "echo \"==========================================\"",
            "echo \"bbav20311 Gene Family Analysis Pipeline\"",
            "echo \"Job ID: ${JOB_ID}\"",
            "echo \"==========================================\"",
            "",
        ]
        
        for i, cmd_info in enumerate(self.commands, 1):
            lines.extend([
                "",
                f"# {cmd_info['description'] or f'Stage {i}'}"
                f"echo \"[STAGE {i}/{len(self.commands)}] {cmd_info['stage']}...\"",
                cmd_info['cmd'],
            ])
        
        lines.extend([
            "",
            "echo \"\"",
            "echo \"==========================================\"",
            "echo \"Analysis Complete!\"",
            "echo \"Results: ${WORKSPACE}/final/\"",
            "echo \"==========================================\"",
            "",
        ])
        
        with open(script_path, 'w') as f:
            f.write("\n".join(lines))
        
        # 添加执行权限
        os.chmod(script_path, 0o755)
        
        return script_path
    
    def generate_environment_yml(self, conda_env: str = "test4py311") -> Path:
        """生成 environment.yml"""
        self.reproducibility_dir.mkdir(exist_ok=True)
        
        env_path = self.reproducibility_dir / "environment.yml"
        
        # 从当前 conda 环境导出（简化版）
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
        self.reproducibility_dir.mkdir(exist_ok=True)
        
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
            f.write("\n".join(sorted(checksums)))
        
        return checksum_path
    
    def _calculate_sha256(self, file_path: Path) -> str:
        """计算文件 SHA-256"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    def copy_to_project_reproducibility(self):
        """复制到项目级 reproducibility 目录"""
        project_repro_dir = PROJECT_ROOT / "reproducibility" / self.job_id
        project_repro_dir.mkdir(parents=True, exist_ok=True)
        
        # 复制文件
        for file in self.reproducibility_dir.iterdir():
            if file.is_file():
                shutil.copy2(file, project_repro_dir / file.name)
        
        return project_repro_dir


def create_reproducibility_bundle(
    job_id: str,
    prefix: str,
    workspace_root: str = str(WORKSPACE_ROOT),
    commands: List[Dict[str, Any]] = None
) -> Dict[str, Path]:
    """
    创建完整的可复现性包
    
    Args:
        job_id: 任务ID
        prefix: 前缀
        workspace_root: workspace 根目录
        commands: 命令列表 [{"stage": "", "cmd": "", "description": ""}]
    
    Returns:
        生成的文件路径字典
    """
    bundle = ReproducibilityBundle(job_id, prefix, workspace_root)
    
    # 添加命令
    if commands:
        for cmd in commands:
            bundle.add_command(
                stage=cmd.get("stage", ""),
                cmd=cmd.get("cmd", []),
                description=cmd.get("description", "")
            )
    
    # 生成文件
    result = {
        "commands_sh": bundle.generate_commands_sh(),
        "environment_yml": bundle.generate_environment_yml(),
        "checksums_sha256": bundle.generate_checksums(),
    }
    
    # 复制到项目目录
    result["project_copy"] = bundle.copy_to_project_reproducibility()
    
    return result


# 兼容旧版导入
if __name__ == "__main__":
    # 测试
    bundle = ReproducibilityBundle("test_job", "Ft.TEST", "/tmp/test_workspace")
    bundle.add_command("identify", ["python", "run_identify.py", "--input", "test.fa"])
    print(bundle.generate_commands_sh())
