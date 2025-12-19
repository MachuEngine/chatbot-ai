# domain/kiosk/schema.py

KIOSK_SCHEMA = {
    "domain": "kiosk",

    "intents": {
        "add_item": {
            "required_slots": ["item_name", "quantity"],
            "optional_slots": ["option_groups", "notes"]
        },
        "modify_item": {
            "required_slots": ["target_item_ref"],
            "optional_slots": ["quantity_delta", "option_groups", "notes"]
        },
        "remove_item": {
            "required_slots": ["target_item_ref"],
            "optional_slots": []
        },
        "checkout": {
            "required_slots": [],
            "optional_slots": ["payment_method", "receipt_type"]
        },
        "cancel_order": {
            "required_slots": ["order_ref"],
            "optional_slots": ["reason"]
        },
        "refund_order": {
            "required_slots": ["order_ref"],
            "optional_slots": ["reason"]
        },
        "ask_store_info": {
            "required_slots": ["info_type"],
            "optional_slots": []
        },
        "request_help": {
            "required_slots": [],
            "optional_slots": ["help_type"]
        },
        "fallback": {"required_slots": [], "optional_slots": []},
    },

    "slots": {
        "item_name": {"type": "string", "values_source": "catalog_db"},
        "quantity": {"type": "integer", "min": 1, "max": 20},

        # 범용 옵션 구조
        "option_groups": {
            "type": "array",
            "max_items": 12,
            "items": {
                "group": {"type": "string"},
                "value": {"type": "string"},
            }
        },

        "notes": {"type": "string", "max_len": 200},

        "target_item_ref": {"type": "string"},
        "quantity_delta": {"type": "integer", "min": -20, "max": 20},

        "payment_method": {"type": "enum", "values": ["card", "cash", "mobile", "qr"]},
        "receipt_type": {"type": "enum", "values": ["paper", "mobile", "none"]},

        "order_ref": {"type": "string"},
        "reason": {"type": "string", "max_len": 200},

        "info_type": {"type": "enum", "values": ["wifi", "restroom", "hours", "parking", "location", "policy"]},
        "help_type": {"type": "enum", "values": ["staff_call", "payment_issue", "order_issue", "device_issue", "other"]},
    },
}
