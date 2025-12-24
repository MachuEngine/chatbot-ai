from fastapi import FastAPI
from api.chat import router as chat_router
from api.chat_audio import router as chat_audio_router

app = FastAPI(title="Metadata Chatbot Prototype")

app.include_router(chat_router, prefix="/api")
app.include_router(chat_audio_router, prefix="/api")

@app.get("/health")
def health():
    return {"ok": True}
