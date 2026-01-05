from domain.kiosk.schema import KIOSK_SCHEMA
from domain.education.schema import EDUCATION_SCHEMA
from domain.driving.schema import DRIVING_SCHEMA

# 명시적으로 등록하여 로딩 오류 방지
SCHEMAS = {
    "kiosk": KIOSK_SCHEMA,
    "education": EDUCATION_SCHEMA,
    "driving": DRIVING_SCHEMA,
}