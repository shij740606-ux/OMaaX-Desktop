#!/usr/bin/env python3
"""
STRING Database API Client
用于获取蛋白互作网络数据
"""

import requests
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
import json

from gfa_core.ppi_string_policy import STRING_SPECIES_MAP, string_taxon_id


class StringAPI:
    """STRING数据库API客户端"""
    
    BASE_URL = "https://string-db.org/api"
    
    SPECIES_MAP = STRING_SPECIES_MAP
    
    def __init__(self, species: str = "ath", timeout: int = 30):
        """
        初始化STRING API客户端
        
        Args:
            species: 物种代码（ath, osa, zma等）
            timeout: 请求超时时间（秒）
        """
        self.species = species
        self.species_id = string_taxon_id(species, "3702")
        self.timeout = timeout
        self.session = requests.Session()
        
    def get_network(self, 
                    proteins: List[str], 
                    required_score: int = 200,
                    network_type: str = "functional") -> Dict[str, Any]:
        """
        获取蛋白互作网络
        
        Args:
            proteins: 蛋白ID列表
            required_score: 互作置信度阈值（0-1000，默认200=0.7）
            network_type: 网络类型（functional/physical）
        
        Returns:
            互作网络数据
        """
        if not proteins:
            return {"error": "No proteins provided"}
        
        # STRING API限制：每次最多2000个蛋白
        if len(proteins) > 2000:
            proteins = proteins[:2000]
            print(f"[WARNING] Protein list truncated to 2000 (STRING API limit)")
        
        params = {
            "identifiers": "\r".join(proteins),
            "species": self.species_id,
            "required_score": required_score,
            "network_type": network_type,
            "caller_identity": "BotanicalBaseAgent"
        }
        
        try:
            response = self.session.get(
                f"{self.BASE_URL}/json/network",
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"error": f"API request failed: {str(e)}"}
    
    def get_interaction_partners(self, 
                                  protein: str,
                                  required_score: int = 200,
                                  limit: int = 100) -> Dict[str, Any]:
        """
        获取指定蛋白的互作伙伴
        
        Args:
            protein: 蛋白ID
            required_score: 互作置信度阈值
            limit: 返回的最大互作数
        
        Returns:
            互作伙伴数据
        """
        params = {
            "identifiers": protein,
            "species": self.species_id,
            "required_score": required_score,
            "limit": limit,
            "caller_identity": "BotanicalBaseAgent"
        }
        
        try:
            response = self.session.get(
                f"{self.BASE_URL}/json/interaction_partners",
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"error": f"API request failed: {str(e)}"}
    
    def get_protein_info(self, proteins: List[str]) -> Dict[str, Any]:
        """
        获取蛋白详细信息
        
        Args:
            proteins: 蛋白ID列表
        
        Returns:
            蛋白信息数据
        """
        if not proteins:
            return {"error": "No proteins provided"}
        
        params = {
            "identifiers": "\r".join(proteins),
            "species": self.species_id,
            "caller_identity": "BotanicalBaseAgent"
        }
        
        try:
            response = self.session.get(
                f"{self.BASE_URL}/json/protein_info",
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"error": f"API request failed: {str(e)}"}
    
    def save_network_to_file(self, 
                             network_data: Dict[str, Any], 
                             output_file: Path,
                             format: str = "tsv"):
        """
        保存网络数据到文件
        
        Args:
            network_data: 网络数据
            output_file: 输出文件路径
            format: 输出格式（tsv/json）
        """
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        if format == "json":
            with open(output_file, 'w') as f:
                json.dump(network_data, f, indent=2)
        elif format == "tsv":
            # 转换为边列表格式
            edges = []
            for interaction in network_data:
                if isinstance(interaction, dict):
                    edges.append({
                        "protein1": interaction.get("preferredName_A", ""),
                        "protein2": interaction.get("preferredName_B", ""),
                        "score": interaction.get("score", 0),
                        "ncbi_tax_id": interaction.get("ncbiTaxonId", ""),
                    })
            
            import pandas as pd
            df = pd.DataFrame(edges)
            df.to_csv(output_file, sep="\t", index=False)
        
        print(f"[INFO] Network saved to: {output_file}")


def test_string_api():
    """测试STRING API"""
    api = StringAPI(species="ath")
    
    # 测试基因
    test_genes = ["AT1G01010", "AT1G01020", "AT1G01030"]
    
    print("Testing STRING API...")
    print(f"Species: {api.species} (ID: {api.species_id})")
    print(f"Test genes: {test_genes}")
    
    # 获取网络
    network = api.get_network(test_genes, required_score=400)
    
    if "error" in network:
        print(f"Error: {network['error']}")
    else:
        print(f"Interactions found: {len(network)}")
        if network:
            print("Sample interaction:")
            print(json.dumps(network[0], indent=2))


if __name__ == "__main__":
    test_string_api()
