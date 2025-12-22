EDU_SCHEMA = {
    "domain": "education",

    "intents": {
        # 개념 설명
        "explain_concept": {
            "required_slots": ["topic"],
            "optional_slots": ["level", "subject", "style", "include_examples", "example_type", "language", "length"]
        },

        # 질문 응답(학습 질문)
        "ask_question": {
            "required_slots": ["question"],
            "optional_slots": ["topic", "subject", "level", "style", "language", "context"]
        },

        # 피드백(서술/답안/글)
        "give_feedback": {
            "required_slots": ["student_answer"],
            "optional_slots": ["question", "rubric", "tone", "level", "subject", "language", "target_improvements"]
        },

        # 요약/정리
        "summarize": {
            "required_slots": ["content"],
            "optional_slots": ["length", "style", "language"]
        },

        # (추가) 연습문제/퀴즈 생성
        "create_practice": {
            "required_slots": ["topic"],
            "optional_slots": ["level", "subject", "num_questions", "question_type", "include_answers", "difficulty"]
        },

        # (추가) 풀이/정답 확인
        "check_answer": {
            "required_slots": ["question", "student_answer"],
            "optional_slots": ["rubric", "level", "subject", "language", "explain_steps", "tone"]
        },

        # (추가) 문장/답안 다듬기
        "rewrite": {
            "required_slots": ["content"],
            "optional_slots": ["style", "tone", "goal", "constraints", "language", "length"]
        },

        "fallback": {"required_slots": [], "optional_slots": []},
    },

    "slots": {
        # core
        "topic": {"type": "string", "max_len": 120},
        "question": {"type": "string", "max_len": 800},
        "content": {"type": "string", "max_len": 3000},
        "context": {"type": "string", "max_len": 1200},
        "student_answer": {"type": "string", "max_len": 3000},

        # personalization
        "subject": {"type": "enum", "values": ["korean", "english", "math", "science", "social", "cs", "other"]},
        "level": {"type": "enum", "values": ["elementary", "middle", "high", "adult"]},
        "language": {"type": "enum", "values": ["ko", "en", "other"]},

        # style/tone/length
        "style": {"type": "enum", "values": ["teacher", "friendly", "exam", "socratic"]},
        "tone": {"type": "enum", "values": ["strict", "warm", "neutral"]},
        "length": {"type": "enum", "values": ["short", "medium", "long"]},

        # examples
        "include_examples": {"type": "boolean"},
        "example_type": {"type": "enum", "values": ["daily", "exam", "analogy", "case", "code"]},

        # feedback rubric (제한된 선택 + 배열 허용)
        "rubric": {
            "type": "array",
            "max_items": 6,
            "items": {"type": "enum", "values": ["accuracy", "logic", "clarity", "evidence", "structure", "grammar"]}
        },
        "target_improvements": {
            "type": "array",
            "max_items": 6,
            "items": {"type": "enum", "values": ["shorten", "expand", "simplify", "add_examples", "fix_grammar", "improve_logic"]}
        },

        # practice
        "num_questions": {"type": "integer", "min": 1, "max": 20},
        "question_type": {"type": "enum", "values": ["mcq", "short", "essay", "mixed"]},
        "include_answers": {"type": "boolean"},
        "difficulty": {"type": "enum", "values": ["easy", "medium", "hard"]},

        # answer checking
        "explain_steps": {"type": "boolean"},

        # rewrite goal/constraints
        "goal": {"type": "enum", "values": ["polish", "simplify", "make_formal", "make_friendly", "exam_ready"]},
        "constraints": {"type": "string", "max_len": 300},
    },
}
