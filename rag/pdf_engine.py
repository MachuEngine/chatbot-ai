# rag/pdf_engine.py
import os
import io
import re
import time
import requests
import numpy as np
from typing import List, Any
from pypdf import PdfReader
from sklearn.metrics.pairwise import cosine_similarity

OPENAI_API_URL = "https://api.openai.com/v1/embeddings"

class PDFEngine:
    def __init__(self):
        self.chunks: List[str] = []
        self.embeddings: Any = None
        self.has_data = False
        self.filename = ""

    def _clean_text(self, text: str) -> str:
        """텍스트 정제: 다중 공백/줄바꿈을 단일 공백으로 치환"""
        # 1. 과도한 줄바꿈/공백 제거
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def load_pdf(self, file_bytes: bytes, filename: str):
        print(f"[PDFEngine] Loading file: {filename}...")
        try:
            reader = PdfReader(io.BytesIO(file_bytes))
            raw_text = ""
            for i, page in enumerate(reader.pages):
                extracted = page.extract_text()
                if extracted:
                    raw_text += extracted + " "
            
            # [보강 1] 텍스트 정제 (Normalization)
            clean_text = self._clean_text(raw_text)
            
            if not clean_text or len(clean_text) < 50:
                print(f"[PDFEngine] ⚠️ Warning: Extracted text is too short or empty. (Scanned PDF?)")
                self.has_data = False
                return

            # [보강 2] 청킹 전략 최적화 (강의자료 특성 반영)
            # - Chunk Size: 500 -> 400 (조금 더 잘게 쪼갬)
            # - Overlap: 50 -> 100 (문맥 끊김 방지)
            chunk_size = 400
            overlap = 100
            self.chunks = []
            
            for i in range(0, len(clean_text), chunk_size - overlap):
                chunk = clean_text[i:i + chunk_size].strip()
                if len(chunk) > 30: 
                    self.chunks.append(chunk)

            if not self.chunks:
                print("[PDFEngine] ⚠️ No valid chunks created.")
                self.has_data = False
                return

            print(f"[PDFEngine] Created {len(self.chunks)} chunks. Starting embedding...")

            # 3. Embedding Generation
            self.embeddings = self._get_embeddings(self.chunks)
            
            if np.all(self.embeddings == 0):
                print("[PDFEngine] ❌ Embedding Failed (All zeros).")
                self.has_data = False
            else:
                self.has_data = True
                self.filename = filename
                print(f"[PDFEngine] ✅ Successfully Loaded '{filename}'")

        except Exception as e:
            print(f"[PDFEngine] Load Error: {e}")
            self.has_data = False

    def search(self, query: str, top_k: int = 3, threshold: float = 0.18) -> str:
        """
        threshold: 0.18 (너무 낮지도 높지도 않은 적정값)
        """
        if not self.has_data or not self.chunks:
            return ""

        # 1. Query Embedding
        query_emb = self._get_embeddings([query])[0]
        if np.all(query_emb == 0):
            return ""
        
        # 2. Cosine Similarity
        scores = cosine_similarity([query_emb], self.embeddings)[0]
        
        # [Debug Log] 유사도 점수 확인
        max_idx = scores.argmax()
        max_score = scores[max_idx]
        print(f"[PDFEngine] Search: '{query}' | Max Score: {max_score:.4f} (Threshold: {threshold})")

        # 3. Filtering
        top_indices = scores.argsort()[-top_k:][::-1]
        results = []
        for idx in top_indices:
            if scores[idx] >= threshold:
                results.append(self.chunks[idx])
        
        return "\n---\n".join(results)

    def _get_embeddings(self, texts: List[str]) -> Any:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            print("[PDFEngine] Error: No API Key")
            return np.zeros((len(texts), 1536))

        # [보강 3] 배치 처리 및 재시도(Retry) 로직
        batch_size = 20  # 한 번에 너무 많이 보내면 에러 날 수 있음
        all_embeddings = []

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            payload = {
                "input": batch,
                "model": "text-embedding-3-small"
            }
            
            # Simple Retry Logic (최대 2회)
            for attempt in range(2):
                try:
                    resp = requests.post(OPENAI_API_URL, json=payload, headers=headers, timeout=20)
                    resp.raise_for_status()
                    data = resp.json()
                    vecs = [item["embedding"] for item in data["data"]]
                    all_embeddings.extend(vecs)
                    break # Success -> Next batch
                except Exception as e:
                    print(f"[PDFEngine] Embedding Batch Error (Attempt {attempt+1}): {e}")
                    time.sleep(1) # Wait 1s
                    if attempt == 1: # Failed twice
                        # 실패 시 해당 배치만큼 0 벡터 채움 (전체 실패 방지)
                        all_embeddings.extend([np.zeros(1536) for _ in range(len(batch))])

        return np.array(all_embeddings)

global_pdf_engine = PDFEngine()