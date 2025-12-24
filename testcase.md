✅ EDU 모드 테스트 케이스 5개 (curl)
1️⃣ 개념 설명
curl -s -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "연음이 뭐야?",
    "meta": {
      "client_session_id": "sess_edu_1",
      "mode": "edu"
    }
  }' | jq

2️⃣ 질문 응답
curl -s -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "받침이 왜 발음이 달라져?",
    "meta": {
      "client_session_id": "sess_edu_2",
      "mode": "edu"
    }
  }' | jq

3️⃣ 텍스트 요약
curl -s -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "다음 글을 요약해줘",
    "meta": {
      "client_session_id": "sess_edu_3",
      "mode": "edu",
      "content": "한국어 발음에는 연음, 동화, 축약 등의 규칙이 있다. 이러한 규칙은 발음을 자연스럽게 만들기 위해 사용된다."
    }
  }' | jq

4️⃣ 학습자 답변 피드백
```bash
curl -s -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "이 답변 평가해줘",
    "meta": {
      "client_session_id": "sess_edu_4",
      "mode": "edu",
      "student_answer": "서울은 발음이 어려워요"
    }
  }' | jq
  ```

5️⃣ 연습문제 생성
curl -s -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "연음 연습문제 만들어줘",
    "meta": {
      "client_session_id": "sess_edu_5",
      "mode": "edu",
      "topic": "연음"
    }
  }' | jq

✅ KIOSK 모드 테스트 케이스 5개 (curl)
1️⃣ 메뉴 주문 (필수 옵션 질문 유도)
curl -s -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "아메리카노 주세요",
    "meta": {
      "client_session_id": "sess_kiosk_1",
      "mode": "kiosk",
      "kiosk_type": "cafe",
      "store_id": "store_01",
      "device_type": "web"
    }
  }' | jq

2️⃣ 옵션 응답 (temperature)
curl -s -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "아이스로요",
    "meta": {
      "client_session_id": "sess_kiosk_1",
      "mode": "kiosk",
      "kiosk_type": "cafe",
      "store_id": "store_01",
      "device_type": "web"
    }
  }' | jq

3️⃣ 수량 포함 주문
curl -s -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "라떼 두 잔 주세요",
    "meta": {
      "client_session_id": "sess_kiosk_2",
      "mode": "kiosk",
      "kiosk_type": "cafe",
      "store_id": "store_01"
    }
  }' | jq

4️⃣ 메뉴 추천
curl -s -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "달달한 음료 추천해줘",
    "meta": {
      "client_session_id": "sess_kiosk_3",
      "mode": "kiosk",
      "kiosk_type": "cafe",
      "store_id": "store_01"
    }
  }' | jq

5️⃣ 존재하지 않는 메뉴
curl -s -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "콜드브루 말차 주세요",
    "meta": {
      "client_session_id": "sess_kiosk_4",
      "mode": "kiosk",
      "kiosk_type": "cafe",
      "store_id": "store_01"
    }
  }' | jq
