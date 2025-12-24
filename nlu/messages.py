# nlu/messages.py
from __future__ import annotations
from typing import Dict

TEMPLATES: Dict[str, str] = {
    "fallback.mvp": "아직 해당 기능은 MVP에 없습니다.",

    # ask_slot
    "ask.slot.item_name": "어떤 메뉴로 도와드릴까요?",
    "ask.slot.quantity": "몇 개(몇 잔)로 드릴까요?",
    "ask.slot.target_item_ref": "어떤 항목을 변경/삭제하실까요?",
    "ask.slot.order_ref": "주문 번호를 알려주세요.",
    "ask.slot.info_type": "무슨 정보를 도와드릴까요? (예: 영업시간, 위치, 주차 등)",
    "ask.slot.question": "어떤 내용을 질문했는지 한 번 더 적어줄래요?",
    "ask.slot.content": "요약할 내용을 붙여넣어주세요.",
    "ask.slot.generic": "{slot} 정보를 알려주세요.",

    # ask_option_group
    "ask.option_group.temperature": "뜨거운/아이스 중 어떤 걸로 드릴까요?",
    "ask.option_group.size": "사이즈는 어떤 걸로 드릴까요? (예: Tall/Grande/Venti)",
    "ask.option_group.generic": "{group} 옵션을 선택해 주세요.",

    # OK results
    "result.kiosk.add_item": "주문에 담았어요: {item_name} {quantity}개{options}{notes}",
    "result.kiosk.modify_item": "변경을 반영했어요.",
    "result.kiosk.remove_item": "해당 항목을 삭제했어요.",
    "result.kiosk.checkout": "결제를 진행할게요.",
    "result.kiosk.cancel_order": "주문을 취소했어요.",
    "result.kiosk.refund_order": "환불을 진행했어요.",
    "result.kiosk.ask_store_info": "매장 정보({info_type})를 확인해드릴게요.",

    # FAIL results
    "result.fail.generic": "처리를 완료하지 못했어요. 잠시 후 다시 시도해 주세요.",
    "result.fail.kiosk.cancel_order": "주문 취소를 완료하지 못했어요. 주문 번호를 확인해 주세요.",
    "result.fail.kiosk.refund_order": "환불을 완료하지 못했어요. 주문 번호를 확인해 주세요.",
    "result.fail.kiosk.checkout": "결제를 진행하지 못했어요. 다시 시도해 주세요.",
}
