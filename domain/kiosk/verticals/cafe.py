# domain/kiosk/verticals/cafe.py

KIOSK_VERTICAL_CAFE = {
    "kiosk_type": "cafe",

    # option group 정의(검증/재질문에 사용)
    "option_groups": {
        "temperature": {"type": "enum", "values": ["hot", "ice"]},
        "size": {"type": "enum", "values": ["tall", "grande", "venti"]},
        "shots": {"type": "integer", "min": 0, "max": 5},
        "takeout": {"type": "boolean"},
    },

    # 특정 intent에서 필수로 요구할 옵션 그룹 정책
    "policies": {
        "required_option_groups_by_intent": {
            "add_item": ["temperature", "size"]
        }
    }
}
