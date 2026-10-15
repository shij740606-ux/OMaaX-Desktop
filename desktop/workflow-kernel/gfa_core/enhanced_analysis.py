"""
增强分析模块

本地优先 + API 增强的混合模式

设计原则:
1. 本地工具优先执行核心分析
2. API 用于补充最新信息和验证
3. API 失败不影响本地结果
4. 可配置是否启用 API 增强
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

from .api_clients import (
    MultiAPIQuery,
    PfamClient,
    NCBIClient,
    EnsemblPlantsClient,
)


@dataclass
class DomainResult:
    """Domain 分析结果（本地 + API 增强）"""
    # 本地分析结果
    pfam_acc: str
    pfam_name: str
    description: str
    score: float
    evalue: float
    start: int
    end: int
    
    # API 增强信息（可选）
    latest_description: Optional[str] = None
    protein_count: Optional[int] = None
    clan: Optional[str] = None
    clan_name: Optional[str] = None
    go_terms: Optional[List[str]] = None
    interpro: Optional[Dict] = None
    
    # 数据来源标记
    data_source: str = "local"  # "local" | "api" | "merged"


@dataclass
class GeneInfo:
    """基因信息（API 查询）"""
    gene_id: str
    symbol: str
    description: str
    organism: str
    chromosome: Optional[str] = None
    location: Optional[str] = None
    synonyms: Optional[List[str]] = None
    
    # 表达数据
    expression_data: Optional[List[Dict]] = None
    
    # 同源基因
    orthologs: Optional[List[Dict]] = None


class EnhancedDomainAnalyzer:
    """
    增强版 Domain 分析器
    
    本地 Pfam HMMER 扫描 + API 补充最新注释
    """
    
    def __init__(self, local_pfam_path: str, enable_api: bool = True):
        """
        初始化分析器
        
        Args:
            local_pfam_path: 本地 Pfam HMM 文件路径
            enable_api: 是否启用 API 增强
        """
        self.local_pfam_path = Path(local_pfam_path)
        self.enable_api = enable_api
        
        if enable_api:
            self.pfam_client = PfamClient()
            self.query_manager = MultiAPIQuery()
    
    def analyze(self, protein_fasta: str, enhance: bool = True) -> List[DomainResult]:
        """
        分析蛋白质结构域
        
        Args:
            protein_fasta: 蛋白质 FASTA 文件路径
            enhance: 是否用 API 增强结果
            
        Returns:
            DomainResult 列表
        """
        # 1. 本地 HMMER 扫描（核心功能，必须成功）
        local_results = self._run_local_hmmscan(protein_fasta)
        
        if not enhance or not self.enable_api:
            return local_results
        
        # 2. API 增强（可选，失败不影响结果）
        enhanced_results = []
        for domain in local_results:
            try:
                enhanced = self._enhance_with_api(domain)
                enhanced_results.append(enhanced)
            except Exception as e:
                # API 失败，保留本地结果
                print(f"[WARN] API enhancement failed for {domain.pfam_acc}: {e}")
                domain.data_source = "local"
                enhanced_results.append(domain)
        
        return enhanced_results
    
    def _run_local_hmmscan(self, fasta_file: str) -> List[DomainResult]:
        """
        运行本地 HMMER hmmscan
        
        实际实现应调用 subprocess 运行 hmmscan
        这里返回模拟结果
        """
        import subprocess
        
        output_file = Path(fasta_file).with_suffix('.domtblout')
        
        cmd = [
            'hmmscan',
            '--domtblout', str(output_file),
            '--noali',
            '-E', '1e-5',
            str(self.local_pfam_path),
            fasta_file
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return self._parse_domtblout(output_file)
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] HMMER failed: {e}")
            return []
        except FileNotFoundError:
            print("[WARN] hmmscan not found, returning empty results")
            return []
    
    def _parse_domtblout(self, domtblout_file: Path) -> List[DomainResult]:
        """解析 HMMER domtblout 文件"""
        results = []
        
        if not domtblout_file.exists():
            return results
        
        with open(domtblout_file) as f:
            for line in f:
                if line.startswith('#'):
                    continue
                
                parts = line.split()
                if len(parts) < 22:
                    continue
                
                # domtblout 格式解析
                try:
                    result = DomainResult(
                        pfam_acc=parts[1],
                        pfam_name=parts[0],
                        description=' '.join(parts[22:]) if len(parts) > 22 else '',
                        score=float(parts[13]),
                        evalue=float(parts[12]),
                        start=int(parts[19]),
                        end=int(parts[20]),
                        data_source="local"
                    )
                    results.append(result)
                except (ValueError, IndexError):
                    continue
        
        return results
    
    def _enhance_with_api(self, domain: DomainResult) -> DomainResult:
        """
        用 API 增强 Domain 结果
        
        Args:
            domain: 本地分析结果
            
        Returns:
            增强后的结果
        """
        # 查询 Pfam API
        api_info = self.pfam_client.get_family_info(domain.pfam_acc)
        
        if api_info:
            domain.latest_description = api_info.get('description')
            domain.protein_count = api_info.get('protein_count')
            domain.clan = api_info.get('clan')
            domain.clan_name = api_info.get('clan_name')
            domain.go_terms = api_info.get('go_terms', [])
            domain.interpro = api_info.get('interpro')
            domain.data_source = "merged"
        
        return domain
    
    def generate_report(self, results: List[DomainResult], output_file: str):
        """
        生成增强报告
        
        Args:
            results: DomainResult 列表
            output_file: 输出文件路径
        """
        report = {
            'summary': {
                'total_domains': len(results),
                'local_only': sum(1 for r in results if r.data_source == 'local'),
                'api_enhanced': sum(1 for r in results if r.data_source == 'merged'),
            },
            'domains': [asdict(r) for r in results],
        }
        
        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"[INFO] Report saved to {output_file}")


class EnhancedReportGenerator:
    """
    增强报告生成器
    
    本地分析结果 + API 补充基因信息
    """
    
    def __init__(self, enable_api: bool = True):
        self.enable_api = enable_api
        
        if enable_api:
            self.ncbi_client = NCBIClient()
            self.ensembl_client = EnsemblPlantsClient()
            self.query_manager = MultiAPIQuery()
    
    def enhance_report(self, local_report: Dict, gene_symbols: List[str]) -> Dict:
        """
        增强报告内容
        
        Args:
            local_report: 本地分析结果
            gene_symbols: 基因符号列表
            
        Returns:
            增强后的报告
        """
        if not self.enable_api:
            return local_report
        
        enhanced = local_report.copy()
        enhanced['gene_annotations'] = {}
        
        for symbol in gene_symbols:
            try:
                # 查询基因信息
                gene_info = self._query_gene_info(symbol)
                if gene_info:
                    enhanced['gene_annotations'][symbol] = gene_info
            except Exception as e:
                print(f"[WARN] Failed to query gene {symbol}: {e}")
        
        return enhanced
    
    def _query_gene_info(self, symbol: str) -> Optional[Dict]:
        """查询基因详细信息"""
        results = self.query_manager.query_all('gene', {
            'gene_symbol': symbol,
            'organism': 'Arabidopsis'
        })
        
        # 合并结果
        gene_info = {
            'symbol': symbol,
            'ncbi': None,
            'ensembl': None,
        }
        
        # 从 NCBI 获取信息
        if 'ncbi' in results and results['ncbi'].status == 'success':
            data = results['ncbi'].data
            if isinstance(data, list) and len(data) > 0:
                gene = data[0]
                gene_info['ncbi'] = {
                    'gene_id': gene.gene_id,
                    'description': gene.description,
                    'chromosome': gene.chromosome,
                    'location': gene.location,
                }
        
        return gene_info if gene_info['ncbi'] or gene_info['ensembl'] else None


# 使用示例
if __name__ == "__main__":
    # 示例 1: Domain 分析（本地 + API 增强）
    print("=" * 60)
    print("示例 1: Domain 分析（本地 + API 增强）")
    print("=" * 60)
    
    analyzer = EnhancedDomainAnalyzer(
        local_pfam_path=str(Path(__file__).resolve().parents[1] / "data" / "hmm" / "Pfam-A.hmm"),
        enable_api=True
    )
    
    # 分析蛋白质
    # results = analyzer.analyze("/path/to/protein.fa", enhance=True)
    # analyzer.generate_report(results, "domain_report.json")
    
    print("\n配置:")
    print(f"  本地 Pfam: {analyzer.local_pfam_path}")
    print(f"  API 增强: {'启用' if analyzer.enable_api else '禁用'}")
    
    # 示例 2: 报告增强
    print("\n" + "=" * 60)
    print("示例 2: 报告增强")
    print("=" * 60)
    
    generator = EnhancedReportGenerator(enable_api=True)
    
    # 假设的本地报告
    local_report = {
        'job_id': 'test_001',
        'stages': ['identify', 'phylogeny', 'domain'],
        'results': {}
    }
    
    # 增强报告
    # enhanced = generator.enhance_report(local_report, ['SPL1', 'SPL2', 'SPL3'])
    
    print("\n配置:")
    print(f"  API 增强: {'启用' if generator.enable_api else '禁用'}")
