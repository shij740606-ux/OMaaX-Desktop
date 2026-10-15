"""
持久化记忆存储 - 将记忆保存到文件
"""

import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Any
from dataclasses import asdict

from gfa_core.mas.memory.base import BaseMemory, MemoryEntry


class PersistentMemory(BaseMemory):
    """基于文件的持久化记忆存储"""
    
    def __init__(self, storage_dir: Path, max_entries: int = 10000):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries
        self._lock = asyncio.Lock()
    
    def _get_file_path(self, key: str) -> Path:
        """获取存储文件路径"""
        # 使用key的前2个字符作为子目录，避免单目录文件过多
        subdir = key[:2] if len(key) >= 2 else "xx"
        dir_path = self.storage_dir / subdir
        dir_path.mkdir(exist_ok=True)
        return dir_path / f"{key}.json"
    
    def _serialize(self, value: Any) -> dict:
        """序列化数据"""
        if hasattr(value, '__dataclass_fields__'):
            return {
                "_type": "dataclass",
                "_class": value.__class__.__name__,
                "data": asdict(value)
            }
        return {"_type": "raw", "data": value}
    
    def _deserialize(self, data: dict) -> Any:
        """反序列化数据"""
        from gfa_core.mas.memory.session import SessionData
        from gfa_core.mas.memory.case import AnalysisCase
        from gfa_core.mas.memory.knowledge import KnowledgeEntry
        
        if data.get("_type") == "dataclass":
            class_name = data.get("_class")
            class_map = {
                "SessionData": SessionData,
                "AnalysisCase": AnalysisCase,
                "KnowledgeEntry": KnowledgeEntry,
            }
            cls = class_map.get(class_name)
            if cls:
                return cls(**data["data"])
        return data.get("data")
    
    async def store(self, key: str, value: Any, ttl: int | None = None) -> None:
        """存储数据到文件"""
        async with self._lock:
            file_path = self._get_file_path(key)
            
            entry = MemoryEntry(
                key=key,
                value=self._serialize(value),
                ttl=ttl,
            )
            
            # 写入文件
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "key": entry.key,
                    "value": entry.value,
                    "ttl": entry.ttl,
                    "created_at": entry.created_at.isoformat(),
                }, f, ensure_ascii=False, indent=2)
    
    async def retrieve(self, key: str) -> Any | None:
        """从文件读取数据"""
        async with self._lock:
            file_path = self._get_file_path(key)
            
            if not file_path.exists():
                return None
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 检查TTL
                if data.get("ttl"):
                    created_at = datetime.fromisoformat(data["created_at"])
                    from datetime import timedelta
                    if datetime.now() > created_at + timedelta(seconds=data["ttl"]):
                        file_path.unlink()  # 删除过期文件
                        return None
                
                return self._deserialize(data["value"])
            
            except (json.JSONDecodeError, KeyError, ValueError):
                return None
    
    async def delete(self, key: str) -> bool:
        """删除数据"""
        async with self._lock:
            file_path = self._get_file_path(key)
            if file_path.exists():
                file_path.unlink()
                return True
            return False
    
    async def exists(self, key: str) -> bool:
        """检查数据是否存在"""
        file_path = self._get_file_path(key)
        if not file_path.exists():
            return False
        
        # 检查是否过期
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if data.get("ttl"):
                created_at = datetime.fromisoformat(data["created_at"])
                from datetime import timedelta
                if datetime.now() > created_at + timedelta(seconds=data["ttl"]):
                    file_path.unlink()
                    return False
            
            return True
        except:
            return False
    
    async def clear(self) -> None:
        """清空所有数据"""
        async with self._lock:
            import shutil
            if self.storage_dir.exists():
                shutil.rmtree(self.storage_dir)
                self.storage_dir.mkdir(parents=True, exist_ok=True)
