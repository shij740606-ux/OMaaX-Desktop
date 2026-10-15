"""
bbav20311 API 客户端模块

集成多个植物数据库和转录组数据平台 API
参考 ClawBio 的 gwas-lookup 多 API 并行查询模式
"""

from .plantcare_client import PlantCAREClient
from .pfam_client import PfamClient
from .ncbi_client import NCBIClient
from .rnaseq_clients import (
    EnsemblPlantsClient,
    PhytozomeClient,
    TAIRClient,
    PlantTFDBClient,
)
from .multi_api_query import MultiAPIQuery

__all__ = [
    'PlantCAREClient',
    'PfamClient',
    'NCBIClient',
    'EnsemblPlantsClient',
    'TAIRClient',
    'PhytozomeClient',
    'PlantTFDBClient',
    'MultiAPIQuery',
]
