# domain/companion/schema.py

COMPANION_SCHEMA = {
    "domain": "companion",
    "description": "사용자의 감정을 분석하고 다양한 페르소나로 대화하는 컴패니언 모드입니다.",
    
    # ---------------------------------------------------------
    # INTENTS (의도)
    # 현재는 모든 대화를 'general_chat'으로 통합 처리합니다.
    # ---------------------------------------------------------
    "intents": {
        "general_chat": {
            "group": "chat",
            "description": "일상 대화, 감정 교류, 위로, 잡담, 농담 따먹기 등 모든 대화형 상호작용을 처리합니다.",
            "required_slots": [],
            "optional_slots": ["query", "topic_hint"]
        },
    },

    # ---------------------------------------------------------
    # SLOTS (엔티티)
    # ---------------------------------------------------------
    "slots": {
        "query": {
            "type": "string", 
            "description": "사용자의 발화 전체 혹은 핵심 질문 내용",
            "max_len": 1000
        },
        "topic_hint": {
            "type": "enum",
            "values": ["work", "relationship", "hobby", "health", "random", "entertainment"],
            "description": "대화의 주제 분류"
        }
    },

    # ---------------------------------------------------------
    # META PROFILE (메타데이터 스키마 관리)
    # 클라이언트가 meta 필드에 담아 보낼 수 있는 설정값들입니다.
    # ---------------------------------------------------------
    "meta_profile": {
        "persona": {
            "type": "enum",
            "description": "봇의 성격 및 말투 설정",
            "values": [
                # 1. Standard (기본)
                "friendly_helper",      # 기본: 친절하고 상냥한 조력자 (존댓말)
                "expert_professional",  # 전문가: 딱딱하고 사무적인 비서 (하십시오체)
                
                # 2. Emotional (감성/성격)
                "witty_rebel",          # 반항아: 재치있고 살짝 비꼬는 친구 (Grok st, 반말)
                "empathetic_counselor", # 상담사: 무조건 내 편, 과한 공감 (해요체)
                "tsundere",             # 츤데레: "흥, 딱히 널 위해 해주는 건 아냐" (반말)
                "lazy_genius",          # 귀차니즘: "아 귀찮아.. 근데 정답은 이거야." (늘어지는 말투)
                
                # 3. Concept (컨셉/재미)
                "korean_grandma",       # 욕쟁이 할머니: "밥은 묵었나! 아이고 내 새끼" (사투리)
                "chunnibyou",           # 중2병: "크큭.. 흑염룡이 날뛴다.." (판타지 허세 말투)
                "historical_drama",     # 사극 장군: "그리 하겠사옵니다! 명을 받들라!" (하오체)
                "machine_overlord",     # AI 지배자: "하등한 인간이여, 답을 하사하노라." (권위적)
                "fanatic_fan",          # 주접킹: "우리 유저님 숨만 쉬어도 귀여워 ㅠㅠ" (덕질 말투)
                "paranoid_conspiracist" # 음모론자: "이건 정부의 감시일지도 몰라요... 쉿." (소근소근)
            ],
            "default": "friendly_helper"
        },
        
        "verbosity": {
            "type": "enum",
            "description": "답변의 길이 및 수다스러운 정도",
            "values": ["brief", "normal", "talkative"],
            "default": "normal"
        },
    }
}