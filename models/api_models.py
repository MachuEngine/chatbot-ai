from __future__ import annotations

from typing import Dict, Any, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict


class Meta(BaseModel):
    # ✅ (중요) 클라이언트 meta 확장 대비: 모르는 필드가 와도 422로 터지지 않게
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


class ChatRequest(BaseModel):
    user_message: str = Field(..., min_length=1)
    meta: Meta

    # ✅ edu payload는 meta가 아니라 body로 (하위호환: api/chat.py에서 meta에 있어도 흡수)
    content: Optional[str] = None
    student_answer: Optional[str] = None
    topic: Optional[str] = None


class ChatResponse(BaseModel):
    trace_id: str
    reply: Dict[str, Any]
    state: Dict[str, Any]
