"""The surgery classification table (수술분류표): which grade, 1 to 5, a named operation belongs to.

The grade decides the lump sum (``Coverage.surgery_table``), so it is the number a wrong reading costs the most. The
confirmation of surgery prints the name and, on these forms, the grade as well. The adjudicator holds the table
itself and checks the two against each other: a grade that disagrees with the table's grade for that name goes to a
person instead of being paid on whichever reading is wrong.

This is a synthetic table for the synthetic product. It is not a real insurer's classification, and the names are the
ones the document generator can print (``claims/vocab.py`` builds its surgeries from here, and a test keeps them in
step).
"""

import re

SURGERY_GRADES: dict[str, int] = {
    "체외충격파쇄석술": 1,
    "충수절제술": 2,
    "탈장교정술": 2,
    "안면윤곽술": 2,
    "담낭절제술": 3,
    "관혈적 정복술 및 내고정술": 3,
    "추간판 제거술": 3,
    "위아전절제술": 4,
    "유방절제술": 4,
    "관상동맥 스텐트 삽입술": 4,
    "심방중격결손 폐쇄술": 5,
    "혈전제거술": 5,
}


def table_grade(name: str | None) -> int | None:
    """The table's grade for a printed surgery name (spacing is not significant), or None when it is not listed."""
    if not name:
        return None
    key = re.sub(r"\s+", "", name)
    return next((grade for listed, grade in SURGERY_GRADES.items() if re.sub(r"\s+", "", listed) == key), None)
