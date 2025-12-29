from __future__ import annotations
import re
from typing import Tuple

EDU_ALLOW_PATTERNS = [
    r"발음", r"받침", r"연음", r"동화", r"축약", r"구개음화", r"된소리", r"표준발음",
    r"띄어쓰기", r"맞춤법", r"문법", r"어휘", r"단어", r"의미", r"예문", r"문장",
    r"요약", r"늘려", r"바꿔", r"첨삭", r"교정",
    r"topik", r"한국어", r"한글", r"조사", r"어미", r"품사",
]

NON_EDU_BLOCK_PATTERNS = [
    r"주식", r"코인", r"비트코인", r"이더리움", r"테슬라", r"삼성전자", r"얼마야", r"가격",
    r"환율", r"주가", r"차트", r"매수", r"매도",
]

def is_edu_relevant(user_message: str) -> Tuple[bool, str]:
    msg = (user_message or "").strip().lower()
    if not msg:
        return True, "empty_ok"

    for p in NON_EDU_BLOCK_PATTERNS:
        if re.search(p, msg, re.IGNORECASE):
            return False, "non_edu_block_keyword"

    for p in EDU_ALLOW_PATTERNS:
        if re.search(p, msg, re.IGNORECASE):
            return True, "edu_allow_keyword"

    # 애매하면 통과(과차단 방지)
    return True, "ambiguous_allow"
