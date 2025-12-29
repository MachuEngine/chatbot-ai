from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.chat import router as chat_router
from api.chat_audio import router as chat_audio_router

app = FastAPI(title="Metadata Chatbot Prototype")

app.include_router(chat_router, prefix="/api")
app.include_router(chat_audio_router, prefix="/api")


@app.get("/health")
def health():
    return {"ok": True}


# static/index.html (chatbot-tester) 서빙
# - 반드시 include_router 이후에 mount해야 /api 경로를 덮지 않음
app.mount("/", StaticFiles(directory="static", html=True), name="static")
