"""
RAG API客户端模块

对接 gene-family-mas 项目的RAG服务
"""

import os
import requests
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from gfa_core.mas.dual_kb_qa import dual_kb_info, query_dual_kb_qa


# RAG API配置
RAG_API_URL = (
    os.environ.get("GENE_FAMILY_MAS_API")
    or os.environ.get("DUAL_KB_QA_BASE_URL")
    or os.environ.get("RAG_API_URL", "http://127.0.0.1:7902")
)
DEFAULT_TOP_K = 5
DEFAULT_THRESHOLD = 0.2


@dataclass
class RAGSearchResult:
    """RAG搜索结果"""
    doc_id: str
    content: str
    score: float
    metadata: Dict[str, Any] = None


class RAGRetriever:
    """RAG检索器 - 调用外部RAG API"""
    
    def __init__(self, llm_manager=None):
        self.api_url = RAG_API_URL
        self.llm_manager = llm_manager
        self._health_checked = False
        self._available = False
        
    def _check_health(self) -> bool:
        """检查RAG API是否可用"""
        if self._health_checked:
            return self._available
            
        try:
            data = dual_kb_info(timeout=5)
            self._available = bool(data)
            if self._available:
                mode = data.get("mode") or data.get("backend") or "gene_family_mas_qa"
                print(f"gene-family-mas-v2 QA API available: {mode}")
        except Exception as e:
            print(f"gene-family-mas-v2 QA API not available: {e}")
            self._available = False
            
        self._health_checked = True
        return self._available
    
    def is_available(self) -> bool:
        """检查RAG是否可用"""
        return self._check_health()
    
    def count_chunks(self) -> int:
        """Return a non-zero readiness marker for the external gene-family MAS service."""
        return 1 if self._check_health() else 0
    
    async def search(
        self, 
        query: str, 
        top_k: int = DEFAULT_TOP_K, 
        threshold: float = DEFAULT_THRESHOLD
    ) -> List[RAGSearchResult]:
        """
        搜索相关文档
        
        Args:
            query: 查询文本
            top_k: 返回结果数量
            threshold: 相似度阈值
            
        Returns:
            RAGSearchResult列表
        """
        if not self._check_health():
            print("gene-family-mas-v2 QA API not available, returning empty results")
            return []
            
        try:
            data = query_dual_kb_qa(query, top_k=top_k, use_llm=False, timeout=60)
            results = []
            
            for item in data.get("sources", []):
                if item.get("score", 0) >= threshold:
                    content = (
                        item.get("content")
                        or item.get("evidence")
                        or " -- ".join(str(item.get(key, "")) for key in ("source", "relation", "target") if item.get(key))
                    )
                    results.append(RAGSearchResult(
                        doc_id=item.get("doc_id") or item.get("paper_id") or item.get("id", ""),
                        content=content,
                        score=item.get("score", 0),
                        metadata=item
                    ))
                    
            return results
            
        except Exception as e:
            print(f"RAG search exception: {e}")
            return []
    
    async def chat(self, query: str, use_llm: bool = True, strict_mode: bool = False) -> Dict[str, Any]:
        """
        对话式检索
        
        Args:
            query: 用户问题
            use_llm: 是否使用LLM生成总结（默认True）
            strict_mode: 严格模式（默认False），为True时只返回原文
            
        Returns:
            {"response": str, "sources": List[Dict], "context": str}
        """
        if not self._check_health():
            return {
                "response": "gene-family-mas-v2 QA服务暂不可用",
                "sources": [],
                "context": ""
            }
            
        try:
            data = query_dual_kb_qa(query, top_k=DEFAULT_TOP_K, use_llm=use_llm, timeout=120)
            
            # 提取sources
            sources = []
            for s in data.get("sources", []):
                sources.append({
                    "doc_id": s.get("doc_id") or s.get("paper_id") or s.get("id", ""),
                    "score": s.get("score", 0)
                })
                
            return {
                "response": data.get("answer", ""),
                "sources": sources,
                "context": "",  # 可后续扩展
                "knowledge_graph": data.get("knowledge_graph"),
                "kg_entities": data.get("kg_entities") or [],
            }
            
        except Exception as e:
            return {
                "response": f"请求异常: {str(e)}",
                "sources": [],
                "context": ""
            }
    
    def get_context(
        self, 
        results: List[RAGSearchResult], 
        max_length: int = 2000
    ) -> str:
        """
        将搜索结果转换为上下文文本
        
        Args:
            results: 搜索结果列表
            max_length: 最大长度
            
        Returns:
            上下文字符串
        """
        context_parts = []
        total_length = 0
        
        for result in results:
            part = f"[{result.doc_id}] {result.content}"
            if total_length + len(part) > max_length:
                break
            context_parts.append(part)
            total_length += len(part)
            
        return "\n\n".join(context_parts)
    
    async def aindex_pdf_directory(self, directory: str) -> int:
        """
        注意: 本地索引功能已弃用，使用远程RAG API
        
        Args:
            directory: PDF目录
            
        Returns:
            chunk数量
        """
        # 远程RAG API已经有索引，不需要重新索引
        return self.count_chunks()


# 快捷函数
def get_rag_retriever() -> RAGRetriever:
    """获取RAG检索器实例"""
    return RAGRetriever()
