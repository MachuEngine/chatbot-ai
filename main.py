from fastapi import FastAPI
from api.chat import router as chat_router

app = FastAPI(title="Metadata Chatbot Prototype")

app.include_router(chat_router, prefix="/api")

@app.get("/health")
def health():
    return {"ok": True}
