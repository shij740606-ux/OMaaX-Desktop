"""
带可复现性支持的 pipeline 包装器

使用方法:
from gfa_core.pipeline_with_repro import run_stage_with_repro

# 替换原来的 run_stage 调用
result = run_stage_with_repro(stage, job, prefix, cpu=cpu, ...)
"""

import sys
from pathlib import Path
from typing import Tuple, Optional

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from apps.web.pipeline_backend import run_stage as _original_run_stage, JobInfo, WORKSPACE_ROOT
from gfa_core.reproducibility import ReproducibilityBundle


def run_stage_with_repro(
    stage: str,
    job: JobInfo,
    prefix: str,
    cpu: int = 8,
    required_pfams: Optional[str] = None,
    ref_species: Optional[list] = None,
    retention_days: int = 7,
    **kwargs
) -> Tuple[str, Optional[Path]]:
    """
    带可复现性支持的 run_stage 包装器
    
    在原始 run_stage 基础上，自动记录命令和生成可复现性文件
    """
    
    # 初始化可复现性 bundle
    reproducibility_bundle = None
    try:
        reproducibility_bundle = ReproducibilityBundle(
            job_id=job.job_id,
            prefix=prefix,
            workspace_root=str(WORKSPACE_ROOT)
        )
    except Exception as e:
        print(f"[WARN] ReproducibilityBundle init failed: {e}")
    
    # 调用原始 run_stage
    result_msg, manifest = _original_run_stage(
        stage=stage,
        job=job,
        prefix=prefix,
        cpu=cpu,
        required_pfams=required_pfams,
        ref_species=ref_species,
        retention_days=retention_days,
        **kwargs
    )
    
    # 如果成功，生成可复现性文件
    if reproducibility_bundle and "✅" in result_msg:
        try:
            reproducibility_bundle.generate_commands_sh()
            reproducibility_bundle.generate_environment_yml()
            reproducibility_bundle.generate_checksums()
            reproducibility_bundle.copy_to_project_reproducibility()
            print(f"[INFO] Reproducibility files generated for {stage}")
        except Exception as e:
            print(f"[WARN] Failed to generate reproducibility files: {e}")
    
    return result_msg, manifest


# 保持向后兼容
run_stage = run_stage_with_repro
