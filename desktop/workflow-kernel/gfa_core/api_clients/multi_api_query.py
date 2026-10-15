"""
多 API 并行查询模块

参考 ClawBio 的 gwas-lookup 模式
同时查询多个数据库，合并结果
"""

import concurrent.futures
import time
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime

from .plantcare_client import PlantCAREClient
from .pfam_client import PfamClient
from .ncbi_client import NCBIClient


@dataclass
class APIResult:
    """API 查询结果"""
    source: str
    status: str  # 'success', 'error', 'timeout'
    data: Any
    error_message: Optional[str] = None
    response_time: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class MultiAPIQuery:
    """
    多 API 并行查询管理器
    
    参考 ClawBio gwas-lookup 的并行查询模式
    同时查询多个数据库，统一返回结果
    """
    
    def __init__(self, max_workers: int = 5, timeout: float = 30.0):
        """
        初始化
        
        Args:
            max_workers: 最大并行线程数
            timeout: 每个查询的超时时间（秒）
        """
        self.max_workers = max_workers
        self.timeout = timeout
        
        # 初始化客户端
        self.clients = {
            'plantcare': PlantCAREClient(),
            'pfam': PfamClient(),
            'ncbi': NCBIClient(),
        }
        
    def query_all(self, query_type: str, query_params: Dict[str, Any]) -> Dict[str, APIResult]:
        """
        并行查询所有相关 API
        
        Args:
            query_type: 查询类型（'gene', 'protein', 'promoter'）
            query_params: 查询参数
            
        Returns:
            各 API 的查询结果
        """
        # 根据查询类型选择要调用的 API
        api_calls = self._get_api_calls(query_type, query_params)
        
        results = {}
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交所有任务
            future_to_api = {
                executor.submit(self._execute_with_timeout, api_name, func, args): api_name
                for api_name, (func, args) in api_calls.items()
            }
            
            # 收集结果
            for future in concurrent.futures.as_completed(future_to_api):
                api_name = future_to_api[future]
                try:
                    result = future.result(timeout=self.timeout)
                    results[api_name] = result
                except concurrent.futures.TimeoutError:
                    results[api_name] = APIResult(
                        source=api_name,
                        status='timeout',
                        data=None,
                        error_message=f'Query timed out after {self.timeout}s'
                    )
                except Exception as e:
                    results[api_name] = APIResult(
                        source=api_name,
                        status='error',
                        data=None,
                        error_message=str(e)
                    )
        
        return results
    
    def _get_api_calls(self, query_type: str, params: Dict[str, Any]) -> Dict[str, tuple]:
        """
        获取 API 调用配置
        
        Args:
            query_type: 查询类型
            params: 查询参数
            
        Returns:
            API 调用字典 {api_name: (function, args)}
        """
        calls = {}
        
        if query_type == 'promoter':
            # 启动子分析：查询 PlantCARE
            if 'sequence' in params:
                calls['plantcare'] = (
                    self.clients['plantcare'].analyze_promoter,
                    (params['sequence'],)
                )
        
        elif query_type == 'protein':
            # 蛋白质分析：查询 Pfam, NCBI
            if 'protein_id' in params:
                calls['pfam'] = (
                    self.clients['pfam'].search_protein,
                    (params['protein_id'],)
                )
            
            if 'gene_symbol' in params:
                calls['ncbi'] = (
                    self.clients['ncbi'].search_gene,
                    (params['gene_symbol'], params.get('organism', 'plants'))
                )
        
        elif query_type == 'gene':
            # 基因分析：查询 NCBI
            if 'gene_symbol' in params:
                calls['ncbi'] = (
                    self.clients['ncbi'].search_gene,
                    (params['gene_symbol'], params.get('organism', 'plants'))
                )
            
            if 'protein_id' in params:
                calls['pfam'] = (
                    self.clients['pfam'].search_protein,
                    (params['protein_id'],)
                )
        
        elif query_type == 'domain':
            # 结构域分析：查询 Pfam
            if 'pfam_acc' in params:
                calls['pfam'] = (
                    self.clients['pfam'].get_family_info,
                    (params['pfam_acc'],)
                )
        
        return calls
    
    def _execute_with_timeout(self, api_name: str, func: Callable, args: tuple) -> APIResult:
        """
        执行单个 API 查询（带计时）
        
        Args:
            api_name: API 名称
            func: 要执行的函数
            args: 函数参数
            
        Returns:
            API 结果
        """
        start_time = time.time()
        
        try:
            data = func(*args)
            response_time = time.time() - start_time
            
            return APIResult(
                source=api_name,
                status='success',
                data=data,
                response_time=response_time
            )
            
        except Exception as e:
            response_time = time.time() - start_time
            
            return APIResult(
                source=api_name,
                status='error',
                data=None,
                error_message=str(e),
                response_time=response_time
            )
    
    def generate_unified_report(self, results: Dict[str, APIResult], 
                                query_type: str, query: str) -> Dict[str, Any]:
        """
        生成统一报告
        
        Args:
            results: 各 API 的查询结果
            query_type: 查询类型
            query: 查询内容
            
        Returns:
            统一格式的报告
        """
        report = {
            'query_type': query_type,
            'query': query,
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'total_apis': len(results),
                'successful': sum(1 for r in results.values() if r.status == 'success'),
                'failed': sum(1 for r in results.values() if r.status == 'error'),
                'timeout': sum(1 for r in results.values() if r.status == 'timeout'),
            },
            'results_by_source': {},
            'merged_data': {},
        }
        
        # 按来源组织结果
        for api_name, result in results.items():
            report['results_by_source'][api_name] = {
                'status': result.status,
                'response_time': result.response_time,
                'data': result.data,
                'error': result.error_message,
            }
        
        # 合并数据（根据查询类型）
        if query_type == 'promoter':
            report['merged_data'] = self._merge_promoter_results(results)
        elif query_type == 'protein':
            report['merged_data'] = self._merge_protein_results(results)
        elif query_type == 'gene':
            report['merged_data'] = self._merge_gene_results(results)
        
        return report
    
    def _merge_promoter_results(self, results: Dict[str, APIResult]) -> Dict[str, Any]:
        """合并启动子分析结果"""
        merged = {
            'cis_elements': [],
            'element_categories': {},
            'core_promoter': [],
            'regulatory_elements': [],
        }
        
        if 'plantcare' in results and results['plantcare'].status == 'success':
            data = results['plantcare'].data
            merged['cis_elements'] = data.get('elements_by_category', {})
            merged['element_categories'] = data.get('categories', {})
            merged['core_promoter'] = data.get('core_promoter_elements', [])
            merged['regulatory_elements'] = data.get('regulatory_elements', [])
        
        return merged
    
    def _merge_protein_results(self, results: Dict[str, APIResult]) -> Dict[str, Any]:
        """合并蛋白质分析结果"""
        merged = {
            'domains': [],
            'families': [],
            'gene_info': None,
        }
        
        if 'pfam' in results and results['pfam'].status == 'success':
            data = results['pfam'].data
            if isinstance(data, list):
                merged['domains'] = [d.name for d in data]
                merged['families'] = [d.accession for d in data]
        
        if 'ncbi' in results and results['ncbi'].status == 'success':
            data = results['ncbi'].data
            if isinstance(data, list) and len(data) > 0:
                merged['gene_info'] = {
                    'symbol': data[0].symbol,
                    'description': data[0].description,
                    'chromosome': data[0].chromosome,
                }
        
        return merged
    
    def _merge_gene_results(self, results: Dict[str, APIResult]) -> Dict[str, Any]:
        """合并基因分析结果"""
        merged = {
            'gene_info': None,
            'protein_domains': [],
        }
        
        if 'ncbi' in results and results['ncbi'].status == 'success':
            data = results['ncbi'].data
            if isinstance(data, list) and len(data) > 0:
                merged['gene_info'] = {
                    'gene_id': data[0].gene_id,
                    'symbol': data[0].symbol,
                    'description': data[0].description,
                    'organism': data[0].organism,
                    'chromosome': data[0].chromosome,
                    'location': data[0].location,
                }
        
        if 'pfam' in results and results['pfam'].status == 'success':
            data = results['pfam'].data
            if isinstance(data, list):
                merged['protein_domains'] = [
                    {'name': d.name, 'accession': d.accession}
                    for d in data
                ]
        
        return merged


# 使用示例
if __name__ == "__main__":
    query_manager = MultiAPIQuery()
    
    # 示例 1: 启动子分析
    print("=" * 60)
    print("示例 1: 启动子分析")
    print("=" * 60)
    
    promoter_seq = "ATGCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCG"
    
    results = query_manager.query_all('promoter', {'sequence': promoter_seq})
    report = query_manager.generate_unified_report(results, 'promoter', promoter_seq[:50])
    
    print(f"查询结果:")
    print(f"  成功: {report['summary']['successful']}")
    print(f"  失败: {report['summary']['failed']}")
    print(f"  超时: {report['summary']['timeout']}")
    
    # 示例 2: 基因查询
    print("\n" + "=" * 60)
    print("示例 2: 基因查询")
    print("=" * 60)
    
    results = query_manager.query_all('gene', {
        'gene_symbol': 'SPL',
        'organism': 'Arabidopsis'
    })
    
    print(f"查询结果:")
    for api_name, result in results.items():
        print(f"  {api_name}: {result.status} ({result.response_time:.2f}s)")
