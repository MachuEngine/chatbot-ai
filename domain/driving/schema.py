# domain/driving/schema.py

DRIVING_SCHEMA = {
    "domain": "driving",
    "intents": {
        "control_hardware": {
            "group": "vehicle",
            "required_slots": ["target_part", "action"],
            "optional_slots": ["location_detail"]
        },
        "control_hvac": {
            "group": "climate",
            "required_slots": ["action"],
            "optional_slots": ["target_temp", "seat_location", "fan_speed"]
        },
        "navigate_to": {
            "group": "navigation",
            "required_slots": ["destination"],
            "optional_slots": ["waypoint"]
        },
        "find_poi": {
            "group": "navigation",
            "required_slots": ["poi_type"],
            "optional_slots": ["sort_by"]
        },
        "general_chat": {
            "group": "assistant",
            "required_slots": [],
            "optional_slots": ["query"]
        },
        "fallback": {"group": "system", "required_slots": [], "optional_slots": []},
    },

    "slots": {
        # [핵심] values에 정의된 영어 값으로만 추출되도록 유도
        "target_part": {
            "type": "enum", 
            "values": ["window", "trunk", "frunk", "door", "light", "wiper", "mirror"]
        },
        "action": {
            "type": "enum", 
            "values": ["open", "close", "on", "off", "lock", "unlock", "up", "down"]
        },
        "location_detail": {
            "type": "enum", 
            "values": ["driver", "passenger", "rear_left", "rear_right", "all"]
        },
        
        # HVAC
        "target_temp": {"type": "integer", "min": 16, "max": 30},
        "seat_location": {"type": "enum", "values": ["driver", "passenger", "rear", "all"]},
        "fan_speed": {"type": "integer", "min": 1, "max": 5},

        # Navigation
        "destination": {"type": "string", "max_len": 100}, # 주소는 enum 불가
        "waypoint": {"type": "string", "max_len": 100},
        "poi_type": {"type": "enum", "values": ["charging_station", "parking", "restaurant", "cafe", "toilet"]},
        "sort_by": {"type": "enum", "values": ["distance", "price", "rating"]},

        # General
        "query": {"type": "string", "max_len": 500},
    }
}