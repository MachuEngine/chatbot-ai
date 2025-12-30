# models/api_models.py
from __future__ import annotations

from typing import Dict, Any, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict


class Meta(BaseModel):
    model_config = ConfigDict(extra="allow")

    client_session_id: str = Field(..., description="탭/세션 식별자", min_length=1)

    device_type: str = "web"
    locale: str = "ko-KR"
    timezone: str = "Asia/Seoul"
    current_url: Optional[str] = None

    mode: Optional[Literal["kiosk", "edu", "web", "admin"]] = None
    input_type: Literal["text", "stt", "voice"] = "text"

    store_id: Optional[str] = None
    kiosk_type: Optional[Literal["cafe", "cinema", "fastfood", "etc"]] = None

    # ✅ [추가] 사용자 학력/레벨 정보
    user_level: Optional[Literal["beginner", "intermediate", "advanced"]] = None

class ChatRequest(BaseModel):
    user_message: str = Field(..., min_length=1)
    meta: Meta

    content: Optional[str] = None
    student_answer: Optional[str] = None
    topic: Optional[str] = None


class ChatResponse(BaseModel):
    trace_id: str
    reply: Dict[str, Any]
    state: Dict[str, Any]