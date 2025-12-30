from fastapi import FastAPI, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from api.chat import router as chat_router

from api.chat import router as chat_router
from api.chat_audio import router as chat_audio_router

from rag.pdf_engine import global_pdf_engine

app = FastAPI(title="Metadata Chatbot Prototype")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix="/api")
app.include_router(chat_audio_router, prefix="/api")


@app.get("/health")
def health():
    return {"ok": True}

@app.post("/api/upload_pdf")
async def upload_pdf(file: UploadFile = File(...)):
    content = await file.read()
    global_pdf_engine.load_pdf(content, file.filename)
    return {
        "filename": file.filename, 
        "status": "success", 
        "chunks": len(global_pdf_engine.chunks)
    }


# static/index.html (chatbot-tester) 서빙
# - 반드시 include_router 이후에 mount해야 /api 경로를 덮지 않음
app.mount("/", StaticFiles(directory="static", html=True), name="static")
