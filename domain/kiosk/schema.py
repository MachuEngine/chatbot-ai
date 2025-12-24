# domain/kiosk/schema.py

KIOSK_SCHEMA = {
    "domain": "kiosk",

    "intents": {
        # -------------------------
        # ORDERING (주문/거래)
        # -------------------------
        "add_item": {
            "group": "ordering",
            "required_slots": ["item_name", "quantity"],
            "optional_slots": ["option_groups", "notes"]
        },
        "modify_item": {
            "group": "ordering",
            "required_slots": ["target_item_ref"],
            "optional_slots": ["quantity_delta", "option_groups", "notes"]
        },
        "remove_item": {
            "group": "ordering",
            "required_slots": ["target_item_ref"],
            "optional_slots": []
        },
        "view_cart": {
            "group": "ordering",
            "required_slots": [],
            "optional_slots": []
        },
        "checkout": {
            "group": "ordering",
            "required_slots": [],
            "optional_slots": ["payment_method", "receipt_type"]
        },
        "cancel_order": {
            "group": "ordering",
            "required_slots": ["order_ref"],
            "optional_slots": ["reason"]
        },
        "refund_order": {
            "group": "ordering",
            "required_slots": ["order_ref"],
            "optional_slots": ["reason"]
        },

        # -------------------------
        # INFORMATION (정보/조회)
        # -------------------------
        "ask_store_info": {
            "group": "info",
            "required_slots": ["info_type"],
            "optional_slots": []
        },
        "ask_menu": {
            "group": "info",
            "required_slots": [],
            "optional_slots": ["category", "dietary", "allergens", "sort_by"]
        },
        "ask_item_info": {
            "group": "info",
            "required_slots": ["item_name"],
            "optional_slots": ["info_fields"]
        },
        "ask_price": {
            "group": "info",
            "required_slots": ["item_name"],
            "optional_slots": ["option_groups", "quantity"]
        },

        # ✅ 추천: 주문 옵션(temperature)과 혼동 방지 위해 temperature_hint로 분리 권장
        "ask_recommendation": {
            "group": "info",
            "required_slots": [],
            "optional_slots": ["category", "budget_max", "dietary", "spicy_level", "temperature_hint"]
        },

        "ask_order_status": {
            "group": "info",
            "required_slots": ["order_ref"],
            "optional_slots": []
        },
        "request_help": {
            "group": "info",
            "required_slots": [],
            "optional_slots": ["help_type"]
        },

        "fallback": {"group": "system", "required_slots": [], "optional_slots": []},
    },

    "slots": {
        # ---- core ordering ----
        "item_name": {"type": "string", "values_source": "catalog_db", "max_len": 80},
        "quantity": {"type": "integer", "min": 1, "max": 20},

        # ✅ option_groups는 array[{group,value}] 유지 추천
        "option_groups": {
            "type": "array",
            "max_items": 12,
            "items": {
                "group": {"type": "string", "max_len": 40},
                "value": {"type": "string", "max_len": 60},
            }
        },

        "notes": {"type": "string", "max_len": 200},

        "target_item_ref": {"type": "string", "max_len": 80},
        "quantity_delta": {"type": "integer", "min": -20, "max": 20},

        "payment_method": {"type": "enum", "values": ["card", "cash", "mobile", "qr"]},
        "receipt_type": {"type": "enum", "values": ["paper", "mobile", "none"]},

        "order_ref": {"type": "string", "max_len": 80},
        "reason": {"type": "string", "max_len": 200},

        # ---- store info ----
        "info_type": {
            "type": "enum",
            "values": ["wifi", "restroom", "hours", "parking", "location", "policy", "pickup", "contact"]
        },

        "help_type": {
            "type": "enum",
            "values": ["staff_call", "payment_issue", "order_issue", "device_issue", "refund_issue", "other"]
        },

        # ---- menu/info browsing ----
        "category": {"type": "string", "values_source": "catalog_category", "max_len": 60},
        "sort_by": {"type": "enum", "values": ["popular", "price_low", "price_high", "new", "recommended"]},

        # dietary/allergen/reco
        "dietary": {"type": "enum", "values": ["none", "vegetarian", "vegan", "halal", "gluten_free"]},
        "allergens": {
            "type": "array",
            "max_items": 8,
            "items": {"type": "enum", "values": ["milk", "egg", "wheat", "soy", "peanut", "tree_nut", "fish", "shellfish"]}
        },

        "info_fields": {
            "type": "array",
            "max_items": 8,
            "items": {"type": "enum", "values": ["price", "options", "calories", "ingredients", "allergens", "spicy_level", "availability"]}
        },

        "budget_max": {"type": "integer", "min": 0, "max": 200000},

        # optional recommendation hints (업종 중립)
        "spicy_level": {"type": "enum", "values": ["none", "mild", "medium", "hot"]},

        # ✅ 주문 옵션으로서의 온도
        "temperature": {"type": "enum", "values": ["hot", "iced"]},

        # ✅ 추천 제약(선호)으로서의 온도 힌트
        "temperature_hint": {"type": "enum", "values": ["hot", "iced"]},
    },
}
