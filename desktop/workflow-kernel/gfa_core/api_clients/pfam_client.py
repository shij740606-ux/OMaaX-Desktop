"""
Pfam API 客户端

Pfam 是一个蛋白质家族数据库，基于隐马尔可夫模型 (HMM)
URL: https://pfam.xfam.org/
API: https://xfam.wordpress.com/2020/07/08/pfam-33-1-is-available/
"""

import requests
import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass


@dataclass
class PfamEntry:
    """Pfam 条目"""
    accession: str
    name: str
    description: str
    clan: Optional[str] = None
    domain_architecture: Optional[List[str]] = None


class PfamClient:
    """
    Pfam 数据库客户端
    
    用于查询蛋白质家族信息、HMM 模型、结构域架构
    """
    
    # Pfam REST API (通过 EBI)
    BASE_URL = "https://www.ebi.ac.uk/interpro/api"
    PFAM_URL = "https://pfam.xfam.org"
    
    def __init__(self, rate_limit: float = 0.5):
        """
        初始化客户端
        
        Args:
            rate_limit: 请求间隔（秒）
        """
        self.rate_limit = rate_limit
        self.last_request_time = 0
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'bbav20311/1.0 (Research Tool)',
            'Accept': 'application/json'
        })
        
    def _rate_limited_request(self, url: str, **kwargs) -> requests.Response:
        """带速率限制的请求"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
            
        response = self.session.get(url, **kwargs)
        self.last_request_time = time.time()
        
        return response
    
    def search_protein(self, protein_id: str) -> List[PfamEntry]:
        """
        搜索蛋白质的结构域
        
        Args:
            protein_id: 蛋白质 ID（UniProt ID）
            
        Returns:
            Pfam 条目列表
        """
        url = f"{self.BASE_URL}/entry/pfam/protein/uniprot/{protein_id}"
        
        try:
            response = self._rate_limited_request(url)
            response.raise_for_status()
            data = response.json()
            
            entries = []
            for result in data.get('results', []):
                entry = PfamEntry(
                    accession=result.get('metadata', {}).get('accession', ''),
                    name=result.get('metadata', {}).get('name', ''),
                    description=result.get('metadata', {}).get('description', ''),
                    clan=result.get('metadata', {}).get('clan', {}).get('accession') if result.get('metadata', {}).get('clan') else None
                )
                entries.append(entry)
            
            return entries
            
        except requests.exceptions.RequestException as e:
            print(f"[WARN] Pfam API request failed: {e}")
            return []
    
    def get_family_info(self, pfam_acc: str) -> Optional[Dict[str, Any]]:
        """
        获取 Pfam 家族详细信息
        
        Args:
            pfam_acc: Pfam 登录号（如 PF03110）
            
        Returns:
            家族详细信息
        """
        url = f"{self.BASE_URL}/entry/pfam/{pfam_acc}"
        
        try:
            response = self._rate_limited_request(url)
            response.raise_for_status()
            data = response.json()
            
            metadata = data.get('metadata', {})
            return {
                'accession': metadata.get('accession'),
                'name': metadata.get('name'),
                'description': metadata.get('description'),
                'clan': metadata.get('clan', {}).get('accession') if metadata.get('clan') else None,
                'clan_name': metadata.get('clan', {}).get('name') if metadata.get('clan') else None,
                'go_terms': metadata.get('go_terms', []),
                'interpro': metadata.get('interpro', {}),
                'protein_count': data.get('proteins', {}).get('total', 0),
                'structure_count': data.get('structures', {}).get('total', 0),
            }
            
        except requests.exceptions.RequestException as e:
            print(f"[WARN] Pfam API request failed: {e}")
            return None
    
    def search_by_keyword(self, keyword: str, page_size: int = 20) -> List[Dict[str, Any]]:
        """
        按关键词搜索 Pfam 家族
        
        Args:
            keyword: 搜索关键词
            page_size: 每页结果数
            
        Returns:
            搜索结果列表
        """
        url = f"{self.BASE_URL}/entry/pfam"
        params = {
            'search': keyword,
            'page_size': page_size,
        }
        
        try:
            response = self._rate_limited_request(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            results = []
            for result in data.get('results', []):
                metadata = result.get('metadata', {})
                results.append({
                    'accession': metadata.get('accession'),
                    'name': metadata.get('name'),
                    'description': metadata.get('description'),
                    'clan': metadata.get('clan', {}).get('accession') if metadata.get('clan') else None,
                })
            
            return results
            
        except requests.exceptions.RequestException as e:
            print(f"[WARN] Pfam API request failed: {e}")
            return []
    
    def get_hmm_model(self, pfam_acc: str) -> Optional[str]:
        """
        获取 HMM 模型（如果可用）
        
        Args:
            pfam_acc: Pfam 登录号
            
        Returns:
            HMM 模型字符串
        """
        # Pfam HMM 模型通常需要从 FTP 或特定端点下载
        # 这里返回下载链接
        return f"https://pfam.xfam.org/family/{pfam_acc}/hmm"
    
    def get_clan_info(self, clan_acc: str) -> Optional[Dict[str, Any]]:
        """
        获取 Clan（超家族）信息
        
        Args:
            clan_acc: Clan 登录号（如 CL0016）
            
        Returns:
            Clan 信息
        """
        url = f"{self.BASE_URL}/entry/pfam/clan/{clan_acc}"
        
        try:
            response = self._rate_limited_request(url)
            response.raise_for_status()
            data = response.json()
            
            return {
                'accession': data.get('metadata', {}).get('accession'),
                'name': data.get('metadata', {}).get('name'),
                'description': data.get('metadata', {}).get('description'),
                'member_families': [f.get('accession') for f in data.get('members', [])],
            }
            
        except requests.exceptions.RequestException as e:
            print(f"[WARN] Pfam API request failed: {e}")
            return None
    
    def analyze_protein_domains(self, protein_sequence: str) -> Dict[str, Any]:
        """
        分析蛋白质序列的结构域
        
        注意：这需要提交到 Pfam 搜索（可能需要网页界面）
        
        Args:
            protein_sequence: 蛋白质序列
            
        Returns:
            结构域分析结果
        """
        # Pfam 序列搜索通常通过网页表单或 InterProScan
        # 这里返回搜索链接
        return {
            'search_url': 'https://www.ebi.ac.uk/jdispatcher/pfa/iprscan5',
            'note': 'Use InterProScan for sequence analysis',
            'sequence_length': len(protein_sequence),
        }


# 使用示例
if __name__ == "__main__":
    client = PfamClient()
    
    # 搜索 SBP 家族
    results = client.search_by_keyword("SBP")
    print(f"Found {len(results)} Pfam families")
    
    # 获取 PF03110 (SBP) 详细信息
    info = client.get_family_info("PF03110")
    if info:
        print(f"Family: {info['name']}")
        print(f"Description: {info['description']}")
        print(f"Protein count: {info['protein_count']}")
