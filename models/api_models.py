# models/api_models.py
from __future__ import annotations

from typing import Dict, Any, Optional, Literal, List
from pydantic import BaseModel, Field, ConfigDict, field_validator


class Meta(BaseModel):
    model_config = ConfigDict(extra="allow")

    client_session_id: str = Field(..., description="탭/세션 식별자", min_length=1)

    device_type: str = "web"
    locale: str = "ko-KR"
    timezone: str = "Asia/Seoul"
    current_url: Optional[str] = None

    # 시스템 모드
    mode: Optional[Literal["kiosk", "edu", "web", "admin", "driving", "companion"]] = None
    input_type: Literal["text", "stt", "voice"] = "text"

    # [추가] 차량 현재 상태 (Driving 모드용)
    # 예: { "window_driver": "closed", "hvac_power": "on" }
    vehicle_status: Optional[Dict[str, Any]] = None

    # 키오스크 관련
    store_id: Optional[str] = None
    kiosk_type: Optional[Literal["cafe", "cinema", "fastfood", "etc"]] = None

    # Edu Context
    user_level: Optional[Literal["beginner", "intermediate", "advanced"]] = None
    user_age_group: Optional[Literal["child", "teen", "adult"]] = None
    
    subject: Optional[str] = None
    tone_style: Optional[str] = None
    native_language: Optional[str] = None
    target_exam: Optional[str] = None
    weak_points: Optional[List[str]] = None

    @field_validator(
        'mode', 'input_type', 'kiosk_type', 
        'user_level', 'user_age_group', 
        'subject', 'tone_style', 'native_language',
        mode='before'
    )
    @classmethod
    def normalize_lowercase(cls, v):
        if isinstance(v, str):
            return v.lower().strip()
        return v


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