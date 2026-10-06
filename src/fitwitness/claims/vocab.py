"""Closed vocabulary of the synthetic claim documents.

Everything the generator can print is listed here, so the embedded font subset can be
built from this file and the generator can assert it never prints a glyph it lacks.
"""

SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임"]
GIVEN = ["민준", "서연", "지호", "하은", "도윤", "서준", "지우", "예은", "현우", "수아"]
HOSPITALS = ["서울연합병원", "한빛의료원", "미래대학교병원", "강남제일병원", "푸른내과의원"]
DOCTORS = ["강현수", "박지원", "이수민", "최영훈", "정다은"]
DEPARTMENTS = {"surgery": "외과", "internal": "내과", "ortho": "정형외과", "neuro": "신경과", "onco": "혈액종양내과"}

# (KCD code, diagnosis name). Codes beginning with C44, Q and Z41 are policy exclusions.
DIAGNOSES: list[tuple[str, str]] = [
    ("K35.8", "급성 충수염"),
    ("K80.2", "담낭결석"),
    ("S82.3", "경골 원위부 골절"),
    ("M51.2", "요추 추간판 탈출증"),
    ("J18.9", "폐렴"),
    ("C16.9", "위의 악성신생물"),
    ("C50.9", "유방의 악성신생물"),
    ("D05.1", "유방 상피내암종"),
    ("I21.9", "급성 심근경색"),
    ("I63.9", "뇌경색증"),
    ("K40.9", "서혜부 탈장"),
    ("N20.0", "신장결석"),
    ("C44.9", "피부의 악성신생물"),
    ("Q21.1", "심방중격결손"),
    ("Z41.1", "미용 목적 성형수술"),
]
# code -> (surgery name, grade 1-5 on the surgery classification table)
SURGERIES: dict[str, tuple[str, int]] = {
    "K35.8": ("충수절제술", 2),
    "K80.2": ("담낭절제술", 3),
    "S82.3": ("관혈적 정복술 및 내고정술", 3),
    "M51.2": ("추간판 제거술", 3),
    "C16.9": ("위아전절제술", 4),
    "C50.9": ("유방절제술", 4),
    "I21.9": ("관상동맥 스텐트 삽입술", 4),
    "K40.9": ("탈장교정술", 2),
    "N20.0": ("체외충격파쇄석술", 1),
    "Q21.1": ("심방중격결손 폐쇄술", 5),
    "Z41.1": ("안면윤곽술", 2),
    "I63.9": ("혈전제거술", 5),
}

# Every literal the renderer prints. Labels end with ':' so the extractor can key on them.
LABELS = [
    "진단서", "입퇴원확인서", "수술확인서", "진료비 계산서·영수증",
    "환자성명:", "성명:", "환자:", "환자명:", "생년:", "병명:", "질병분류기호:", "진단일:", "발행기관:",
    "입원일:", "퇴원일:", "진료과:", "의료기관:", "수술명:", "수술일:", "수술분류:", "관련진단:", "시행기관:",
    "진료기간:", "급여본인부담금:", "비급여:", "합계:", "발행:", "문서번호:", "발급일:", "담당의:", "면허번호:",
    "위와 같이 진단함.", "위와 같이 확인함.", "연구용 합성 문서 · 실제 환자 정보가 아닙니다",
    "FitWitness synthetic claim document. Not a real medical record.",
    "종", "원", "금", "원정", "년", "월", "일", "제", "호", "~", "·", "-", "/", ".", ",", ":", "(", ")",
]
PRODUCT_NAMES = ["종합건강보험 A형", "암진단 플러스 B형"]


def all_text() -> str:
    parts = SURNAMES + GIVEN + HOSPITALS + DOCTORS + list(DEPARTMENTS.values()) + LABELS + PRODUCT_NAMES
    parts += [name for _, name in DIAGNOSES] + [code for code, _ in DIAGNOSES]
    parts += [name for name, _ in SURGERIES.values()]
    parts += ["0123456789", "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz", " "]
    return "".join(parts)
