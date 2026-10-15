#!/usr/bin/env python3
"""
Conversation API Routes - 多会话管理REST API
类似ChatGPT的会话管理功能
"""

import asyncio
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime

from gfa_core.mas.memory.conversation import ConversationManager

router = APIRouter()

# 全局ConversationManager实例
_conversation_manager: Optional[ConversationManager] = None


def get_conversation_manager() -> ConversationManager:
    """获取ConversationManager实例（单例）"""
    global _conversation_manager
    if _conversation_manager is None:
        _conversation_manager = ConversationManager()
    return _conversation_manager


# ============ Pydantic Models ============

class CreateConversationRequest(BaseModel):
    """创建会话请求"""
    user_id: str
    title: Optional[str] = None
    gene_family: Optional[str] = None
    species: Optional[str] = None
    system_prompt: Optional[str] = None
    job_id: Optional[str] = None
    input_dir: Optional[str] = None
    output_dir: Optional[str] = None
    working_dir: Optional[str] = None


class CreateConversationResponse(BaseModel):
    """创建会话响应"""
    conversation_id: str
    title: str
    created_at: str
    message: str


class AddMessageRequest(BaseModel):
    """添加消息请求"""
    conversation_id: str
    role: str  # "user" | "assistant" | "system"
    content: str
    metadata: Optional[dict] = None


class AddMessageResponse(BaseModel):
    """添加消息响应"""
    success: bool
    message: str


class ConversationSummary(BaseModel):
    """会话摘要"""
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


class ListConversationsResponse(BaseModel):
    """列会话响应"""
    user_id: str
    conversations: list[ConversationSummary]
    total: int


class MessageItem(BaseModel):
    """消息项"""
    role: str
    content: str
    timestamp: str
    metadata: Optional[dict] = None


class GetMessagesResponse(BaseModel):
    """获取消息响应"""
    conversation_id: str
    title: str
    messages: list[MessageItem]
    total: int


class UpdateTitleRequest(BaseModel):
    """更新标题请求"""
    conversation_id: str
    title: str


class DeleteConversationRequest(BaseModel):
    """删除会话请求"""
    conversation_id: str


class LinkToJobRequest(BaseModel):
    """关联到Job请求"""
    conversation_id: str
    job_id: str


# ============ API Routes ============

@router.post("/conversations/create", response_model=CreateConversationResponse)
async def create_conversation(request: CreateConversationRequest):
    """
    创建新会话
    
    类似ChatGPT的"New Chat"功能
    """
    try:
        manager = get_conversation_manager()
        
        conversation = await manager.create_conversation(
            user_id=request.user_id,
            title=request.title,
            gene_family=request.gene_family,
            species=request.species,
            system_prompt=request.system_prompt,
            job_id=request.job_id,
            input_dir=request.input_dir,
            output_dir=request.output_dir,
            working_dir=request.working_dir,
        )
        
        return CreateConversationResponse(
            conversation_id=conversation.conversation_id,
            title=conversation.title,
            created_at=conversation.created_at,
            message="会话创建成功"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建会话失败: {str(e)}")


@router.post("/conversations/message", response_model=AddMessageResponse)
async def add_message(request: AddMessageRequest):
    """
    添加消息到会话
    
    发送消息或接收AI回复
    """
    try:
        manager = get_conversation_manager()
        
        success = await manager.add_message(
            conversation_id=request.conversation_id,
            role=request.role,
            content=request.content,
            metadata=request.metadata,
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="会话不存在")
        
        return AddMessageResponse(
            success=True,
            message="消息添加成功"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加消息失败: {str(e)}")


@router.get("/conversations/list/{user_id}", response_model=ListConversationsResponse)
async def list_conversations(
    user_id: str,
    status: str = Query(default="active", description="会话状态: active/archived/deleted"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """
    列出用户的所有会话
    
    类似ChatGPT左侧边栏的会话列表
    """
    try:
        manager = get_conversation_manager()
        
        conversations = await manager.list_user_conversations(
            user_id=user_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        
        return ListConversationsResponse(
            user_id=user_id,
            conversations=[
                ConversationSummary(
                    conversation_id=c.conversation_id,
                    title=c.title,
                    last_message=c.last_message,
                    updated_at=c.updated_at,
                    message_count=c.message_count,
                    gene_family=c.gene_family,
                    job_id=c.job_id,
                    input_dir=c.input_dir,
                    output_dir=c.output_dir,
                    working_dir=c.working_dir,
                )
                for c in conversations
            ],
            total=len(conversations)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取会话列表失败: {str(e)}")


@router.get("/conversations/{conversation_id}/messages", response_model=GetMessagesResponse)
async def get_messages(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """
    获取会话的消息历史
    
    查看某个会话的完整对话
    """
    try:
        manager = get_conversation_manager()
        
        # 获取会话信息
        conversation = await manager.get_conversation(conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="会话不存在")
        
        # 默认返回最近 limit 条消息；显式 offset 用于从旧消息开始分页。
        total_messages = len(conversation.messages or [])
        if offset == 0:
            messages = (conversation.messages or [])[-limit:]
        else:
            messages = await manager.get_messages(
                conversation_id=conversation_id,
                limit=limit,
                offset=offset,
            )
        
        return GetMessagesResponse(
            conversation_id=conversation_id,
            title=conversation.title,
            messages=[
                MessageItem(
                    role=m["role"],
                    content=m["content"],
                    timestamp=m["timestamp"],
                    metadata=m.get("metadata"),
                )
                for m in messages
            ],
            total=total_messages
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取消息失败: {str(e)}")


@router.post("/conversations/update-title")
async def update_title(request: UpdateTitleRequest):
    """
    更新会话标题
    
    重命名会话
    """
    try:
        manager = get_conversation_manager()
        
        success = await manager.update_title(
            conversation_id=request.conversation_id,
            title=request.title,
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="会话不存在")
        
        return {"success": True, "message": "标题更新成功"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新标题失败: {str(e)}")


@router.post("/conversations/delete")
async def delete_conversation(request: DeleteConversationRequest):
    """
    删除会话
    
    软删除，会话进入deleted状态
    """
    try:
        manager = get_conversation_manager()
        
        success = await manager.delete_conversation(
            conversation_id=request.conversation_id,
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="会话不存在")
        
        return {"success": True, "message": "会话已删除"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除会话失败: {str(e)}")


@router.post("/conversations/archive")
async def archive_conversation(request: DeleteConversationRequest):
    """
    归档会话
    
    会话进入archived状态
    """
    try:
        manager = get_conversation_manager()
        
        success = await manager.archive_conversation(
            conversation_id=request.conversation_id,
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="会话不存在")
        
        return {"success": True, "message": "会话已归档"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"归档会话失败: {str(e)}")


@router.post("/conversations/link-to-job")
async def link_to_job(request: LinkToJobRequest):
    """
    关联会话到分析任务
    
    将对话与具体的基因家族分析任务关联
    """
    try:
        manager = get_conversation_manager()
        
        success = await manager.link_to_job(
            conversation_id=request.conversation_id,
            job_id=request.job_id,
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="会话不存在")
        
        return {"success": True, "message": "已关联到分析任务"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"关联任务失败: {str(e)}")
