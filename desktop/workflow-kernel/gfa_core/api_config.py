"""
API 配置管理模块

管理各数据库 API 的密钥和配置
支持从 YAML 文件和环境变量读取
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Optional, Any
from dataclasses import dataclass


@dataclass
class APIConfig:
    """API 配置数据类"""
    api_key: Optional[str] = None
    rate_limit: float = 1.0
    timeout: int = 30
    max_retries: int = 3


class ConfigManager:
    """
    API 配置管理器
    
    管理所有 API 的配置，支持：
    1. YAML 配置文件
    2. 环境变量
    3. 默认值
    """
    
    CONFIG_FILE = Path(__file__).parent.parent / "config" / "api_config.yaml"
    
    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._load_config()
    
    def _load_config(self):
        """加载配置"""
        # 1. 加载 YAML 配置
        if self.CONFIG_FILE.exists():
            with open(self.CONFIG_FILE, 'r') as f:
                self._config = yaml.safe_load(f) or {}
        
        # 2. 从环境变量覆盖
        self._load_from_env()
    
    def _load_from_env(self):
        """从环境变量加载配置"""
        env_mappings = {
            'ncbi': {
                'api_key': 'NCBI_API_KEY',
            },
            'phytozome': {
                'api_key': 'PHYTOZOME_API_KEY',
            },
            'tair': {
                'api_key': 'TAIR_API_KEY',
            },
        }
        
        for service, mappings in env_mappings.items():
            if service not in self._config:
                self._config[service] = {}
            
            for config_key, env_var in mappings.items():
                env_value = os.environ.get(env_var)
                if env_value:
                    self._config[service][config_key] = env_value
    
    def get_config(self, service: str) -> APIConfig:
        """
        获取指定服务的配置
        
        Args:
            service: 服务名称（ncbi, pfam, phytozome 等）
            
        Returns:
            APIConfig 对象
        """
        service_config = self._config.get(service, {})
        settings = self._config.get('settings', {})
        
        return APIConfig(
            api_key=service_config.get('api_key'),
            rate_limit=service_config.get('rate_limit', settings.get('default_rate_limit', 1.0)),
            timeout=settings.get('request_timeout', 30),
            max_retries=settings.get('max_retries', 3),
        )
    
    def get_endpoint(self, service: str) -> Optional[str]:
        """获取服务端点"""
        endpoints = self._config.get('endpoints', {})
        return endpoints.get(service)
    
    def get_setting(self, key: str, default: Any = None) -> Any:
        """获取通用设置"""
        settings = self._config.get('settings', {})
        return settings.get(key, default)
    
    def has_api_key(self, service: str) -> bool:
        """检查是否有 API key"""
        config = self.get_config(service)
        return config.api_key is not None and config.api_key != ""


# 全局配置实例
_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """获取全局配置管理器（单例）"""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def get_api_config(service: str) -> APIConfig:
    """快捷函数：获取 API 配置"""
    return get_config_manager().get_config(service)


# 使用示例
if __name__ == "__main__":
    config = get_config_manager()
    
    # 获取 NCBI 配置
    ncbi_config = config.get_config('ncbi')
    print(f"NCBI API Key: {'已配置' if ncbi_config.api_key else '未配置'}")
    print(f"Rate Limit: {ncbi_config.rate_limit}s")
    
    # 获取端点
    ncbi_endpoint = config.get_endpoint('ncbi')
    print(f"Endpoint: {ncbi_endpoint}")
