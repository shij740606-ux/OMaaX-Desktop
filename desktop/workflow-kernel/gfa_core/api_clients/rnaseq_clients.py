"""
转录组数据平台 API 客户端

集成多个转录组数据库和工具平台
"""

import requests
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass


@dataclass
class ExpressionData:
    """基因表达数据"""
    gene_id: str
    tpm: Optional[float] = None
    fpkm: Optional[float] = None
    count: Optional[float] = None
    sample: Optional[str] = None
    tissue: Optional[str] = None
    condition: Optional[str] = None


class EnsemblPlantsClient:
    """
    Ensembl Plants API 客户端
    
    植物基因组数据库
    URL: https://plants.ensembl.org/
    API: https://rest.ensembl.org/
    """
    
    BASE_URL = "https://rest.ensembl.org"
    
    def __init__(self, rate_limit: float = 0.5):
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
    
    def get_gene_info(self, species: str, gene_id: str) -> Optional[Dict[str, Any]]:
        """
        获取基因信息
        
        Args:
            species: 物种名称（如 arabidopsis_thaliana）
            gene_id: 基因 ID
            
        Returns:
            基因信息
        """
        url = f"{self.BASE_URL}/lookup/symbol/{species}/{gene_id}"
        
        try:
            response = self._rate_limited_request(url)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"[WARN] Ensembl API error: {e}")
            return None
    
    def get_expression(self, species: str, gene_id: str) -> List[ExpressionData]:
        """
        获取基因表达数据
        
        Args:
            species: 物种名称
            gene_id: 基因 ID
            
        Returns:
            表达数据列表
        """
        url = f"{self.BASE_URL}/expression/{species}/{gene_id}"
        
        try:
            response = self._rate_limited_request(url)
            if response.status_code == 200:
                data = response.json()
                
                expression_data = []
                for item in data.get('data', []):
                    expr = ExpressionData(
                        gene_id=gene_id,
                        tpm=item.get('tpm'),
                        fpkm=item.get('fpkm'),
                        sample=item.get('sample'),
                        tissue=item.get('tissue'),
                    )
                    expression_data.append(expr)
                
                return expression_data
            return []
        except Exception as e:
            print(f"[WARN] Ensembl API error: {e}")
            return []
    
    def get_homologues(self, species: str, gene_id: str, target_species: str) -> List[Dict[str, Any]]:
        """
        获取同源基因
        
        Args:
            species: 源物种
            gene_id: 基因 ID
            target_species: 目标物种
            
        Returns:
            同源基因列表
        """
        url = f"{self.BASE_URL}/homology/symbol/{species}/{gene_id}"
        params = {'target_species': target_species}
        
        try:
            response = self._rate_limited_request(url, params=params)
            if response.status_code == 200:
                return response.json().get('data', [])
            return []
        except Exception as e:
            print(f"[WARN] Ensembl API error: {e}")
            return []


class PhytozomeClient:
    """
    Phytozome API 客户端
    
    植物比较基因组学平台
    URL: https://phytozome-next.jgi.doe.gov/
    """
    
    BASE_URL = "https://phytozome-next.jgi.doe.gov/api"
    
    def __init__(self, api_key: Optional[str] = None, rate_limit: float = 1.0):
        self.api_key = api_key
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
        
        if self.api_key:
            kwargs.setdefault('params', {})
            kwargs['params']['api_key'] = self.api_key
        
        response = self.session.get(url, **kwargs)
        self.last_request_time = time.time()
        
        return response
    
    def search_gene(self, organism: str, gene_name: str) -> List[Dict[str, Any]]:
        """
        搜索基因
        
        Args:
            organism: 生物体（如 Athaliana_447_TAIR10）
            gene_name: 基因名称
            
        Returns:
            基因列表
        """
        url = f"{self.BASE_URL}/genes/search"
        params = {
            'organism': organism,
            'name': gene_name,
        }
        
        try:
            response = self._rate_limited_request(url, params=params)
            if response.status_code == 200:
                return response.json().get('genes', [])
            return []
        except Exception as e:
            print(f"[WARN] Phytozome API error: {e}")
            return []
    
    def get_gene_expression(self, organism: str, gene_id: str) -> Dict[str, Any]:
        """
        获取基因表达数据
        
        Args:
            organism: 生物体
            gene_id: 基因 ID
            
        Returns:
            表达数据
        """
        url = f"{self.BASE_URL}/expression/{organism}/{gene_id}"
        
        try:
            response = self._rate_limited_request(url)
            if response.status_code == 200:
                return response.json()
            return {}
        except Exception as e:
            print(f"[WARN] Phytozome API error: {e}")
            return {}


class TAIRClient:
    """
    TAIR (The Arabidopsis Information Resource) API 客户端
    
    拟南芥数据库
    URL: https://www.arabidopsis.org/
    """
    
    BASE_URL = "https://www.arabidopsis.org/api"
    
    def __init__(self, rate_limit: float = 1.0):
        self.rate_limit = rate_limit
        self.last_request_time = 0
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'bbav20311/1.0 (Research Tool)'
        })
    
    def _rate_limited_request(self, url: str, **kwargs) -> requests.Response:
        """带速率限制的请求"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        
        response = self.session.get(url, **kwargs)
        self.last_request_time = time.time()
        
        return response
    
    def search_locus(self, locus_name: str) -> Optional[Dict[str, Any]]:
        """
        搜索基因位点
        
        Args:
            locus_name: 位点名称（如 AT1G01010）
            
        Returns:
            位点信息
        """
        url = f"{self.BASE_URL}/loci/{locus_name}"
        
        try:
            response = self._rate_limited_request(url)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"[WARN] TAIR API error: {e}")
            return None


class PlantTFDBClient:
    """
    PlantTFDB (Plant Transcription Factor Database) API 客户端
    
    植物转录因子数据库
    URL: http://planttfdb.gao-lab.org/
    """
    
    BASE_URL = "http://planttfdb.gao-lab.org/api"
    
    def __init__(self, rate_limit: float = 1.0):
        self.rate_limit = rate_limit
        self.last_request_time = 0
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'bbav20311/1.0 (Research Tool)'
        })
    
    def _rate_limited_request(self, url: str, **kwargs) -> requests.Response:
        """带速率限制的请求"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        
        response = self.session.get(url, **kwargs)
        self.last_request_time = time.time()
        
        return response
    
    def search_tf(self, species: str, gene_id: str) -> Optional[Dict[str, Any]]:
        """
        搜索转录因子
        
        Args:
            species: 物种（如 ath, osa, zma）
            gene_id: 基因 ID
            
        Returns:
            转录因子信息
        """
        url = f"{self.BASE_URL}/tf/{species}/{gene_id}"
        
        try:
            response = self._rate_limited_request(url)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"[WARN] PlantTFDB API error: {e}")
            return None
    
    def get_family_info(self, species: str, family: str) -> List[Dict[str, Any]]:
        """
        获取转录因子家族信息
        
        Args:
            species: 物种
            family: 家族名称（如 MYB, bZIP）
            
        Returns:
            家族成员列表
        """
        url = f"{self.BASE_URL}/family/{species}/{family}"
        
        try:
            response = self._rate_limited_request(url)
            if response.status_code == 200:
                return response.json().get('members', [])
            return []
        except Exception as e:
            print(f"[WARN] PlantTFDB API error: {e}")
            return []


# 转录组数据平台汇总
RNASEQ_PLATFORMS = {
    'ensembl_plants': {
        'name': 'Ensembl Plants',
        'url': 'https://plants.ensembl.org/',
        'species': ['Arabidopsis', 'Rice', 'Maize', 'Soybean', 'Tomato', ...],
        'data_types': ['genome', 'annotation', 'expression', 'variation'],
        'client': EnsemblPlantsClient,
    },
    'phytozome': {
        'name': 'Phytozome',
        'url': 'https://phytozome-next.jgi.doe.gov/',
        'species': ['Arabidopsis', 'Rice', 'Maize', 'Poplar', ...],
        'data_types': ['genome', 'annotation', 'expression', 'synteny'],
        'client': PhytozomeClient,
    },
    'tair': {
        'name': 'TAIR',
        'url': 'https://www.arabidopsis.org/',
        'species': ['Arabidopsis'],
        'data_types': ['genome', 'annotation', 'phenotype', 'literature'],
        'client': TAIRClient,
    },
    'planttfdb': {
        'name': 'PlantTFDB',
        'url': 'http://planttfdb.gao-lab.org/',
        'species': ['Arabidopsis', 'Rice', 'Maize', 'Soybean', ...],
        'data_types': ['transcription_factor', 'regulation'],
        'client': PlantTFDBClient,
    },
}


# 使用示例
if __name__ == "__main__":
    print("转录组数据平台 API 客户端")
    print("=" * 60)
    
    # Ensembl Plants
    ensembl = EnsemblPlantsClient()
    gene_info = ensembl.get_gene_info('arabidopsis_thaliana', 'SPL1')
    if gene_info:
        print(f"Ensembl: Found {gene_info.get('display_name')}")
    
    # PlantTFDB
    planttfdb = PlantTFDBClient()
    tf_info = planttfdb.search_tf('ath', 'AT1G01010')
    if tf_info:
        print(f"PlantTFDB: Found {tf_info.get('family')} family TF")
