# api/chat.py
import uuid
from fastapi import APIRouter
from models.api_models import ChatRequest, ChatResponse
from session.session_manager import SessionManager
from nlu.router import pick_candidates
from nlu.llm_client import nlu_with_llm
from nlu.validator import validate_and_build_action
from utils.logging import log_event
from nlu.normalizer import apply_session_rules


router = APIRouter()
sessions = SessionManager()

@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    trace_id = uuid.uuid4().hex[:12]

    state = sessions.get(req.meta.client_session_id)
    log_event(trace_id, "request", {"meta": req.meta.model_dump(), "user_message": req.user_message})

    candidates = pick_candidates(req, state)
    log_event(trace_id, "candidates", {"candidates": candidates})

    nlu = nlu_with_llm(req, state, candidates)
    nlu = apply_session_rules(state, nlu, req.user_message)  # ✅ 반드시 있어야 함
    log_event(trace_id, "nlu_normalized", {"nlu": nlu})

    action, new_state = validate_and_build_action(req, state, nlu)
    log_event(trace_id, "action", {"action": action, "new_state": new_state})

    sessions.set(req.meta.client_session_id, new_state)

    return ChatResponse(trace_id=trace_id, reply=action["reply"], state=new_state)
