"""
Agent消息总线系统
支持Agent间异步消息传递和协作
"""
import asyncio
import json
from datetime import datetime
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from enum import Enum


class MessageType(Enum):
    """消息类型"""
    REQUEST = "request"           # 请求
    RESPONSE = "response"         # 响应
    NOTIFICATION = "notification" # 通知
    ERROR = "error"              # 错误
    HEARTBEAT = "heartbeat"      # 心跳


class MessagePriority(Enum):
    """消息优先级"""
    HIGH = 1
    NORMAL = 2
    LOW = 3


@dataclass
class AgentMessage:
    """Agent消息"""
    id: str
    sender: str           # 发送者Agent ID
    receiver: str         # 接收者Agent ID (或 "broadcast")
    type: MessageType
    content: Any
    priority: MessagePriority = MessagePriority.NORMAL
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    session_id: str = ""
    reply_to: Optional[str] = None  # 回复的消息ID
    metadata: Dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "sender": self.sender,
            "receiver": self.receiver,
            "type": self.type.value,
            "content": self.content,
            "priority": self.priority.value,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "reply_to": self.reply_to,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "AgentMessage":
        """从字典创建"""
        return cls(
            id=data["id"],
            sender=data["sender"],
            receiver=data["receiver"],
            type=MessageType(data["type"]),
            content=data["content"],
            priority=MessagePriority(data.get("priority", 2)),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
            session_id=data.get("session_id", ""),
            reply_to=data.get("reply_to"),
            metadata=data.get("metadata", {})
        )


class MessageBus:
    """
    Agent消息总线
    
    支持：
    - 点对点消息传递
    - 广播消息
    - 消息订阅/发布
    - 消息历史记录
    - 异步消息处理
    """
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}  # Agent ID -> 回调函数列表
        self._message_history: List[AgentMessage] = []
        self._max_history = 1000  # 最大历史记录数
        self._lock = asyncio.Lock()
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
    
    async def start(self):
        """启动消息总线"""
        self._running = True
        asyncio.create_task(self._process_messages())
    
    async def stop(self):
        """停止消息总线"""
        self._running = False
    
    async def _process_messages(self):
        """处理消息队列"""
        while self._running:
            try:
                message = await asyncio.wait_for(
                    self._message_queue.get(), 
                    timeout=1.0
                )
                await self._deliver_message(message)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Message processing error: {e}")
    
    async def send(self, message: AgentMessage) -> bool:
        """
        发送消息
        
        Args:
            message: 要发送的消息
        
        Returns:
            是否成功加入队列
        """
        try:
            # 添加到历史记录
            async with self._lock:
                self._message_history.append(message)
                if len(self._message_history) > self._max_history:
                    self._message_history = self._message_history[-self._max_history:]
            
            # 加入处理队列
            await self._message_queue.put(message)
            return True
        except Exception as e:
            print(f"Failed to send message: {e}")
            return False
    
    async def _deliver_message(self, message: AgentMessage):
        """投递消息到订阅者"""
        if message.receiver == "broadcast":
            # 广播消息
            for agent_id, callbacks in self._subscribers.items():
                for callback in callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            asyncio.create_task(callback(message))
                        else:
                            callback(message)
                    except Exception as e:
                        print(f"Broadcast error to {agent_id}: {e}")
        else:
            # 点对点消息
            if message.receiver in self._subscribers:
                for callback in self._subscribers[message.receiver]:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            asyncio.create_task(callback(message))
                        else:
                            callback(message)
                    except Exception as e:
                        print(f"Delivery error to {message.receiver}: {e}")
    
    def subscribe(self, agent_id: str, callback: Callable):
        """
        订阅消息
        
        Args:
            agent_id: Agent ID
            callback: 消息回调函数
        """
        if agent_id not in self._subscribers:
            self._subscribers[agent_id] = []
        self._subscribers[agent_id].append(callback)
    
    def unsubscribe(self, agent_id: str, callback: Callable = None):
        """
        取消订阅
        
        Args:
            agent_id: Agent ID
            callback: 要移除的回调函数，None表示移除所有
        """
        if agent_id in self._subscribers:
            if callback is None:
                del self._subscribers[agent_id]
            else:
                self._subscribers[agent_id] = [
                    cb for cb in self._subscribers[agent_id] 
                    if cb != callback
                ]
    
    async def request(self, sender: str, receiver: str, content: Any, 
                     timeout: float = 30.0) -> Optional[AgentMessage]:
        """
        发送请求并等待响应
        
        Args:
            sender: 发送者Agent ID
            receiver: 接收者Agent ID
            content: 请求内容
            timeout: 超时时间（秒）
        
        Returns:
            响应消息或None（超时）
        """
        import uuid
        
        request_id = str(uuid.uuid4())
        response_future = asyncio.Future()
        
        # 临时订阅响应
        def response_handler(msg: AgentMessage):
            if (msg.type == MessageType.RESPONSE and 
                msg.reply_to == request_id):
                if not response_future.done():
                    response_future.set_result(msg)
        
        self.subscribe(sender, response_handler)
        
        # 发送请求
        request_msg = AgentMessage(
            id=request_id,
            sender=sender,
            receiver=receiver,
            type=MessageType.REQUEST,
            content=content,
            priority=MessagePriority.HIGH
        )
        
        await self.send(request_msg)
        
        try:
            # 等待响应
            response = await asyncio.wait_for(response_future, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            return None
        finally:
            # 取消订阅
            self.unsubscribe(sender, response_handler)
    
    def get_message_history(self, session_id: str = None, 
                           agent_id: str = None,
                           limit: int = 100) -> List[AgentMessage]:
        """
        获取消息历史
        
        Args:
            session_id: 会话ID过滤
            agent_id: Agent ID过滤
            limit: 返回消息数量限制
        
        Returns:
            消息列表
        """
        messages = self._message_history
        
        if session_id:
            messages = [m for m in messages if m.session_id == session_id]
        
        if agent_id:
            messages = [
                m for m in messages 
                if m.sender == agent_id or m.receiver == agent_id
            ]
        
        return messages[-limit:]
    
    def get_conversation(self, agent1: str, agent2: str, 
                        session_id: str = None) -> List[AgentMessage]:
        """
        获取两个Agent之间的对话
        
        Args:
            agent1: Agent 1 ID
            agent2: Agent 2 ID
            session_id: 会话ID过滤
        
        Returns:
            按时间排序的消息列表
        """
        messages = [
            m for m in self._message_history
            if ((m.sender == agent1 and m.receiver == agent2) or
                (m.sender == agent2 and m.receiver == agent1))
        ]
        
        if session_id:
            messages = [m for m in messages if m.session_id == session_id]
        
        return sorted(messages, key=lambda m: m.timestamp)


# 全局消息总线实例
_message_bus: Optional[MessageBus] = None


def get_message_bus() -> MessageBus:
    """获取全局消息总线实例"""
    global _message_bus
    if _message_bus is None:
        _message_bus = MessageBus()
    return _message_bus


async def init_message_bus():
    """初始化消息总线"""
    bus = get_message_bus()
    await bus.start()
    return bus
