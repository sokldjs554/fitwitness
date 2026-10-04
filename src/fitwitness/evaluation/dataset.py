"""Frozen pilot cases from held-out CAD families. Only evaluator reads gold."""
from decimal import Decimal
from pathlib import Path
import json
from fitwitness.contracts import DrawingRevision
from fitwitness.ingest.pdf import extract_pdf

FAMILIES = ['FW-F003', 'FW-F012', 'FW-F018', 'FW-F029']
CATEGORIES = ['match', 'dimension', 'material', 'missing', 'unit', 'revision']


def build_cases(root: Path):
    manifest = json.loads((root/'manifest.json').read_text())
    assert set(FAMILIES).issubset(manifest['family_splits']['test'])
    gold = json.loads((root/'gold/labels.json').read_text())
    cases = []
    for family in FAMILIES:
        entries = [d for d in manifest['document_entries'] if d['family_id']==family]
        base = entries[0]
        truth = gold[base['id']]
        dimension = 'hole_spacing' if truth.get('hole_spacing') is not None else 'width'
        need = Decimal(str(truth[dimension]))
        for category, variant in zip(CATEGORIES, [0,1 if dimension=='hole_spacing' else 4,2,3,0,5]):
            entry = entries[variant]
            expected = {'match':'match','dimension':'mismatch','material':'mismatch',
                        'missing':'unknown','unit':'match',
                        'revision':'mismatch' if dimension=='hole_spacing' else 'match'}[category]
            unit = 'cm' if category=='unit' else 'mm'
            value = str(need / 10 if unit=='cm' else need)
            requirements = [dict(id='dimension',field=dimension,value=dict(low=value,high=value),unit=unit),
                            dict(id='material',field='material',value='SUS304')]
            # Verify the independent generator labels agree with the declared category.
            observed = gold[entry['id']]
            checks = [observed.get(dimension)==float(need), observed.get('material')=='SUS304']
            independent = 'unknown' if observed.get('material') is None and checks[0] else ('match' if all(checks) else 'mismatch')
            assert independent==expected, (family,category,observed)
            revision = DrawingRevision(tenant_id='evaluation',effective_from='2026-10-04T00:00:00+00:00',
                **{k:entry[k] for k in ['id','document_id','drawing_number','family_id','revision_label','supersedes','kind','title','source_hash']})
            facts = extract_pdf((root/entry['pdf']).read_bytes(), revision)
            facts = [f.model_copy(update={'id':f'F{i}'}) for i,f in enumerate(facts) if f.field in (dimension,'material')]
            cases.append(dict(case_id=f'{family}-{category}', family_id=family, category=category,
                drawing_number=entry['drawing_number'],revision=entry['revision_label'],source_hash=entry['source_hash'],
                query=f'{dimension} {value}{unit}, material SUS304. Evaluate the supplied current revision.',
                requirements=requirements, facts=[f.model_dump(mode='json') for f in facts], expected=expected,
                revision_id=entry['id']))
    return cases
