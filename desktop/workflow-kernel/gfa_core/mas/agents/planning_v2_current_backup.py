"""
PlanningAgent V2 - LLM驱动的智能任务规划
根据用户意图和系统状态,动态生成最优执行计划

V2.1 更新:
- 集成PreFlight预检验证
- 集成Learning参数推荐
- 输出包含: 预检验证结果、执行计划、推荐参数
"""
import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from .base import AgentMessage, AgentState, BaseAgent, QueryMode
from gfa_core.mas.llm import LLMManager


@dataclass
class PreFlightIssue:
    """验证问题"""
    severity: str  # error, warning, info
    category: str  # input, dependency, resource, parameter
    message: str
    suggestion: str
    auto_fixable: bool = False
    fix_action: Optional[str] = None


@dataclass
class StageStatus:
    """阶段状态"""
    stage: str
    status: str  # success, partial_success, failed, not_found
    manifest_path: Optional[Path] = None
    output_files: List[str] = field(default_factory=list)
    missing_outputs: List[str] = field(default_factory=list)


class PlanningAgentV2(BaseAgent):
    """
    智能任务规划Agent V2
    
    功能:
    1. 根据用户意图生成分析流程
    2. 考虑已完成的阶段,避免重复执行
    3. 智能调度并行/串行执行
    4. 生成详细的执行计划(含参数,预估时间)
    5. 集成PreFlight预检验证
    6. 集成Learning参数推荐
    """
    name = "planning_v2"
    role = "Intelligent Workflow Planner"
    
    # 各阶段预期输入文件 (来自PreFlight)
    REQUIRED_INPUTS = {
        "identify": ["protein"],
        "phylogeny": ["protein"],
        "motif": ["protein"],
        "domain": ["protein"],
        "structure": ["protein"],
        "promoter": ["gene_list", "genome"],
        "synteny": ["gff", "blast"],
        "karyotype": ["chromosome_data"],
        "pep": ["protein"],
        "expression": ["expression_matrix", "sample_info"],
    }
    
    # 各阶段依赖 (来自PreFlight)
    STAGE_DEPENDENCIES = {
        "phylogeny": ["identify"],
        "motif": ["identify", "phylogeny"],
        "domain": ["identify", "phylogeny"],
        "structure": ["identify", "phylogeny"],
        "promoter": ["identify", "phylogeny"],
        "synteny": ["identify"],
        "karyotype": ["identify", "synteny"],
        "pep": ["identify"],
        "expression": ["identify", "phylogeny"],
    }
    
    # Learning默认参数
    DEFAULT_LEARNING_PARAMS = {
        "identify": {
            "cpu": 8,
            "dom_i_evalue_max": 0.001,
            "hmm_cov_min": 0.3,
            "blast_evalue": "1e-05"
        },
        "phylogeny": {
            "cpu": 8,
            "bootstrap": 1000,
            "model": "MFP"
        }
    }
    
    # 阶段基础信息
    STAGE_INFO = {
        "identify": {"name": "基因家族鉴定", "estimated_time": 5, "cpu": 8},
        "phylogeny": {"name": "系统发育分析", "estimated_time": 30, "cpu": 8},
        "motif": {"name": "Motif分析", "estimated_time": 20, "cpu": 8},
        "domain": {"name": "结构域分析", "estimated_time": 15, "cpu": 8},
        "structure": {"name": "基因结构分析", "estimated_time": 10, "cpu": 8},
        "promoter": {"name": "启动子分析", "estimated_time": 20, "cpu": 8},
        "synteny": {"name": "共线性分析", "estimated_time": 30, "cpu": 8},
        "karyotype": {"name": "核型分析", "estimated_time": 10, "cpu": 8},
        "interspecies_synteny": {"name": "物种间共线性", "estimated_time": 40, "cpu": 8},
        "expression": {"name": "表达分析", "estimated_time": 15, "cpu": 8},
        "kaks": {"name": "Ka/Ks分析", "estimated_time": 20, "cpu": 8},
        "ortholog": {"name": "直系同源基因鉴定", "estimated_time": 15, "cpu": 8, "description": "识别与参考物种的直系同源基因，用于PPI分析"},
        "ppi": {"name": "PPI分析", "estimated_time": 10, "cpu": 8},
        "pep": {"name": "蛋白特征分析", "estimated_time": 10, "cpu": 8},
        "rnaseq_qc": {"name": "RNA-seq质控", "estimated_time": 30, "cpu": 8},
        "rnaseq_align": {"name": "RNA-seq比对", "estimated_time": 60, "cpu": 8},
        "rnaseq_quant": {"name": "RNA-seq定量", "estimated_time": 30, "cpu": 8},
        "rnaseq_de": {"name": "差异表达分析", "estimated_time": 20, "cpu": 8},
        "rnaseq_enrich": {"name": "富集分析", "estimated_time": 15, "cpu": 8}
    }
    
    # 依赖关系
    DEPENDENCIES = {
        "phylogeny": ["identify"],
        "motif": ["identify", "phylogeny"],  # motif需要进化树结果
        "domain": ["identify", "phylogeny"],  # domain需要进化树结果
        "structure": ["identify", "phylogeny"],  # structure需要进化树结果
        "promoter": ["identify", "phylogeny"],  # promoter需要进化树结果
        "synteny": ["identify"],
        "karyotype": ["identify", "synteny"],  # karyotype需要synteny结果
        "interspecies_synteny": ["identify"],
        "expression": ["identify", "phylogeny"],  # expression需要进化树结果
        "kaks": ["identify", "phylogeny", "interspecies_synteny"],  # kaks需要进化树和共线性结果
        "ortholog": ["identify", "phylogeny"],  # ortholog需要进化树结果
        "ppi": ["identify", "phylogeny", "ortholog"],  # ppi需要ortholog映射到参考物种
        "pep": ["identify"],
        "rnaseq_align": ["rnaseq_qc"],
        "rnaseq_quant": ["rnaseq_align"],
        "rnaseq_de": ["rnaseq_quant"],
        "rnaseq_enrich": ["rnaseq_de"]
    }
    
    # 可并行执行的阶段组
    PARALLEL_GROUPS = [
        ["motif", "domain", "structure"],
        ["synteny", "karyotype"],
        ["expression", "ppi", "pep"],
    ]
    
    def __init__(self, llm_manager: LLMManager, 
                 project_root: str = "/home/xia-lab/wutx/00test/bbav5_0502_Yang_Tihuan.7880",
                 learning_history_file: str = "/home/xia-lab/wutx/00test/bbav5_0502_Yang_Tihuan.7880/gfa_core/mas/learning_history.json"):
        super().__init__(llm_manager)
        self.project_root = Path(project_root)
        self.workspace_root = self.project_root / "workspace"
        self.input_root = self.project_root / "input"
        self.learning_history_file = Path(learning_history_file)
        self.execution_history: List[Dict] = []
        self.param_stats: Dict[str, Dict] = {}
        self._load_learning_history()
    
    def _load_learning_history(self):
        """加载Learning历史记录"""
        if self.learning_history_file.exists():
            try:
                with open(self.learning_history_file) as f:
                    data = json.load(f)
                    self.execution_history = data.get("executions", [])
                    self.param_stats = data.get("param_stats", {})
            except Exception as e:
                print(f"[PlanningAgentV2] 加载学习历史失败: {e}")
    
    async def execute(self, state: AgentState, message: AgentMessage) -> AgentState:
        """
        执行智能任务规划 (V2.1 - 集成PreFlight和Learning)
        
        流程:
        1. 预检验证 (PreFlight)
        2. 获取Learning参数推荐
        3. 生成执行计划
        4. 如果验证失败,直接返回不生成计划
        """
        query = message.payload.get("query", "")
        # 优先从 results["results"]["intent"] 获取，回退到 results["intent"]
        intent = state.results.get("results", {}).get("intent", {}) or state.results.get("intent", {})
        completed_stages = state.results.get("completed_stages", [])
        
        print(f"[PlanningAgentV2] completed_stages: {completed_stages}")
        print(f"[PlanningAgentV2] intent stages: {intent.get('stages', 'NOT FOUND')}")
        
        # 从 message.payload 获取 job_id（优先），然后回退到 state 和 intent
        job_id = message.payload.get("job_id", "")
        if not job_id:
            job_id = state.job_id if hasattr(state, 'job_id') else ""
        if not job_id:
            job_id = intent.get("job_id", "")
        if not job_id:
            job_id = state.session_id
        workspace = intent.get("workspace", "")
        prefix = intent.get("prefix", f"{intent.get('species', 'Ft')}.{intent.get('gene_family', 'SPL')}")
        print(f"[PlanningAgentV2] job_id from payload/state: {job_id}, prefix: {prefix}")
        
        # ===== 步骤1: PreFlight预检验证 =====
        print(f"[PlanningAgentV2] 开始PreFlight预检验证...")
        preflight_result = await self._run_preflight_validation(intent, job_id, prefix, workspace)
        state.results["preflight_validation"] = preflight_result
        
        # 如果验证失败且有严重错误,直接返回不生成计划
        if not preflight_result["valid"]:
            errors = [i for i in preflight_result["issues"] if i["severity"] == "error"]
            if errors:
                error_msg = "; ".join([e["message"] for e in errors])
                print(f"[PlanningAgentV2] PreFlight验证失败: {error_msg}")
                state.messages.append({
                    "role": "planning",
                    "content": f"❌ 预检验证失败: {error_msg}",
                })
                state.results["task_plan"] = {
                    "valid": False,
                    "error": error_msg,
                    "preflight_result": preflight_result,
                    "stages": [],
                    "learning_recommendations": {}
                }
                return state
        
        # ===== 步骤2: 获取Learning参数推荐 =====
        target_stages = intent.get("stages", ["identify"])
        learning_recommendations = await self._get_learning_recommendations(target_stages)
        state.results["learning_recommendations"] = learning_recommendations
        
        # ===== 步骤3: 生成执行计划 =====
        plan = await self._generate_plan_with_llm(
            query=query,
            intent=intent,
            completed_stages=completed_stages,
            job_id=job_id,
            workspace=workspace,
            learning_recommendations=learning_recommendations
        )
        
        # 更新状态 - 包含所有三部分，同时提供扁平化的stages字段
        state.results["task_plan"] = {
            "valid": True,
            "preflight_validation": preflight_result,
            "execution_plan": plan,
            "learning_recommendations": learning_recommendations,
            # 扁平化字段，方便前端直接访问
            "stages": plan.get("stages", []),
            "total_stages": plan.get("total_stages", 0),
            "prefix": plan.get("prefix", ""),
            "gene_family": plan.get("gene_family", ""),
            "species": plan.get("species", ""),
            "estimated_total_time": plan.get("estimated_total_time", "")
        }
        
        # 添加消息
        total_time = sum(s.get("estimated_time", 10) for s in plan.get("stages", []))
        warning_count = len([i for i in preflight_result["issues"] if i["severity"] == "warning"])
        info_count = len([i for i in preflight_result["issues"] if i["severity"] == "info"])
        
        status_msg = f"✅ 预检验证通过"
        if warning_count > 0:
            status_msg += f" ({warning_count}个警告)"
        status_msg += f" | 已生成执行计划: {len(plan.get('stages', []))}个阶段, 预估{total_time}分钟"
        if learning_recommendations:
            status_msg += f" | 已应用{len(learning_recommendations)}个阶段的学习推荐参数"
        
        state.messages.append({
            "role": "planning",
            "content": status_msg,
        })
        
        return state
    
    # ==================== PreFlight 验证方法 ====================
    
    async def _run_preflight_validation(self, intent: Dict, job_id: str, prefix: str, workspace: str) -> Dict:
        """
        执行PreFlight预检验证
        
        Returns:
            验证结果字典,包含valid, issues, stage_status等
        """
        issues = []
        stage_status = {}
        
        workspace_path = Path(workspace) if workspace else (self.workspace_root / job_id if job_id else self.workspace_root)
        input_dir = self.input_root / job_id if job_id else self.input_root
        
        target_stages = intent.get("stages", ["identify"])
        
        for stage_id in target_stages:
            print(f"[PreFlight] 验证阶段: {stage_id}")
            
            # 1. 检查依赖阶段
            dep_issues = self._check_dependencies(stage_id, prefix, workspace_path)
            issues.extend(dep_issues)
            
            # 2. 检查输入文件
            input_issues = self._check_input_files(stage_id, input_dir)
            issues.extend(input_issues)
            
            # 3. 检查已有输出(manifest)
            status = self._check_stage_status_preflight(stage_id, prefix, workspace_path)
            stage_status[stage_id] = status
            
            if status.status in ["success", "partial_success"]:
                print(f"[PreFlight] 阶段 {stage_id} 已完成 ({status.status})")
                issues.append({
                    "severity": "info",
                    "category": "dependency",
                    "message": f"阶段 {stage_id} 已完成",
                    "suggestion": "该阶段已完成,可跳过执行",
                    "auto_fixable": True,
                    "fix_action": "skip_stage"
                })
        
        # 4. 检查资源
        resource_issues = self._check_resources()
        issues.extend(resource_issues)
        
        # 判断整体有效性
        errors = [i for i in issues if i["severity"] == "error"]
        warnings = [i for i in issues if i["severity"] == "warning"]
        
        valid = len(errors) == 0
        
        return {
            "valid": valid,
            "issues": issues,
            "stage_status": {
                stage: {
                    "status": status.status,
                    "manifest_path": str(status.manifest_path) if status.manifest_path else None,
                    "output_files": status.output_files,
                    "missing_outputs": status.missing_outputs
                }
                for stage, status in stage_status.items()
            },
            "summary": {
                "total_stages": len(target_stages),
                "errors": len(errors),
                "warnings": len(warnings),
                "completed_stages": sum(1 for s in stage_status.values() if s.status == "success")
            }
        }
    
    def _check_dependencies(self, stage_id: str, prefix: str, workspace: Path) -> List[Dict]:
        """检查依赖阶段"""
        issues = []
        deps = self.STAGE_DEPENDENCIES.get(stage_id, [])
        
        for dep in deps:
            dep_status = self._check_stage_status_preflight(dep, prefix, workspace)
            if dep_status.status not in ["success", "partial_success"]:
                # 将依赖未完成从 error 改为 warning
                # 因为 _generate_plan_with_llm 会自动添加依赖阶段
                issues.append({
                    "severity": "warning",  # 改为 warning 而不是 error
                    "category": "dependency",
                    "message": f"依赖阶段 {dep} 未完成 (状态: {dep_status.status})",
                    "suggestion": f"将自动添加 {dep} 阶段到执行计划",
                    "auto_fixable": True,  # 可自动修复
                    "fix_action": "add_dependency"
                })
        
        return issues
    
    def _check_input_files(self, stage_id: str, input_dir: Path) -> List[Dict]:
        """检查输入文件"""
        issues = []
        required = self.REQUIRED_INPUTS.get(stage_id, [])
        
        for file_type in required:
            found = self._find_input_file(input_dir, file_type)
            if not found:
                issues.append({
                    "severity": "warning",
                    "category": "input",
                    "message": f"缺少 {file_type} 类型输入文件",
                    "suggestion": f"请准备 {file_type} 文件或检查已有输出",
                    "auto_fixable": False
                })
        
        return issues
    
    def _find_input_file(self, input_dir: Path, file_type: str) -> bool:
        """查找输入文件"""
        patterns = {
            "hmm": ["*.hmm", "*.hmmprofile"],
            "protein": ["*.faa", "*.fasta", "*.pep"],
            "gene_list": ["*.gene", "*.txt", "*.list"],
            "genome": ["*.fna", "*.genome", "*.fasta"],
            "gff": ["*.gff", "*.gff3"],
            "blast": ["*.blast", "*.blastp"],
            "chromosome_data": ["*.chr", "*.chromosome"],
            "expression_matrix": ["*.matrix", "*.csv", "*.txt"],
            "sample_info": ["*.sample", "*.info"]
        }
        
        for pattern in patterns.get(file_type, [f"*{file_type}*"]):
            if list(input_dir.glob(pattern)):
                return True
        return False
    
    def _check_stage_status_preflight(self, stage_id: str, prefix: str, workspace: Path) -> StageStatus:
        """检查阶段执行状态(PreFlight版本)"""
        final_dir = workspace / "final"
        
        manifest_file = final_dir / f"{prefix}.{stage_id}.manifest.json"
        
        if manifest_file.exists():
            try:
                with open(manifest_file, 'r') as f:
                    manifest = json.load(f)
                
                status = manifest.get("status", "unknown")
                artifacts = manifest.get("artifacts", {})
                output_files = list(artifacts.keys())
                
                return StageStatus(
                    stage=stage_id,
                    status=status,
                    manifest_path=manifest_file,
                    output_files=output_files,
                    missing_outputs=[]
                )
            except:
                pass
        
        return StageStatus(
            stage=stage_id,
            status="not_found",
            manifest_path=None,
            output_files=[],
            missing_outputs=[]
        )
    
    def _check_resources(self) -> List[Dict]:
        """检查系统资源"""
        issues = []
        
        # CPU 检查
        cpu_count = os.cpu_count() or 1
        if cpu_count < 4:
            issues.append({
                "severity": "warning",
                "category": "resource",
                "message": f"CPU 核数较少: {cpu_count}",
                "suggestion": "建议至少 4 核 CPU",
                "auto_fixable": False
            })
        
        # 磁盘空间检查
        try:
            mem_info = shutil.disk_usage("/")
            mem_gb = mem_info.free / (1024**3)
            if mem_gb < 2:
                issues.append({
                    "severity": "warning",
                    "category": "resource",
                    "message": f"磁盘空间不足: {mem_gb:.1f} GB",
                    "suggestion": "建议至少 10 GB 磁盘空间",
                    "auto_fixable": False
                })
        except:
            pass
        
        return issues
    
    # ==================== Learning 参数推荐方法 ====================
    
    async def _get_learning_recommendations(self, target_stages: List[str]) -> Dict[str, Dict]:
        """
        获取Learning参数推荐
        
        Args:
            target_stages: 目标阶段列表
            
        Returns:
            各阶段的参数推荐字典
        """
        recommendations = {}
        
        for stage in target_stages:
            # 从历史记录中获取推荐参数
            stage_history = [h for h in self.execution_history 
                            if h.get("stage") == stage and h.get("success")]
            
            if len(stage_history) >= 3:
                # 有足够历史数据，分析最优参数
                params = self._analyze_best_params(stage, stage_history)
                recommendations[stage] = {
                    "confidence": "high" if len(stage_history) >= 10 else "medium",
                    "params": params,
                    "message": f"基于 {len(stage_history)} 次成功执行推荐"
                }
            else:
                # 使用默认参数
                recommendations[stage] = {
                    "confidence": "low",
                    "params": self.DEFAULT_LEARNING_PARAMS.get(stage, {}),
                    "message": "历史数据不足，使用默认参数"
                }
        
        return recommendations
    
    def _analyze_best_params(self, stage: str, stage_history: List[Dict]) -> Dict:
        """分析最优参数"""
        # 简单实现：返回最常用的参数组合
        if not stage_history:
            return self.DEFAULT_LEARNING_PARAMS.get(stage, {})
        
        # 取最近一次成功的参数
        latest = stage_history[-1]
        return latest.get("params", self.DEFAULT_LEARNING_PARAMS.get(stage, {}))
    
    async def _generate_plan_with_llm(
        self, 
        query: str, 
        intent: Dict,
        completed_stages: List[str],
        job_id: str = "",
        workspace: str = "",
        learning_recommendations: Dict = None
    ) -> Dict[str, Any]:
        """使用LLM生成执行计划"""
        
        target_stages = intent.get("stages", ["identify"])
        gene_family = intent.get("gene_family", "SPL")
        species = intent.get("species", "Ft")
        prefix = intent.get("prefix", f"{species}.{gene_family}")
        
        # 使用传入的 workspace 和 job_id
        if job_id and workspace:
            workspace = f"{workspace}"
        elif not workspace:
            workspace = f"/home/xia-lab/wutx/00test/bbav5_0502_Yang_Tihuan.7880/workspace/{job_id}" if job_id else "/home/xia-lab/wutx/00test/bbav5_0502_Yang_Tihuan.7880/workspace"
        
        # ===== 关键修改：基于 manifest 文件状态过滤阶段 =====
        remaining_stages = []
        for stage in target_stages:
            status = self._check_stage_status_preflight(stage, prefix, Path(workspace))
            print(f"[PlanningAgentV2] Stage {stage} status from manifest: {status}")
            
            if status == "success":
                # 成功完成，跳过
                print(f"[PlanningAgentV2] Stage {stage} already completed (success), skipping")
                continue
            elif status == "partial_success":
                # 部分成功，询问用户或继续（这里选择继续，让用户决定）
                print(f"[PlanningAgentV2] Stage {stage} partially completed, will re-execute")
                remaining_stages.append(stage)
            elif status == "failed":
                # 失败，重新执行
                print(f"[PlanningAgentV2] Stage {stage} failed previously, will re-execute")
                remaining_stages.append(stage)
            else:
                # not_found 或其他，执行
                remaining_stages.append(stage)
        
        # 回退：如果没有获取到 workspace，使用旧的 completed_stages 逻辑
        if not remaining_stages and not job_id:
            print(f"[PlanningAgentV2] Falling back to completed_stages logic")
            remaining_stages = [s for s in target_stages if s not in completed_stages]
        
        # 如果没有剩余阶段,返回空计划
        if not remaining_stages:
            return {
                "prefix": prefix,
                "gene_family": gene_family,
                "species": species,
                "stages": [],
                "total_stages": 0,
                "estimated_time": "0分钟",
                "description": "所有阶段已完成",
                "parallel_groups": []
            }
        
        # 检查依赖阶段是否已完成，如果已完成则不添加到计划
        stages_with_deps = []
        for stage in remaining_stages:
            deps = self.DEPENDENCIES.get(stage, [])
            # 只添加未完成的依赖阶段
            for dep in deps:
                if dep not in completed_stages and dep not in stages_with_deps and dep not in remaining_stages:
                    stages_with_deps.append(dep)
            stages_with_deps.append(stage)
        
        # 生成默认阶段配置（传入Learning推荐参数）
        stages = self._generate_default_stages(stages_with_deps, prefix, learning_recommendations)
        
        total_time = sum(s["estimated_time"] for s in stages)
        
        return {
            "prefix": prefix,
            "gene_family": gene_family,
            "species": species,
            "stages": stages,
            "total_stages": len(stages),
            "execution_strategy": "parallel",
            "max_parallel": 4,
            "estimated_total_time": f"{total_time}分钟",
            "description": f"执行{gene_family}基因家族的{', '.join(remaining_stages)}分析",
            "parallel_groups": []
        }
    
    def _generate_default_stages(
        self, 
        stage_ids: List[str], 
        prefix: str,
        learning_recommendations: Dict = None
    ) -> List[Dict]:
        """生成默认阶段配置（支持Learning推荐参数）"""
        stages = []
        
        # 获取Learning推荐的参数
        recommended_params = {}
        if learning_recommendations and learning_recommendations.get("confidence") in ["high", "medium"]:
            recommended_params = learning_recommendations.get("params", {})
            print(f"[PlanningAgentV2] 应用Learning推荐参数: {recommended_params}")
        
        for i, stage_id in enumerate(stage_ids):
            info = self.STAGE_INFO.get(stage_id, {"name": stage_id, "estimated_time": 10, "cpu": 8})
            deps = self.DEPENDENCIES.get(stage_id, [])
            
            # 判断是否可以并行
            can_parallel = len(deps) > 0 and i > 0
            parallel_group = None
            
            for group in self.PARALLEL_GROUPS:
                if stage_id in group:
                    parallel_group = f"group_{self.PARALLEL_GROUPS.index(group)}"
                    break
            
            # 基础参数
            stage_params = {
                "prefix": prefix,
                "cpu": info["cpu"]
            }
            
            # 融入Learning推荐参数（如果有）
            if stage_id in recommended_params:
                stage_params.update(recommended_params[stage_id])
                print(f"[PlanningAgentV2] Stage {stage_id} 使用推荐参数: {recommended_params[stage_id]}")
            
            stage = {
                "id": stage_id,
                "name": info["name"],
                "order": i + 1,
                "dependencies": deps,
                "can_parallel": can_parallel,
                "parallel_group": parallel_group,
                "params": stage_params,
                "estimated_time": info["estimated_time"]
            }
            stages.append(stage)
        
        return stages


def create_planning_agent_v2(llm_manager):
    """创建PlanningAgentV2实例"""
    return PlanningAgentV2(llm_manager)
