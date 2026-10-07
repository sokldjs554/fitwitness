"""The OCR reader's rules, on hand-made tokens (no Tesseract needed) and on the scan simulator."""
import numpy as np
from PIL import Image
from fitwitness.claims import ocr
from fitwitness.claims.extract import LABELS, _kind
from fitwitness.claims.scan import LEVELS, degrade

LABEL_KINDS = {label: _kind(field) for label, field in LABELS.items()}
H = 20  # line height of the hand-made page


def tok(text, x0, y=100, conf=95.0, width=None, line=(1, 1, 1)):
    return ocr.Token(text, x0, x0 + (width if width is not None else 12 * len(text)), y, y + H, conf, line)


def syllables(text, x0, y=100, conf=93.0, gap=3, line=(1, 1, 1)):
    """Hangul as Tesseract hands it back: one token per syllable."""
    out, x = [], x0
    for ch in text:
        out.append(ocr.Token(ch, x, x + 18, y, y + H, conf, line))
        x += 18 + gap
    return out


def read(tokens):
    return [(label, value, round(conf, 2)) for label, value, _, conf in ocr.pairs(tokens, "d1", 1, (1000, 1400), LABEL_KINDS)]


def test_syllables_become_a_label_and_the_value_follows_it():
    tokens = syllables("입원일", 100) + [tok(":", 190, width=6), tok("2025-12-07", 400, conf=88.0)]
    assert read(tokens) == [("입원일:", "2025-12-07", 0.88)]


def test_a_label_split_in_two_words_is_found_whole():
    tokens = syllables("환자", 100, line=(1, 1, 1)) + [tok("성명:", 150, width=40), tok("박지우", 400, conf=93.0)]
    assert read(tokens)[0][:2] == ("환자성명:", "박지우")


def test_a_value_keeps_the_spaces_the_scanner_saw_and_not_the_ones_it_invented():
    tokens = syllables("병명", 100) + [tok(":", 150, width=6)] + syllables("급성", 400) + syllables("심근경색", 470, gap=2)
    assert read(tokens)[0][1] == "급성 심근경색"


def test_value_length_follows_the_kind_of_field():
    tokens = syllables("입원일", 100) + [tok(":", 190, width=6), tok("2025-12-07", 400), tok("stamp", 560), tok("smudge", 640)]
    assert read(tokens)[0][1] == "2025-12-07"  # a date is one word; whatever follows it is something else


def test_a_poorly_read_word_after_the_value_is_not_part_of_it():
    tokens = syllables("성명", 100) + [tok(":", 150, width=6), tok("박지우", 400, conf=92.0), tok("Ne(it", 480, conf=31.0)]
    assert read(tokens)[0][:2] == ("성명:", "박지우")


def test_noise_below_the_floor_is_dropped_and_the_next_label_ends_a_value():
    tokens = syllables("퇴원일", 100) + [tok(":", 190, width=6), tok("2025.12.17", 400, conf=90.0)] + \
        syllables("진료과", 100, y=140, line=(1, 1, 2)) + [tok(":", 220, y=140, width=6, line=(1, 1, 2)), tok("내과", 400, y=140, line=(1, 1, 2))]
    assert [(l, v) for l, v, _ in read(tokens)] == [("퇴원일:", "2025.12.17")]  # 진료과 is a stop label, not a field


def test_confidence_is_the_weakest_token_of_label_and_value():
    tokens = syllables("합계", 100, conf=96.0) + [tok(":", 150, width=6, conf=96.0), tok("3,884,730", 400, conf=71.0), tok("원", 520, conf=95.0)]
    assert read(tokens) == [("합계:", "3,884,730 원", 0.71)]


def test_letters_are_turned_back_into_digits_only_where_a_digit_must_be():
    assert ocr.repair_date("2O25-l2-O7") == "2025-12-07"
    assert ocr.repair_date("2025.12,30") == "2025.12.30"
    assert ocr.repair_date("2025,12.30") == "2025.12.30"
    assert ocr.repair_date("내과") == "내과"  # not a date: left for the validator to flag
    assert ocr.repair_code("121,9") == "I21.9"
    assert ocr.repair_code("K35.8") == "K35.8" and ocr.repair_code("C5O.9") == "C50.9"
    assert ocr.repair_code("급성") == "급성"
    assert ocr.repair_amount("3,884,73O 원") == "3,884,730 원" and ocr.repair_amount("푸른내과") == "푸른내과"


def test_the_scan_levels_get_worse_in_every_dimension():
    order = ["clean", "light", "medium", "heavy"]
    for key in ("rotate", "blur", "noise", "contrast"):
        values = [LEVELS[l][key] for l in order]
        assert values == sorted(values, reverse=(key == "contrast")), key
    assert [LEVELS[l]["jpeg"] for l in order] == sorted([LEVELS[l]["jpeg"] for l in order], reverse=True)


def test_degradation_is_reproducible_and_damages_more_at_a_higher_level():
    page = Image.new("L", (300, 200), 255)
    for x in range(20, 280, 4):
        for y in range(50, 60):
            page.putpixel((x, y), 0)
    a = np.asarray(degrade(page, "medium", 7), dtype=np.int16)
    assert np.array_equal(a, np.asarray(degrade(page, "medium", 7), dtype=np.int16)), "same seed, same damage"
    assert not np.array_equal(a, np.asarray(degrade(page, "medium", 8), dtype=np.int16)), "another document, other damage"
    base = np.asarray(page, dtype=np.int16)
    err = {lv: float(np.abs(np.asarray(degrade(page, lv, 3), dtype=np.int16) - base).mean()) for lv in LEVELS}
    assert err["clean"] < err["light"] < err["medium"] < err["heavy"]


def test_an_amount_is_refused_when_it_is_not_written_like_an_amount():
    assert ocr.amount_value(ocr.repair_amount("3,884,730 원")) == 3884730
    assert ocr.amount_value(ocr.repair_amount("금 3,884,730원정")) == 3884730
    assert ocr.amount_value(ocr.repair_amount("3884730원")) == 3884730
    assert ocr.amount_value(ocr.repair_amount("3,884,73O원!")) == 3884730  # noise beside the number is dropped
    assert ocr.amount_value("2,909,1912!") is None  # a smudge read as a digit breaks the groups of three
    assert ocr.amount_value("29,09,191원") is None
    assert ocr.amount_value("원") is None


def test_a_two_read_as_the_letter_z_at_the_head_of_a_code():
    assert ocr.repair_code("241.1") == "Z41.1"


def test_a_receipt_total_is_accepted_only_when_the_lines_above_it_add_up():
    lines = ["1,745,514원", "1,163,677 원"]
    assert ocr.checked_amount("2,909,191원", lines) == (2909191, True)
    assert ocr.checked_amount("2,909,1912!", lines) == (2909191, True)  # the unit was misread; the sum confirms the number
    assert ocr.checked_amount("2,909,181원", lines) == (None, False)  # a digit misread in the total
    assert ocr.checked_amount("2,909,191원", ["1,745,5l4원", "1,163,677 원"]) == (2909191, True)  # a letter for a digit is repaired first
    assert ocr.checked_amount("2,909,191원", []) == (2909191, False)  # nothing to check against: written like an amount is enough
    assert ocr.checked_amount("2,909,1912!", []) == (None, False)  # ... but a number with a smudge is not
    assert ocr.checked_amount("원", lines) == (None, False)


def test_a_grade_whose_syllable_was_read_as_a_digit():
    assert ocr.repair_grade("38") == "3종" and ocr.repair_grade("3종") == "3종" and ocr.repair_grade("종") == "종"


def test_a_zero_at_the_head_of_a_code_is_not_guessed_into_a_letter():
    assert ocr.repair_code("005.1") == "005.1"  # O, D or Q: left as read, it fails the code's shape and goes to a person
