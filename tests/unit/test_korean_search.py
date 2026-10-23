from fitwitness.retrieval.pipeline import tokenize, extract_requirements

def test_korean_part_names_with_particles_keep_exact_ids_intact():
    assert 'bracket' in tokenize('장비에 고정할 브래킷을 찾아줘')
    assert 'shaft' in tokenize('회전축 대신 샤프트를 찾아줘')
    assert 'fw-000-0' in tokenize('FW-000-0')
    assert 'bracket' not in tokenize('FW-000-0')
    assert 'shaft' not in tokenize('축산 장비')
    assert any(r.field=='kind' and r.value=='bracket' for r in extract_requirements('브래킷을 찾아줘'))


def test_the_workshop_spelling_of_bracket_is_a_kind_too():
    # "실린더 고정 브라켓" was the second phrase a production engineer said they type (docs/user-test/2026-10-24-D.md)
    assert any(r.field == 'kind' and r.value == 'bracket' for r in extract_requirements('실린더 고정 브라켓'))
    assert 'bracket' in tokenize('브라켓을 찾아줘')
