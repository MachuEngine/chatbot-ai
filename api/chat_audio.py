# api/chat_audio.py
from __future__ import annotations

import os
import json
import requests
from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from models.api_models import ChatRequest, Meta
from api.chat import chat  # 기존 /chat 로직 재사용

router = APIRouter()

OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"

@router.post("/chat_audio")
async def chat_audio(
    audio_file: UploadFile = File(...),
    meta_json: str = Form(...),
):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not set")

    # meta 파싱
    meta = Meta(**json.loads(meta_json))

    model = os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe").strip()

    # OpenAI transcriptions (multipart)
    # - 문서상 transcriptions/ translations 엔드포인트가 있고, transcribe 모델 지원 :contentReference[oaicite:4]{index=4}
    files = {
        "file": (audio_file.filename, await audio_file.read(), audio_file.content_type or "application/octet-stream"),
    }
    data = {
        "model": model,
        # "language": "ko",  # 필요하면 켜기
    }

    r = requests.post(
        OPENAI_TRANSCRIBE_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        files=files,
        data=data,
        timeout=30,
    )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=r.text[:800])

    resp = r.json()
    text = (resp.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty_transcript")

    # 기존 /chat 재사용 (input_type만 stt로 바꿔서 추적)
    req = ChatRequest(user_message=text, meta=meta.model_copy(update={"input_type": "stt"}))
    return chat(req)
