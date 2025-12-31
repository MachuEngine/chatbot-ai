# domain/education/schema.py
EDUCATION_SCHEMA = {
    "domain": "education",

    "intents": {
        # 1) 지식 탐구 (개념설명 + 단순질문 통합)
        # - "연음이 뭐야?", "이 단어 뜻 알려줘", "예문 더 줘"
        "ask_knowledge": {
            "required_slots": ["topic"],
            "optional_slots": [
                "question", "request_type",  # definition, usage, example, difference
                "level", "subject", "style", "language", "include_examples",
                # [NEW] Personalized & Context slots
                "native_language", "target_exam", "device_type"
            ]
        },

        # 2) 제출물 평가 (피드백 + 정답확인 통합)
        # - "이 문장 어때?", "이거 정답 맞아?", "채점해줘"
        "evaluate_submission": {
            "required_slots": ["student_answer"],
            "optional_slots": [
                "question", "evaluation_type", # grading, correction, feedback
                "rubric", "target_improvements",
                "level", "subject", "tone",
                "native_language"
            ]
        },

        # 3) 콘텐츠 변형 (요약 + 문장다듬기 + 번역 등)
        # - "요약해줘", "자연스럽게 고쳐줘"
        "process_content": {
            "required_slots": ["content"],
            "optional_slots": [
                "process_type", # summarize, rewrite, expand, translate
                "style", "tone", "length", "goal", "constraints"
            ]
        },

        # 4) 연습문제 생성
        "create_practice": {
            "required_slots": ["topic"],
            "optional_slots": [
                "level", "subject", "num_questions", "question_type", "difficulty", "include_answers",
                "target_exam"
            ]
        },

        # 5) UI/기능 네비게이션 (RAG 전용)
        "ask_ui_navigation": {
            "required_slots": [],
            "optional_slots": ["menu_name_query"]
        },
        
        # 6) 잡담/교육 외 (Guard용)
        "chitchat": {
            "required_slots": [],
            "optional_slots": []
        },

        "fallback": {"required_slots": [], "optional_slots": []},
    },

    "slots": {
        # --- Core Content ---
        "topic": {"type": "string", "max_len": 120},
        "question": {"type": "string", "max_len": 800},
        "content": {"type": "string", "max_len": 3000},
        "student_answer": {"type": "string", "max_len": 3000},
        "menu_name_query": {"type": "string", "max_len": 50},

        # --- Sub-types for Consolidated Intents ---
        "request_type": {
            "type": "enum",
            "values": ["definition", "usage", "example", "difference", "history", "general"]
        },
        "evaluation_type": {
            "type": "enum",
            "values": ["grading", "correction", "feedback", "explanation"]
        },
        "process_type": {
            "type": "enum",
            "values": ["summarize", "rewrite", "expand", "translate"]
        },

        # --- Context / Personalization (Sticky Candidates) ---
        "subject": {
            "type": "enum", 
            "values": ["korean", "english", "math", "science", "social", "history", "coding", "other"]
        },
        "level": {"type": "enum", "values": ["beginner", "intermediate", "advanced"]},
        "language": {"type": "enum", "values": ["ko", "en", "other"]},
        
        # [NEW] Learner Profile & Context Slots
        "native_language": {"type": "string", "max_len": 10}, # e.g., 'en', 'vi', 'ja'
        "target_exam": {"type": "string", "max_len": 50},     # e.g., 'TOPIK', 'SAT', 'IELTS'
        "user_age_group": {"type": "enum", "values": ["child", "teen", "adult"]},
        "weak_points": {
            "type": "array",
            "max_items": 10,
            "items": {"type": "string"}
        },
        
        # [NEW] System Environment Slots
        "device_type": {"type": "enum", "values": ["mobile", "web", "kiosk", "speaker"]},
        "output_format": {"type": "enum", "values": ["markdown", "text", "speech"]},

        # --- Style & Preference ---
        "style": {"type": "enum", "values": ["teacher", "friendly", "exam", "socratic", "formal"]},
        "tone": {"type": "enum", "values": ["strict", "warm", "neutral", "encouraging"]},
        "length": {"type": "enum", "values": ["short", "medium", "long", "detailed"]},
        
        # --- Modifiers ---
        "include_examples": {"type": "boolean"},
        "rubric": {
            "type": "array",
            "max_items": 5,
            "items": {"type": "enum", "values": ["grammar", "vocabulary", "fluency", "logic", "spelling"]}
        },
        "target_improvements": {
            "type": "array",
            "max_items": 5,
            "items": {"type": "enum", "values": ["fix_grammar", "make_natural", "make_polite", "simplify", "expand"]}
        },
        
        # --- Practice ---
        "num_questions": {"type": "integer", "min": 1, "max": 10},
        "question_type": {"type": "enum", "values": ["mcq", "short_answer", "essay", "mixed"]},
        "difficulty": {"type": "enum", "values": ["easy", "medium", "hard"]},
        "include_answers": {"type": "boolean"},

        # --- Rewrite Constraints ---
        "goal": {"type": "enum", "values": ["polish", "formalize", "simplify", "creative"]},
        "constraints": {"type": "string", "max_len": 200},
    },
}