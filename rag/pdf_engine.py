# rag/pdf_engine.py
import os
import io
import json
import requests
import numpy as np
from typing import List, Dict, Any
from pypdf import PdfReader
from sklearn.metrics.pairwise import cosine_similarity

OPENAI_API_URL = "https://api.openai.com/v1/embeddings"

class PDFEngine:
    def __init__(self):
        self.chunks: List[str] = []
        self.embeddings: Any = None  # numpy array
        self.has_data = False
        self.filename = ""

    def load_pdf(self, file_bytes: bytes, filename: str):
        reader = PdfReader(io.BytesIO(file_bytes))
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        
        # 1. Chunking (단순하게 500자 단위로 자름)
        chunk_size = 500
        overlap = 50
        self.chunks = []
        
        for i in range(0, len(text), chunk_size - overlap):
            chunk = text[i:i + chunk_size].strip()
            if len(chunk) > 50: # 너무 짧은 건 무시
                self.chunks.append(chunk)

        # 2. Embedding (OpenAI API)
        if self.chunks:
            self.embeddings = self._get_embeddings(self.chunks)
            self.has_data = True
            self.filename = filename
            print(f"[PDFEngine] Loaded {filename} with {len(self.chunks)} chunks.")

    def search(self, query: str, top_k: int = 3, threshold: float = 0.3) -> str:
        if not self.has_data:
            return ""

        # 1. 쿼리 임베딩
        query_emb = self._get_embeddings([query])[0]
        
        # 2. 코사인 유사도 계산
        # query_emb: (1536,), self.embeddings: (N, 1536)
        scores = cosine_similarity([query_emb], self.embeddings)[0]
        
        # 3. Top-K 추출
        top_indices = scores.argsort()[-top_k:][::-1]

        # 유사도 점수가 threshold 이상인 것만 필터링
        valid_indices = [i for i in top_indices if scores[i] >= threshold]
        
        results = []
        for idx in valid_indices:
            results.append(self.chunks[idx])
            
        return "\n---\n".join(results)

    def _get_embeddings(self, texts: List[str]) -> Any:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            print("[PDFEngine] Error: No API Key")
            return np.zeros((len(texts), 1536))

        # 배치 처리 (한 번에 보내기)
        payload = {
            "input": texts,
            "model": "text-embedding-3-small"
        }
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        try:
            resp = requests.post(OPENAI_API_URL, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            # 순서대로 벡터 추출
            vecs = [item["embedding"] for item in data["data"]]
            return np.array(vecs)
        except Exception as e:
            print(f"[PDFEngine] Embedding Error: {e}")
            return np.zeros((len(texts), 1536))

# 싱글톤 인스턴스 (메모리 공유용)
global_pdf_engine = PDFEngine()