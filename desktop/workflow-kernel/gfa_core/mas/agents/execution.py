"""
ExecutionAgent V2 - 集成StageToolRegistry
支持执行bbav20311分析阶段脚本
"""
import asyncio
from typing import Dict, List, Optional
from .base import AgentMessage, AgentState, BaseAgent
from gfa_core.mas.llm import LLMManager
from gfa_core.project_paths import WORKSPACE_ROOT


class ExecutionAgent(BaseAgent):
    """
    任务执行Agent V2
    使用StageToolRegistry执行实际的分析阶段
    """
    name = "execution"
    role = "Task Execution Specialist"
    
    def __init__(self, llm_manager: LLMManager, stage_registry=None):
        super().__init__(llm_manager)
        self.stage_registry = stage_registry
    
    async def execute(self, state: AgentState, message: AgentMessage) -> AgentState:
        """
        执行分析任务
        
        从state中提取任务信息，使用StageToolRegistry执行
        """
        print(f"[ExecutionAgent] 开始执行，session_id: {state.session_id}")
        
        # 获取任务信息
        task_plan = state.results.get("task_plan", {})
        stages_detail = task_plan.get("stages", [])
        # 优先使用用户传入的 job_id，其次使用 session_id
        job_id = state.results.get("job_id") or state.session_id or "default_job"
        prefix = state.results.get("prefix", "")
        
        print(f"[ExecutionAgent] task_plan: {task_plan}")
        print(f"[ExecutionAgent] stages_detail: {stages_detail}")
        print(f"[ExecutionAgent] job_id: {job_id}, prefix: {prefix}")
        
        # 提取 stage_id 列表（支持 dict 或 string）
        stages_to_run = []
        for stage in stages_detail:
            if isinstance(stage, dict):
                stages_to_run.append(stage.get("id", "unknown"))
            else:
                stages_to_run.append(stage)
        
        print(f"[ExecutionAgent] stages_to_run: {stages_to_run}")
        
        # 获取意图信息（用于参数）
        intent = state.results.get("results", {}).get("intent", {}) or state.results.get("intent", {})
        print(f"[ExecutionAgent] intent: {intent}")
        
        if not stages_to_run:
            # 检查是否是因为PreFlight验证失败导致没有可执行的阶段
            # preflight_result在task_plan中，key是"preflight_result"
            preflight_result = task_plan.get("preflight_result", {})
            task_plan_valid = task_plan.get("valid", True)
            task_plan_error = task_plan.get("error", "")
            
            if not task_plan_valid:
                # PreFlight验证失败，返回错误而不是加载旧结果
                error_msg = task_plan_error or "执行前验证失败"
                print(f"[ExecutionAgent] ❌ PreFlight验证失败，无法执行: {error_msg}")
                state.messages.append({
                    "role": "execution",
                    "content": f"❌ 执行前验证失败: {error_msg}"
                })
                # AgentState使用results字典
                state.results["execution_status"] = "error"
                state.results["execution_error"] = error_msg
                state.results["execution_results"] = []
                return state
            
            # 如果没有需要执行的阶段（可能都已完成了），从manifest加载已有结果
            print(f"[ExecutionAgent] ✅ 没有需要执行的阶段（可能都已完成了），从manifest加载结果")
            
            # 从manifest加载已完成阶段的结果
            completed_results = []
            job_id = state.results.get("job_id") or state.session_id
            prefix = state.results.get("prefix", "")
            
            try:
                from pathlib import Path
                import json
                workspace = WORKSPACE_ROOT / job_id / "final"
                if workspace.exists():
                    for manifest_file in workspace.glob(f"{prefix}.*.manifest.json"):
                        try:
                            with open(manifest_file, 'r') as f:
                                manifest = json.load(f)
                            stage = manifest.get("stage", "unknown")
                            completed_results.append({
                                "stage": stage,
                                "result": {
                                    "status": manifest.get("status", "success"),
                                    "manifest": manifest,
                                    "manifest_path": str(manifest_file),
                                    "message": f"✅ {stage.capitalize()} 完成（从manifest加载）"
                                }
                            })
                            print(f"[ExecutionAgent] 加载manifest: {manifest_file.name}, stage: {stage}")
                        except Exception as e:
                            print(f"[ExecutionAgent] 加载manifest失败: {e}")
            except Exception as e:
                print(f"[ExecutionAgent] 扫描manifest目录失败: {e}")
            
            state.messages.append({
                "role": "execution",
                "content": f"✅ 所有阶段已完成，加载 {len(completed_results)} 个已有结果"
            })
            state.results["execution_status"] = "success"
            state.results["execution_results"] = completed_results
            state.results["execution_error"] = None
            return state
        
        # 检查stage_registry是否可用
        print(f"[ExecutionAgent] stage_registry: {self.stage_registry}")
        if self.stage_registry is None:
            print("[ExecutionAgent] ❌ Stage registry not available")
            state.messages.append({
                "role": "execution",
                "content": "⚠️ Stage registry not available, cannot execute analysis"
            })
            state.results["execution_status"] = "error"
            state.results["execution_error"] = "Stage registry not initialized"
            return state
        
        print(f"[ExecutionAgent] ✅ Stage registry available, stages: {self.stage_registry.list_all()}")
        
        # 执行每个阶段
        execution_results = []
        overall_status = "success"
        
        print(f"[ExecutionAgent] 开始执行 {len(stages_to_run)} 个阶段")
        
        for stage_id in stages_to_run:
            print(f"[ExecutionAgent] 处理阶段: {stage_id}")
            
            # 检查阶段是否存在
            try:
                stage_tool = self.stage_registry.get(stage_id)
                print(f"[ExecutionAgent] stage_tool: {stage_tool}")
            except Exception as e:
                print(f"[ExecutionAgent] ❌ 获取 stage_tool 失败: {e}")
                import traceback
                traceback.print_exc()
                state.messages.append({
                    "role": "execution",
                    "content": f"❌ Stage '{stage_id}' 获取失败: {str(e)}"
                })
                overall_status = "error"
                continue
            
            if stage_tool is None:
                print(f"[ExecutionAgent] ❌ Stage '{stage_id}' not found")
                state.messages.append({
                    "role": "execution",
                    "content": f"❌ Stage '{stage_id}' not found in registry"
                })
                overall_status = "error"
                continue
            
            # 通知开始执行
            print(f"[ExecutionAgent] 🚀 开始执行 {stage_tool.name} ({stage_id})")
            state.messages.append({
                "role": "execution",
                "content": f"🚀 Starting {stage_tool.name} ({stage_id})..."
            })
            
            # 准备参数
            extra_params = state.results.get("params", {}).copy()
            extra_params.update(intent.get("params", {}))
            print(f"[ExecutionAgent] extra_params: {extra_params}")
            
            # 执行阶段
            try:
                print(f"[ExecutionAgent] 调用 stage_registry.execute({stage_id}, {job_id}, {prefix})")
                # stage_registry.execute 现在已经是 async，直接 await
                result = await self.stage_registry.execute(
                    stage_id=stage_id,
                    job_id=job_id,
                    prefix=prefix,
                    extra_params=extra_params
                )
                print(f"[ExecutionAgent] ✅ 执行完成，结果: {result}")
                
                execution_results.append({
                    "stage": stage_id,
                    "result": result
                })
                
                if result.get("status") in ["success", "partial_success"]:
                    state.messages.append({
                        "role": "execution",
                        "content": f"✅ {stage_tool.name} completed successfully"
                    })
                else:
                    # 提取错误信息（优先使用error字段，否则使用message字段）
                    error_msg = result.get('error') or result.get('message', 'Unknown error')
                    
                    # 从日志文件读取详细错误信息
                    stderr = result.get('stderr', '')
                    stdout = result.get('stdout', '')
                    
                    # 如果stderr为空，尝试从日志文件读取
                    if not stderr and result.get('manifest_path'):
                        try:
                            from pathlib import Path
                            manifest_path = Path(result['manifest_path'])
                            logs_dir = manifest_path.parent.parent / "logs"
                            if logs_dir.exists():
                                import glob
                                log_files = glob.glob(str(logs_dir / f"*_{stage_id}.log"))
                                if log_files:
                                    latest_log = max(log_files, key=lambda x: Path(x).stat().st_mtime)
                                    with open(latest_log, 'r', encoding='utf-8', errors='ignore') as f:
                                        log_content = f.read()
                                        stderr = log_content[-2000:]  # 最后2000字符
                        except Exception as e:
                            print(f"[ExecutionAgent] 读取日志文件失败: {e}")
                    
                    state.messages.append({
                        "role": "execution",
                        "content": f"❌ {stage_tool.name} failed: {error_msg}"
                    })
                    overall_status = "error"
                    # 更新最后一个execution_result，添加错误详情
                    if execution_results:
                        execution_results[-1]["error_details"] = {
                            "error": error_msg,
                            "stderr": stderr,
                            "stdout": stdout,
                            "message": result.get('message', '')
                        }
                    break
                    
            except Exception as e:
                execution_results.append({
                    "stage": stage_id,
                    "result": {"status": "error", "error": str(e)}
                })
                state.messages.append({
                    "role": "execution",
                    "content": f"❌ Exception in {stage_tool.name}: {str(e)}"
                })
                overall_status = "error"
                break
        
        # 更新状态 - 保存到AgentState.results（供其他Agent使用）
        state.results["execution_results"] = execution_results
        state.results["execution_status"] = overall_status
        if overall_status == "error":
            for item in execution_results:
                result = item.get("result", {}) if isinstance(item, dict) else {}
                error_msg = result.get("error") or result.get("message")
                if error_msg:
                    state.results["execution_error"] = error_msg
                    break
        
        return state
    
    async def execute_single_stage(self, state: AgentState, stage_id: str) -> dict:
        """
        执行单个分析阶段
        
        Args:
            state: 当前Agent状态
            stage_id: 阶段ID
        
        Returns:
            执行结果字典
        """
        if self.stage_registry is None:
            return {"status": "error", "error": "Stage registry not initialized"}
        
        job_id = state.session_id or state.results.get("job_id", "default_job")
        prefix = state.results.get("prefix", "")
        params = state.results.get("params", {})
        
        # stage_registry.execute 现在已经是 async，直接 await
        return await self.stage_registry.execute(
            stage_id=stage_id,
            job_id=job_id,
            prefix=prefix,
            extra_params=params
        )
    
    def get_available_stages(self) -> List[str]:
        """获取可用的分析阶段列表"""
        if self.stage_registry is None:
            return []
        return [s.id for s in self.stage_registry.list_all()]
