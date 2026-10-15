"""
NCBI API 客户端

NCBI E-utilities API
URL: https://www.ncbi.nlm.nih.gov/
API Docs: https://www.ncbi.nlm.nih.gov/books/NBK25501/
"""

import requests
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Any
from dataclasses import dataclass


@dataclass
class NCBIGene:
    """NCBI 基因信息"""
    gene_id: str
    symbol: str
    description: str
    organism: str
    chromosome: Optional[str] = None
    location: Optional[str] = None


class NCBIClient:
    """
    NCBI E-utilities API 客户端
    
    用于查询基因、蛋白质、序列等信息
    """
    
    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    
    def __init__(self, api_key: Optional[str] = None, rate_limit: float = 0.34):
        """
        初始化客户端
        
        Args:
            api_key: NCBI API 密钥（可提高速率限制到 10 requests/second）
            rate_limit: 请求间隔（秒），无 key 时为 3 requests/second
        """
        self.api_key = api_key
        self.rate_limit = rate_limit if api_key else 0.34
        self.last_request_time = 0
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'bbav20311/1.0 (Research Tool)'
        })
        
    def _rate_limited_request(self, url: str, params: Dict = None) -> requests.Response:
        """带速率限制的请求"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        
        # 添加 API key
        if params is None:
            params = {}
        if self.api_key:
            params['api_key'] = self.api_key
            
        response = self.session.get(url, params=params)
        self.last_request_time = time.time()
        
        return response
    
    def search(self, db: str, term: str, retmax: int = 20) -> List[str]:
        """
        搜索 NCBI 数据库
        
        Args:
            db: 数据库名称（gene, protein, nucleotide, etc.）
            term: 搜索词
            retmax: 返回结果数
            
        Returns:
            ID 列表
        """
        url = f"{self.BASE_URL}/esearch.fcgi"
        params = {
            'db': db,
            'term': term,
            'retmode': 'json',
            'retmax': retmax,
        }
        
        try:
            response = self._rate_limited_request(url, params)
            response.raise_for_status()
            data = response.json()
            
            return data.get('esearchresult', {}).get('idlist', [])
            
        except requests.exceptions.RequestException as e:
            print(f"[WARN] NCBI API request failed: {e}")
            return []
    
    def fetch(self, db: str, ids: List[str], rettype: str = 'json') -> Dict[str, Any]:
        """
        获取记录详情
        
        Args:
            db: 数据库名称
            ids: ID 列表
            rettype: 返回类型
            
        Returns:
            记录详情
        """
        url = f"{self.BASE_URL}/efetch.fcgi"
        params = {
            'db': db,
            'id': ','.join(ids),
            'retmode': rettype,
        }
        
        try:
            response = self._rate_limited_request(url, params)
            response.raise_for_status()
            
            if rettype == 'json':
                return response.json()
            else:
                return {'text': response.text}
            
        except requests.exceptions.RequestException as e:
            print(f"[WARN] NCBI API request failed: {e}")
            return {}
    
    def summary(self, db: str, ids: List[str]) -> List[Dict[str, Any]]:
        """
        获取记录摘要
        
        Args:
            db: 数据库名称
            ids: ID 列表
            
        Returns:
            摘要列表
        """
        url = f"{self.BASE_URL}/esummary.fcgi"
        params = {
            'db': db,
            'id': ','.join(ids),
            'retmode': 'json',
        }
        
        try:
            response = self._rate_limited_request(url, params)
            response.raise_for_status()
            data = response.json()
            
            result = data.get('result', {})
            uids = result.get('uids', [])
            
            summaries = []
            for uid in uids:
                summaries.append(result.get(uid, {}))
            
            return summaries
            
        except requests.exceptions.RequestException as e:
            print(f"[WARN] NCBI API request failed: {e}")
            return []
    
    def search_gene(self, symbol: str, organism: str = "plants") -> List[NCBIGene]:
        """
        搜索基因
        
        Args:
            symbol: 基因符号
            organism: 物种
            
        Returns:
            基因信息列表
        """
        term = f"{symbol}[Gene] AND {organism}[Organism]"
        gene_ids = self.search('gene', term)
        
        if not gene_ids:
            return []
        
        summaries = self.summary('gene', gene_ids)
        
        genes = []
        for summary in summaries:
            gene = NCBIGene(
                gene_id=str(summary.get('uid', '')),
                symbol=summary.get('name', ''),
                description=summary.get('description', ''),
                organism=summary.get('organism', ''),
                chromosome=summary.get('chromosome', ''),
                location=summary.get('maploc', '')
            )
            genes.append(gene)
        
        return genes
    
    def get_sequence(self, db: str, id: str, seq_start: Optional[int] = None, 
                     seq_stop: Optional[int] = None) -> str:
        """
        获取序列
        
        Args:
            db: 数据库（nuccore, protein）
            id: 序列 ID
            seq_start: 起始位置
            seq_stop: 终止位置
            
        Returns:
            FASTA 格式序列
        """
        url = f"{self.BASE_URL}/efetch.fcgi"
        params = {
            'db': db,
            'id': id,
            'rettype': 'fasta',
            'retmode': 'text',
        }
        
        if seq_start and seq_stop:
            params['seq_start'] = seq_start
            params['seq_stop'] = seq_stop
        
        try:
            response = self._rate_limited_request(url, params)
            response.raise_for_status()
            return response.text
            
        except requests.exceptions.RequestException as e:
            print(f"[WARN] NCBI API request failed: {e}")
            return ""
    
    def link(self, dbfrom: str, dbto: str, ids: List[str]) -> Dict[str, List[str]]:
        """
        获取数据库间链接
        
        Args:
            dbfrom: 源数据库
            dbto: 目标数据库
            ids: ID 列表
            
        Returns:
            链接关系
        """
        url = f"{self.BASE_URL}/elink.fcgi"
        params = {
            'dbfrom': dbfrom,
            'db': dbto,
            'id': ','.join(ids),
            'retmode': 'json',
        }
        
        try:
            response = self._rate_limited_request(url, params)
            response.raise_for_status()
            data = response.json()
            
            links = {}
            for linkset in data.get('linksets', []):
                for linksetdb in linkset.get('linksetdbs', []):
                    dbname = linksetdb.get('dbto', '')
                    links[dbname] = linksetdb.get('links', [])
            
            return links
            
        except requests.exceptions.RequestException as e:
            print(f"[WARN] NCBI API request failed: {e}")
            return {}


# 使用示例
if __name__ == "__main__":
    client = NCBIClient()
    
    # 搜索 SPL 基因
    genes = client.search_gene("SPL", "Arabidopsis")
    print(f"Found {len(genes)} genes")
    
    for gene in genes:
        print(f"  - {gene.symbol}: {gene.description}")
