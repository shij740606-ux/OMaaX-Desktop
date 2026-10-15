"""
PlantCARE API 客户端

PlantCARE 是一个植物顺式作用调控元件数据库
URL: http://bioinformatics.psb.ugent.be/webtools/plantcare/html/
"""

import requests
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CisElement:
    """顺式作用元件"""
    name: str
    sequence: str
    function: str
    position: Optional[int] = None
    strand: Optional[str] = None


class PlantCAREClient:
    """
    PlantCARE 数据库客户端
    
    用于查询启动子区域的顺式作用调控元件
    """
    
    BASE_URL = "http://bioinformatics.psb.ugent.be/webtools/plantcare/html/"
    
    def __init__(self, rate_limit: float = 1.0):
        """
        初始化客户端
        
        Args:
            rate_limit: 请求间隔（秒），避免过载服务器
        """
        self.rate_limit = rate_limit
        self.last_request_time = 0
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'bbav20311/1.0 (Research Tool)'
        })
        
    def _rate_limited_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """带速率限制的请求"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
            
        response = self.session.request(method, url, **kwargs)
        self.last_request_time = time.time()
        
        return response
    
    def search_elements(self, sequence: str, element_type: Optional[str] = None) -> List[CisElement]:
        """
        搜索顺式作用元件
        
        Args:
            sequence: DNA 序列（启动子区域）
            element_type: 元件类型过滤（如 "light", "hormone", "stress"）
            
        Returns:
            顺式作用元件列表
        """
        # PlantCARE 网页版没有公开 REST API
        # 这里实现基于本地数据库的查询或网页解析
        # 实际使用时可能需要本地部署 PlantCARE 数据库
        
        # 模拟返回（实际应替换为真实查询）
        elements = self._parse_sequence_for_elements(sequence)
        
        if element_type:
            elements = [e for e in elements if element_type.lower() in e.function.lower()]
            
        return elements
    
    def _parse_sequence_for_elements(self, sequence: str) -> List[CisElement]:
        """
        解析序列中的顺式作用元件
        
        基于 PlantCARE 已知元件模式进行匹配
        """
        # PlantCARE 常见元件数据库（简化版）
        element_patterns = {
            'CAAT-box': {'pattern': 'CCAAT', 'function': 'core promoter element'},
            'TATA-box': {'pattern': 'TATA', 'function': 'core promoter element'},
            'G-box': {'pattern': 'CACGTG', 'function': 'light responsive'},
            'ABRE': {'pattern': 'ACGTGG', 'function': 'abscisic acid responsive'},
            'W-box': {'pattern': 'TTGAC', 'function': 'WRKY binding site'},
            'WUN-motif': {'pattern': 'AAATTTCCT', 'function': 'wound responsive'},
            'MYB-binding': {'pattern': 'CNGTT', 'function': 'MYB transcription factor binding'},
            'MYC-binding': {'pattern': 'CACATG', 'function': 'MYC transcription factor binding'},
            'GT-1 motif': {'pattern': 'GGTTAA', 'function': 'light responsive'},
            'I-box': {'pattern': 'GATAA', 'function': 'light responsive'},
        }
        
        elements = []
        sequence_upper = sequence.upper()
        
        for name, info in element_patterns.items():
            pattern = info['pattern']
            # 支持简并碱基
            import re
            pattern_regex = pattern.replace('N', '[ATCG]')
            
            for match in re.finditer(pattern_regex, sequence_upper):
                elements.append(CisElement(
                    name=name,
                    sequence=sequence[match.start():match.end()],
                    function=info['function'],
                    position=match.start(),
                    strand='+'
                ))
        
        return elements
    
    def get_element_details(self, element_name: str) -> Optional[Dict[str, Any]]:
        """
        获取元件详细信息
        
        Args:
            element_name: 元件名称（如 "G-box"）
            
        Returns:
            元件详细信息字典
        """
        # 本地元件数据库
        element_db = {
            'CAAT-box': {
                'name': 'CAAT-box',
                'consensus': 'CCAAT',
                'function': 'Core promoter element, enhances transcription efficiency',
                'factors': ['CTF/NF1', 'CCAAT-binding factors'],
                'references': ['Mantovani, 1999'],
            },
            'TATA-box': {
                'name': 'TATA-box',
                'consensus': 'TATA(A/T)A(A/T)',
                'function': 'Core promoter element, transcription start site selection',
                'factors': ['TBP (TATA-binding protein)'],
                'references': ['Smale & Kadonaga, 2003'],
            },
            'G-box': {
                'name': 'G-box',
                'consensus': 'CACGTG',
                'function': 'Light-responsive element, also involved in ABA signaling',
                'factors': ['bZIP transcription factors', 'HY5'],
                'references': ['Menkens et al., 1995'],
            },
            'ABRE': {
                'name': 'ABRE',
                'consensus': 'ACGTGG/TC',
                'function': 'Abscisic acid responsive element',
                'factors': ['bZIP/ABF transcription factors'],
                'references': ['Nakashima & Yamaguchi-Shinozaki, 2013'],
            },
            'W-box': {
                'name': 'W-box',
                'consensus': 'TTGAC(C/T)',
                'function': 'WRKY transcription factor binding site, defense response',
                'factors': ['WRKY transcription factors'],
                'references': ['Eulgem et al., 2000'],
            },
        }
        
        return element_db.get(element_name)
    
    def analyze_promoter(self, promoter_sequence: str, upstream_length: int = 1500) -> Dict[str, Any]:
        """
        全面分析启动子区域
        
        Args:
            promoter_sequence: 启动子序列
            upstream_length: 上游长度（默认1500bp）
            
        Returns:
            分析结果字典
        """
        elements = self.search_elements(promoter_sequence)
        
        # 分类统计
        categories = {}
        for elem in elements:
            category = self._categorize_element(elem)
            if category not in categories:
                categories[category] = []
            categories[category].append(elem)
        
        return {
            'total_elements': len(elements),
            'categories': {k: len(v) for k, v in categories.items()},
            'elements_by_category': categories,
            'core_promoter_elements': [e for e in elements if 'core promoter' in e.function],
            'regulatory_elements': [e for e in elements if 'responsive' in e.function or 'binding' in e.function],
            'sequence_length': len(promoter_sequence),
        }
    
    def _categorize_element(self, element: CisElement) -> str:
        """对元件进行分类"""
        function_lower = element.function.lower()
        
        if 'core promoter' in function_lower:
            return 'core_promoter'
        elif 'light' in function_lower:
            return 'light_responsive'
        elif 'hormone' in function_lower or 'acid' in function_lower:
            return 'hormone_responsive'
        elif 'stress' in function_lower or 'wound' in function_lower:
            return 'stress_responsive'
        elif 'binding' in function_lower:
            return 'tf_binding'
        else:
            return 'other'


# 使用示例
if __name__ == "__main__":
    client = PlantCAREClient()
    
    # 测试序列

    # 测试序列
    test_seq = "ATGCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCG"
    
    # 搜索元件
    elements = client.search_elements(test_seq)
    print(f"Found {len(elements)} cis-elements")
    
    for elem in elements:
        print(f"  - {elem.name}: {elem.sequence} ({elem.function})")

