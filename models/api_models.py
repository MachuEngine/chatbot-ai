from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

class Meta(BaseModel):
    client_session_id: str = Field(..., description="탭/세션 식별자")
    device_type: str = "web"
    locale: str = "ko-KR"
    timezone: str = "Asia/Seoul"
    current_url: Optional[str] = None
    mode: Optional[str] = None
    input_type: str = "text"
    store_id: Optional[str] = None  # (추가) 매장 식별자

    kiosk_type: Optional[str] = None  # ✅ 추가: "cafe", "cinema", ...

class ChatRequest(BaseModel):
    user_message: str
    meta: Meta

class ChatResponse(BaseModel):
    trace_id: str                  # (추가)
    reply: Dict[str, Any]
    state: Dict[str, Any]

    