"""智能模型选择器 - 根据任务类型自动选择最优模型

阿里云百炼 Coding Plan 支持模型:
- 智谱GLM: glm-5, glm-4.7
- DeepSeek: deepseek-v4-flash
- MiniMax: minimax-m2.5

策略: 优先使用DeepSeek v15提取器模型，根据任务类型选择最优模型
"""

from enum import Enum
from typing import Optional


class TaskType(Enum):
    """任务类型"""
    ROUTER = "router"           # 意图识别/路由
    KNOWLEDGE = "knowledge"     # 知识查询/RAG
    ANALYSIS = "analysis"       # 复杂分析/推理
    CODE = "code"               # 代码生成
    SUMMARY = "summary"         # 总结/报告


class ModelSelector:
    """智能模型选择器 - 阿里云百炼 Coding Plan
    
    策略: 优先使用DeepSeek，GLM和MiniMax作为备选
    
    模型选择:
    - ROUTER: deepseek-v4-flash (速度快，准确率高)
    - KNOWLEDGE: deepseek-v4-flash (文献问答与证据总结)
    - ANALYSIS: glm-5 (复杂推理能力强)
    - CODE: minimax-m2.5 (代码生成能力强)
    - SUMMARY: deepseek-v4-flash (速度快，效果好)
    """
    
    # 百炼 Coding Plan 模型配置（仅非Qwen模型）
    MODELS = {
        # 智谱GLM系列 - 复杂推理
        "glm-5": {
            "provider": "glm",
            "model": "glm-5",
            "temperature": 0.7,
            "max_tokens": 8192,
            "strengths": ["复杂推理", "分析任务", "准确率高"],
            "speed": "medium",
        },
        "glm-4.7": {
            "provider": "glm",
            "model": "glm-4.7",
            "temperature": 0.7,
            "max_tokens": 4096,
            "strengths": ["效果均衡", "速度快"],
            "speed": "fast",
        },
        # DeepSeek系列 - 首选
        "deepseek-v4-flash": {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "temperature": 0.7,
            "max_tokens": 8192,
            "strengths": ["长上下文", "理解能力强", "支持图片理解", "速度快"],
            "speed": "fast",
        },
        # MiniMax系列 - 代码生成
        "minimax-m2.5": {
            "provider": "minimax",
            "model": "minimax-m2.5",
            "temperature": 0.7,
            "max_tokens": 4096,
            "strengths": ["代码生成", "结构化输出"],
            "speed": "medium",
        },
    }
    
    # 任务类型到模型的映射（优先DeepSeek）
    TASK_MODEL_MAPPING = {
        TaskType.ROUTER: "deepseek-v4-flash",  # 首选DeepSeek，速度快
        TaskType.KNOWLEDGE: "deepseek-v4-flash", # 首选DeepSeek，证据总结
        TaskType.ANALYSIS: "glm-5",          # GLM复杂推理
        TaskType.CODE: "minimax-m2.5",       # MiniMax代码
        TaskType.SUMMARY: "deepseek-v4-flash", # 首选DeepSeek
    }
    
    # 备选模型映射（当首选不可用时）
    FALLBACK_MAPPING = {
        "deepseek-v4-flash": "glm-4.7",
        "glm-5": "glm-4.7",
        "minimax-m2.5": "glm-4.7",
    }
    
    @classmethod
    def select_model(cls, task_type: TaskType, prefer_speed: bool = False, fallback: bool = False) -> dict:
        """根据任务类型选择最优模型
        
        Args:
            task_type: 任务类型
            prefer_speed: 是否优先速度
            fallback: 是否使用备选模型
            
        Returns:
            模型配置字典
        """
        model_key = cls.TASK_MODEL_MAPPING.get(task_type, "deepseek-v4-flash")
        
        # 如果使用备选
        if fallback and model_key in cls.FALLBACK_MAPPING:
            model_key = cls.FALLBACK_MAPPING[model_key]
        
        # 如果优先速度，降级到更快的模型
        if prefer_speed:
            speed_priority = {
                "glm-5": "glm-4.7",
            }
            model_key = speed_priority.get(model_key, model_key)
        
        return cls.MODELS[model_key].copy()
    
    @classmethod
    def select_for_query(cls, query: str) -> dict:
        """根据查询内容自动选择模型
        
        Args:
            query: 用户查询
            
        Returns:
            模型配置字典
        """
        query_lower = query.lower()
        
        # 代码关键词
        code_keywords = ["代码", "脚本", "python", "r", "编写", "生成", "programming", "script"]
        # 分析关键词
        analysis_keywords = ["分析", "鉴定", "计算", "推理", "比较", "评估", "analyze", "calculate"]
        # 知识关键词
        knowledge_keywords = ["什么是", "有哪些", "介绍", "说明", "查询", "what is", "how to"]
        
        # 判断任务类型
        if any(kw in query_lower for kw in code_keywords):
            return cls.select_model(TaskType.CODE)
        elif any(kw in query_lower for kw in analysis_keywords):
            return cls.select_model(TaskType.ANALYSIS)
        elif any(kw in query_lower for kw in knowledge_keywords):
            return cls.select_model(TaskType.KNOWLEDGE)
        else:
            return cls.select_model(TaskType.ROUTER)
    
    @classmethod
    def get_model_info(cls, model_key: str) -> Optional[dict]:
        """获取模型信息"""
        return cls.MODELS.get(model_key)
    
    @classmethod
    def list_available_models(cls) -> list:
        """列出所有可用模型"""
        return [
            {
                "key": key,
                "name": config["model"],
                "provider": config["provider"],
                "strengths": config["strengths"],
                "speed": config["speed"],
            }
            for key, config in cls.MODELS.items()
        ]
    
    @classmethod
    def list_by_provider(cls, provider: str) -> list:
        """按提供商列出模型"""
        return [
            {
                "key": key,
                "name": config["model"],
                "strengths": config["strengths"],
                "speed": config["speed"],
            }
            for key, config in cls.MODELS.items()
            if config["provider"] == provider
        ]
    
    @classmethod
    def get_recommendation(cls) -> str:
        """获取模型推荐说明"""
        return """
模型推荐策略（基于性能评比）:

1. **DeepSeek V4 Flash** - 首选模型
   - 适用: 意图识别、知识查询、总结报告
   - 优势: v15提取器同款配置、响应快、证据总结稳定
   
2. **GLM-5** - 复杂推理
   - 适用: 复杂分析、推理任务
   - 优势: 推理能力强、准确率高
   
3. **GLM-4.7** - 快速响应
   - 适用: 简单任务、备选方案
   - 优势: 速度快、效果均衡
   
4. **MiniMax-M2.5** - 代码生成
   - 适用: 代码生成、结构化输出
   - 优势: 代码能力好

**推荐配置**:
- 默认使用DeepSeek V4 Flash
- 复杂分析切换到GLM-5
- 代码生成使用MiniMax-M2.5
"""


# 便捷函数
def select_model_for_task(task_type: TaskType, prefer_speed: bool = False, fallback: bool = False) -> dict:
    """根据任务类型选择模型"""
    return ModelSelector.select_model(task_type, prefer_speed, fallback)


def select_model_for_query(query: str) -> dict:
    """根据查询选择模型"""
    return ModelSelector.select_for_query(query)
