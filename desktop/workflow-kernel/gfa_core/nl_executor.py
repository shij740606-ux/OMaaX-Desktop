"""
自然语言执行接口 - 第一阶段：Identify Only
通过对话框输入命令执行分析
"""
import sys
import json
from pathlib import Path

# 添加项目路径
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.agents.intent_parser import parse_intent, intent_to_confirmation_message
from gfa_core.tools.identify_tool import run_identify, get_identify_params_help


class NaturalLanguageExecutor:
    """自然语言执行器"""
    
    def __init__(self):
        self.current_job_id = None
        self.last_intent = None
        self.awaiting_confirmation = False
    
    def execute(self, query: str, job_id: str = None) -> dict:
        """
        执行自然语言查询
        
        返回: {"type": "result"|"confirmation"|"error", "message": str, ...}
        """
        query = query.strip()
        
        # 处理确认/取消
        if self.awaiting_confirmation:
            if query.lower() in ["y", "yes", "确认", "是", "执行", "ok"]:
                return self._execute_confirmed()
            elif query.lower() in ["n", "no", "取消", "否", "不"]:
                self.awaiting_confirmation = False
                return {"type": "result", "message": "已取消执行"}
        
        # 解析意图
        intent = parse_intent(query)
        self.last_intent = intent
        
        # 根据任务类型处理
        if intent.task_type == "identify":
            return self._handle_identify(intent, job_id)
        elif intent.task_type == "pipeline":
            return self._handle_pipeline(intent, job_id)
        elif intent.task_type == "qa":
            return self._handle_qa(intent)
        else:
            return {
                "type": "error",
                "message": f"无法理解的请求: {query}\n"
                           f"请尝试:\n"
                           f"  - '鉴定Ft的SPL家族'\n"
                           f"  - '用PF03110鉴定SPL'\n"
                           f"  - '做全套分析'"
            }
    
    def _handle_identify(self, intent, job_id: str = None) -> dict:
        """处理鉴定请求"""
        
        # 检查必要信息
        if not intent.family_name:
            return {
                "type": "error",
                "message": "请指定基因家族名称，例如:\n"
                           "  - '鉴定Ft的SPL家族'\n"
                           "  - '鉴定MYB'"
            }
        
        # 如果没有job_id，需要用户先上传文件
        if not job_id:
            return {
                "type": "error", 
                "message": "请先上传输入文件到input目录，然后提供job_id\n"
                           "或在Gradio界面选择已上传的文件。"
            }
        
        self.current_job_id = job_id
        
        # 低置信度需要确认
        if intent.requires_confirmation:
            self.awaiting_confirmation = True
            msg = intent_to_confirmation_message(intent)
            msg += f"\n\n输入 '确认' 执行，或补充更多信息。"
            return {"type": "confirmation", "message": msg, "intent": intent}
        
        # 直接执行
        return self._execute_identify(intent, job_id)
    
    def _execute_identify(self, intent, job_id: str) -> dict:
        """执行identify"""
        
        # 构建参数
        prefix = intent.prefix or intent.family_name
        required_pfams = intent.parameters.get("pfam")
        cpu = intent.parameters.get("cpu", 8)
        evalue = intent.parameters.get("evalue", 1e-3)
        
        print(f"[执行Identify] job_id={job_id}, prefix={prefix}, pfam={required_pfams}")
        
        # 调用tool
        result = run_identify(
            job_id=job_id,
            prefix=prefix,
            required_pfams=required_pfams,
            cpu=cpu,
            dom_i_evalue_max=evalue,
        )
        
        if result.success:
            return {
                "type": "result",
                "message": result.message,
                "manifest_path": str(result.manifest_path) if result.manifest_path else None,
                "final_members": result.final_members_count,
            }
        else:
            return {
                "type": "error",
                "message": result.message,
                "error_code": result.error,
            }
    
    def _execute_confirmed(self) -> dict:
        """执行已确认的操作"""
        self.awaiting_confirmation = False
        
        if not self.last_intent:
            return {"type": "error", "message": "没有待执行的操作"}
        
        if self.last_intent.task_type == "identify":
            return self._execute_identify(self.last_intent, self.current_job_id)
        
        return {"type": "error", "message": "不支持的操作类型"}
    
    def _handle_pipeline(self, intent, job_id: str = None) -> dict:
        """处理pipeline请求（暂不实现）"""
        return {
            "type": "result",
            "message": f"Pipeline模式识别到stages: {intent.stages}\n"
                       f"当前仅支持单stage执行，完整pipeline开发中。"
        }
    
    def _handle_qa(self, intent) -> dict:
        """处理问答请求"""
        if "help" in intent.raw_query.lower() or "帮助" in intent.raw_query:
            return {
                "type": "result",
                "message": get_identify_params_help()
            }
        
        return {
            "type": "result",
            "message": f"Q&A模式: {intent.raw_query}\n"
                       f"家族: {intent.family_name}\n"
                       f"当前仅支持分析命令，问答功能开发中。"
        }


# 测试运行
if __name__ == "__main__":
    executor = NaturalLanguageExecutor()
    
    print("=== 自然语言执行接口测试 ===\n")
    print("可用命令示例:")
    print("  - '鉴定Ft的SPL家族'")
    print("  - '用PF03110鉴定SPL'")
    print("  - 'help' 查看参数说明")
    print("  - 'quit' 退出")
    print()
    
    # 找一个测试用的job_id
    input_root = PROJECT_ROOT / "input"
    test_job_id = None
    if input_root.exists():
        for d in input_root.iterdir():
            if d.is_dir():
                test_job_id = d.name
                break
    
    if test_job_id:
        print(f"使用测试job_id: {test_job_id}\n")
    else:
        print("警告: 未找到可用的job_id，请先上传文件\n")
    
    # 交互式测试
    while True:
        try:
            query = input("> ").strip()
            if query.lower() in ["quit", "exit", "退出"]:
                break
            if not query:
                continue
            
            result = executor.execute(query, test_job_id)
            print(f"\n[{result['type'].upper()}]")
            print(result['message'])
            print()
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[ERROR] {e}")
            
    print("\n再见!")
