"""
Conversation Manager - 类似ChatGPT的多会话管理
支持用户维度、会话列表、历史消息
"""
import asyncio
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, List
from enum import Enum

from gfa_core.project_paths import MEMORY_ROOT


class ConversationStatus(str, Enum):
    """会话状态 - 使用str继承以便JSON序列化"""
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


@dataclass
class Conversation:
    """会话（类似ChatGPT的一个聊天窗口）"""
    conversation_id: str
    user_id: str
    title: str
    status: str = "active"  # 使用str而不是Enum
    
    # 关联信息
    gene_family: Optional[str] = None
    species: Optional[str] = None
    job_id: Optional[str] = None
    input_dir: Optional[str] = None
    output_dir: Optional[str] = None
    working_dir: Optional[str] = None
    
    # 消息历史
    messages: List[dict] = field(default_factory=list)
    
    # 元数据
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    message_count: int = 0
    
    # 设置
    model: str = "glm-5"
    system_prompt: Optional[str] = None


@dataclass
class ConversationSummary:
    """会话摘要（用于列表展示）"""
    conversation_id: str
    title: str
    last_message: str
    updated_at: str
    message_count: int
    gene_family: Optional[str] = None
    job_id: Optional[str] = None
    input_dir: Optional[str] = None
    output_dir: Optional[str] = None
    working_dir: Optional[str] = None


class ConversationManager:
    """
    会话管理器
    
    功能：
    1. 创建/删除/归档会话
    2. 按用户列出所有会话
    3. 获取会话消息历史
    4. 自动更新会话标题
    """
    
    def __init__(self, storage_dir: Path = None):
        if storage_dir is None:
            storage_dir = Path(os.environ.get("BBAV5_CONVERSATION_DIR") or MEMORY_ROOT / "conversations")
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        # 用户索引：user_id -> list of conversation_ids
        self._user_index_file = self.storage_dir / "user_index.json"
        self._user_index: dict = {}
        self._load_user_index()
        
        self._lock = asyncio.Lock()
        print(f"[ConversationManager] Storage: {self.storage_dir}")
    
    def _load_user_index(self):
        """加载用户索引"""
        if self._user_index_file.exists():
            try:
                with open(self._user_index_file, 'r', encoding='utf-8') as f:
                    self._user_index = json.load(f)
            except Exception as e:
                print(f"[ConversationManager] Failed to load index: {e}")
                self._user_index = {}
    
    async def _save_user_index(self):
        """保存用户索引"""
        async with self._lock:
            with open(self._user_index_file, 'w', encoding='utf-8') as f:
                json.dump(self._user_index, f, ensure_ascii=False, indent=2)
    
    def _get_conv_file(self, conversation_id: str) -> Path:
        """获取会话文件路径。

        Existing conversation ids contain their creation date, e.g.
        conv_20260524_172646_default.  Resolve that month first so old chats
        remain readable after the calendar month changes.
        """
        match = None
        try:
            import re
            match = re.match(r"^conv_(\d{6})\d{2}_", str(conversation_id or ""))
        except Exception:
            match = None
        candidate_months = []
        if match:
            candidate_months.append(match.group(1))
        candidate_months.append(datetime.now().strftime("%Y%m"))
        seen = set()
        for date_prefix in candidate_months:
            if date_prefix in seen:
                continue
            seen.add(date_prefix)
            dir_path = self.storage_dir / date_prefix
            path = dir_path / f"{conversation_id}.json"
            if path.exists():
                return path
        dir_path = self.storage_dir / (candidate_months[0] if candidate_months else datetime.now().strftime("%Y%m"))
        dir_path.mkdir(exist_ok=True)
        return dir_path / f"{conversation_id}.json"
    
    async def create_conversation(
        self,
        user_id: str,
        title: str = None,
        gene_family: str = None,
        species: str = None,
        system_prompt: str = None,
        job_id: str = None,
        input_dir: str = None,
        output_dir: str = None,
        working_dir: str = None,
    ) -> Conversation:
        """创建新会话"""
        conversation_id = f"conv_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{user_id[:8]}"
        
        if title is None:
            title = f"新会话 {datetime.now().strftime('%m-%d %H:%M')}"
        
        conversation = Conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            title=title,
            gene_family=gene_family,
            species=species,
            system_prompt=system_prompt,
            job_id=job_id,
            input_dir=input_dir,
            output_dir=output_dir,
            working_dir=working_dir,
        )
        
        # 保存会话
        await self._save_conversation(conversation)
        
        # 更新用户索引
        if user_id not in self._user_index:
            self._user_index[user_id] = []
        self._user_index[user_id].insert(0, conversation_id)
        await self._save_user_index()
        
        print(f"[ConversationManager] Created: {conversation_id} for user {user_id}")
        return conversation
    
    async def _save_conversation(self, conversation: Conversation):
        """保存会话到文件"""
        file_path = self._get_conv_file(conversation.conversation_id)
        async with self._lock:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(asdict(conversation), f, ensure_ascii=False, indent=2)
    
    async def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """获取会话详情"""
        file_path = self._get_conv_file(conversation_id)
        if not file_path.exists():
            return None
        
        try:
            async with self._lock:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            
            return Conversation(**data)
        except Exception as e:
            print(f"[ConversationManager] Failed to load conversation: {e}")
            return None
    
    async def list_user_conversations(
        self,
        user_id: str,
        status: str = "active",
        limit: int = 20,
        offset: int = 0,
    ) -> List[ConversationSummary]:
        """
        列出用户的所有会话（类似ChatGPT左侧边栏）
        
        Returns:
            List[ConversationSummary]: 会话摘要列表，按更新时间倒序
        """
        conversation_ids = self._user_index.get(user_id, [])
        
        summaries = []
        for conv_id in conversation_ids:
            conv = await self.get_conversation(conv_id)
            if conv and conv.status == status:
                # 获取最后一条消息
                last_msg = ""
                if conv.messages:
                    last_msg_content = conv.messages[-1].get('content', '')
                    last_msg = last_msg_content[:50] + "..." if len(last_msg_content) > 50 else last_msg_content
                
                summaries.append(ConversationSummary(
                    conversation_id=conv.conversation_id,
                    title=conv.title,
                    last_message=last_msg,
                    updated_at=conv.updated_at,
                    message_count=conv.message_count,
                    gene_family=conv.gene_family,
                    job_id=conv.job_id,
                    input_dir=conv.input_dir,
                    output_dir=conv.output_dir,
                    working_dir=conv.working_dir,
                ))
        
        # 按更新时间倒序
        summaries.sort(key=lambda x: x.updated_at, reverse=True)
        
        return summaries[offset:offset + limit]
    
    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict = None,
    ) -> bool:
        """添加消息到会话"""
        conv = await self.get_conversation(conversation_id)
        if not conv:
            return False
        
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        
        conv.messages.append(message)
        conv.message_count += 1
        conv.updated_at = datetime.now().isoformat()
        
        # 自动更新标题（第一条用户消息）
        if conv.message_count == 1 and role == "user":
            conv.title = content[:30] + "..." if len(content) > 30 else content
        
        await self._save_conversation(conv)
        return True
        await self._save_conversation(conv)
        return True
    
    async def get_messages(
        self,
        conversation_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[dict]:
        """获取会话消息历史"""
        conv = await self.get_conversation(conversation_id)
        if not conv:
            return []
        
        return conv.messages[offset:offset + limit]
    
    async def update_title(self, conversation_id: str, title: str) -> bool:
        """更新会话标题"""
        conv = await self.get_conversation(conversation_id)
        if not conv:
            return False
        
        conv.title = title
        await self._save_conversation(conv)
        return True
    
    async def delete_conversation(self, conversation_id: str) -> bool:
        """删除会话"""
        conv = await self.get_conversation(conversation_id)
        if not conv:
            return False
        
        conv.status = "deleted"
        await self._save_conversation(conv)
        
        # 从用户索引中移除
        if conv.user_id in self._user_index:
            if conversation_id in self._user_index[conv.user_id]:
                self._user_index[conv.user_id].remove(conversation_id)
                await self._save_user_index()
        
        return True
    
    async def archive_conversation(self, conversation_id: str) -> bool:
        """归档会话"""
        conv = await self.get_conversation(conversation_id)
        if not conv:
            return False
        
        conv.status = "archived"
        await self._save_conversation(conv)
        return True
    
    async def link_to_job(self, conversation_id: str, job_id: str) -> bool:
        """关联会话到分析任务"""
        conv = await self.get_conversation(conversation_id)
        if not conv:
            return False
        
        conv.job_id = job_id
        await self._save_conversation(conv)
        return True
