# domain/driving/schema.py

DRIVING_SCHEMA = {
    "domain": "driving",
    "intents": {
        "control_hardware": {
            "group": "vehicle",
            "description": "창문, 트렁크, 라이트, 와이퍼 등 차량의 물리적 하드웨어 장치를 제어할 때 사용합니다.",
            "required_slots": ["target_part", "action"],
            "optional_slots": ["location_detail"]
        },
        "control_hvac": {
            "group": "climate",
            "description": "에어컨, 히터, 통풍 시트, 열선 등 차량의 공조 및 온도 조절 장치를 제어할 때 사용합니다.",
            "required_slots": ["action"],
            "optional_slots": ["target_temp", "seat_location", "fan_speed", "hvac_mode"]
        },
        "navigate_to": {
            "group": "navigation",
            "description": "특정 주소나 명칭을 가진 목적지로 내비게이션 경로 안내를 시작하거나 경유지를 설정할 때 사용합니다.",
            "required_slots": ["destination"],
            "optional_slots": ["waypoint"]
        },
        "find_poi": {
            "group": "navigation",
            "description": "주변의 식당, 주유소, 화장실, 충전소 등 특정 카테고리의 장소를 찾거나 검색할 때 사용합니다. (단순히 대화 중에 상호명이나 장소가 언급된 경우는 제외)",
            "required_slots": ["poi_type"],
            "optional_slots": ["sort_by"]
        },
        "general_chat": {
            "group": "assistant",
            "description": "차량 제어나 길 안내와 관련 없는 모든 일상 대화, 게임, 퀴즈, 농담, 일반적인 지식 질문 등을 처리합니다. 게임 중 힌트나 모호한 발화도 이곳에서 처리합니다.",
            "required_slots": [],
            "optional_slots": ["query"]
        },
        "fallback": {
            "group": "system",
            "description": "사용자의 요청을 이해할 수 없거나 시스템이 지원하지 않는 기능을 요청했을 때 사용합니다.",
            "required_slots": [], 
            "optional_slots": []
        },
    },

    "slots": {
        "target_part": {
            "type": "enum", 
            "values": [
                "window", "trunk", "frunk", "door_lock", "light", "wiper", "mirror", 
                "seat_heater", "seat_ventilation", "steering_wheel", "sunroof", "charge_port", "fuel_cap",
                "high_beam", "fog_light"
            ]
        },
        "action": {
            "type": "enum", 
            "values": ["open", "close", "on", "off", "lock", "unlock", "up", "down", "fold", "unfold", "tilt"]
        },
        "location_detail": {
            "type": "enum", 
            "values": ["driver", "passenger", "rear", "rear_left", "rear_right", "all"]
        },
        
        # HVAC
        "target_temp": {"type": "integer", "min": 16, "max": 32},
        "seat_location": {"type": "enum", "values": ["driver", "passenger", "rear", "all"]},
        "fan_speed": {"type": "integer", "min": 1, "max": 5},
        "hvac_mode": {"type": "enum", "values": ["heat", "cool", "auto", "dry", "defog", "fresh_air", "recirculation"]},

        # Navigation
        "destination": {"type": "string", "max_len": 100},
        "waypoint": {"type": "string", "max_len": 100},
        "poi_type": {"type": "enum", "values": ["charging_station", "parking", "restaurant", "cafe", "toilet"]},
        "sort_by": {"type": "enum", "values": ["distance", "price", "rating"]},

        # General
        "query": {"type": "string", "max_len": 500},
    }
}