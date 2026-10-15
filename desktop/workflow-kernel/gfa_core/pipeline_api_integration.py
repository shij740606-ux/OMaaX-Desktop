"""
Pipeline API 集成模块

简化版 API 增强集成，直接在 pipeline 中使用
"""

from pathlib import Path
from typing import Dict, List, Optional, Any
import json

from .api_clients import MultiAPIQuery, PfamClient, NCBIClient
from .enhanced_analysis import DomainResult


class PipelineAPIEnhancer:
    """
    Pipeline API 增强器
    
    简化版集成，用于 pipeline_backend.py
    """
    
    def __init__(self, enable_api: bool = True):
        """
        初始化
        
        Args:
            enable_api: 是否启用 API 增强
        """
        self.enable_api = enable_api
        
        if enable_api:
            self.query_manager = MultiAPIQuery()
            self.pfam_client = PfamClient()
            self.ncbi_client = NCBIClient()
    
    def enhance_stage_result(self, stage: str, result: Dict, 
                            prefix: str, **kwargs) -> Dict:
        """
        增强阶段结果
        
        Args:
            stage: 分析阶段名称
            result: 本地分析结果
            prefix: 前缀
            **kwargs: 额外参数
            
        Returns:
            增强后的结果
        """
        if not self.enable_api:
            return result
        
        try:
            if stage == 'domain':
                return self._enhance_domain_result(result, prefix)
            elif stage == 'identify':
                return self._enhance_identify_result(result, prefix)
            elif stage == 'phylogeny':
                return self._enhance_phylogeny_result(result, prefix)
            else:
                return result
        except Exception as e:
            # API 失败不影响本地结果
            print(f"[WARN] API enhancement failed for {stage}: {e}")
            return result
    
    def _enhance_domain_result(self, result: Dict, prefix: str) -> Dict:
        """增强 Domain 分析结果"""
        enhanced = result.copy()
        
        # 获取 Pfam 注释
        pfam_accs = []
        for domain in result.get('domains', []):
            pfam_acc = domain.get('pfam_acc', '')
            if pfam_acc:
                pfam_accs.append(pfam_acc)
        
        if pfam_accs:
            enhanced['pfam_annotations'] = {}
            for acc in pfam_accs[:3]:  # 只增强前3个，避免过多请求
                try:
                    info = self.pfam_client.get_family_info(acc)
                    if info:
                        enhanced['pfam_annotations'][acc] = {
                            'latest_description': info.get('description'),
                            'protein_count': info.get('protein_count'),
                            'clan': info.get('clan'),
                            'go_terms': info.get('go_terms', [])[:5],  # 前5个GO
                        }
                except:
                    pass
        
        return enhanced
    
    def _enhance_identify_result(self, result: Dict, prefix: str) -> Dict:
        """增强 Identify 结果"""
        enhanced = result.copy()
        
        # 查询基因家族信息
        try:
            # 从 prefix 提取基因家族名称
            family_name = prefix.split('.')[1] if '.' in prefix else prefix
            
            # 查询 NCBI
            genes = self.ncbi_client.search_gene(family_name, 'Arabidopsis')
            if genes:
                enhanced['ncbi_annotations'] = {
                    'gene_count': len(genes),
                    'representative_genes': [
                        {
                            'symbol': g.symbol,
                            'description': g.description,
                            'chromosome': g.chromosome,
                        }
                        for g in genes[:5]
                    ]
                }
        except:
            pass
        
        return enhanced
    
    def _enhance_phylogeny_result(self, result: Dict, prefix: str) -> Dict:
        """增强 Phylogeny 结果"""
        enhanced = result.copy()
        
        # 可以添加同源基因查询等
        # 这里简化处理
        
        return enhanced
    
    def generate_final_report(self, job_id: str, prefix: str, 
                             stage_results: Dict[str, Dict]) -> Dict:
        """
        生成最终增强报告
        
        Args:
            job_id: 任务 ID
            prefix: 前缀
            stage_results: 各阶段结果
            
        Returns:
            完整报告
        """
        report = {
            'job_id': job_id,
            'prefix': prefix,
            'api_enhanced': self.enable_api,
            'stages': {},
            'summary': {
                'total_stages': len(stage_results),
                'completed_stages': list(stage_results.keys()),
            }
        }
        
        # 合并各阶段结果
        for stage, result in stage_results.items():
            report['stages'][stage] = result
        
        # API 增强汇总
        if self.enable_api:
            report['api_enhancements'] = {
                'enabled': True,
                'sources': ['Pfam', 'NCBI', 'Ensembl Plants'],
            }
        
        return report


# 在 pipeline_backend.py 中使用示例
"""
# 在 run_stage 函数中添加：

from gfa_core.pipeline_api_integration import PipelineAPIEnhancer

# 初始化（放在函数开头）
api_enhancer = PipelineAPIEnhancer(enable_api=True)

# 在分析完成后增强结果
if stage == "domain":
    # 本地分析
    result = run_local_domain_analysis(...)
    
    # API 增强
    result = api_enhancer.enhance_stage_result('domain', result, prefix)
    
    return result
"""


# 简化的使用函数
def enhance_analysis_result(stage: str, local_result: Dict, 
                           prefix: str, enable_api: bool = True) -> Dict:
    """
    简化函数：增强分析结果
    
    Args:
        stage: 阶段名称
        local_result: 本地结果
        prefix: 前缀
        enable_api: 是否启用 API
        
    Returns:
        增强后的结果
    """
    enhancer = PipelineAPIEnhancer(enable_api=enable_api)
    return enhancer.enhance_stage_result(stage, local_result, prefix)


# 使用示例
if __name__ == "__main__":
    print("Pipeline API Integration Demo")
    print("=" * 60)
    
    # 示例 1: 增强 Domain 结果
    print("\n示例 1: 增强 Domain 结果")
    local_domain_result = {
        'domains': [
            {'pfam_acc': 'PF03110', 'name': 'SBP', 'score': 250.5},
            {'pfam_acc': 'PF00498', 'name': 'BPL', 'score': 180.3},
        ]
    }
    
    enhanced = enhance_analysis_result('domain', local_domain_result, 'At.WRKY', enable_api=True)
    print(f"  本地结果: {len(enhanced['domains'])} domains")
    print(f"  API 增强: {'pfam_annotations' in enhanced}")
    
    # 示例 2: 禁用 API
    print("\n示例 2: 禁用 API")
    local_only = enhance_analysis_result('domain', local_domain_result, 'At.WRKY', enable_api=False)
    print(f"  API 增强: {'pfam_annotations' in local_only}")
    
    print("\n" + "=" * 60)
    print("完成！")
