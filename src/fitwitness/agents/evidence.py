"""Keep retrieved candidates separate from evidence actually inspected by an agent."""
from fitwitness.contracts import Candidate, Fact
from fitwitness.agents.tools import SCHEMAS


class EvidenceSession:
    def __init__(self, candidates, available, *, restored=False):
        self.available = set(available)
        self.candidates = [c.model_copy(deep=True) for c in candidates]
        if not restored:
            for c in self.candidates:
                c.facts = []

    def observe(self, op, result):
        if op.name not in self.available:
            raise ValueError('unavailable tool')
        known = {c.revision_id: c for c in self.candidates}
        if op.name.startswith('search_'):
            for item in result:
                c = Candidate.model_validate(item)
                if c.revision_id not in known:
                    c.facts = []
                    self.candidates.append(c)
                    known[c.revision_id] = c
            return
        if op.name == 'compare_revisions':
            groups = [(op.arguments['old_id'], result['old']), (op.arguments['new_id'], result['new'])]
        else:
            groups = [(op.arguments['revision_id'], result)]
        validated = [(rid, [Fact.model_validate(f) for f in items]) for rid, items in groups]
        if any(f.source.revision_id != rid for rid, facts in validated for f in facts):
            raise ValueError('observation source does not match requested revision')
        for rid, facts in validated:
            if rid in known:
                merged = {f.id: f for f in known[rid].facts}
                merged.update({f.id: f for f in facts})
                known[rid].facts = list(merged.values())

    def context(self, query, requirements, observations, decisions=()):
        return {
            'query': query,
            'requirements': [{k: r[k] for k in ('field', 'operator', 'value', 'unit', 'required') if k in r} for r in requirements],
            'candidates': [{'revision_id': c.revision_id, 'scores': c.scores,
                            'inspected': [{'id': f.id, 'field': f.field, 'value': f.model_dump(mode='json')['value'], 'unit': f.unit, 'certainty': f.certainty} for f in c.facts]} for c in self.candidates],
            'observations': [{'tool': x['tool'], 'items': len(x['result'])} for x in observations[-8:]],
            'decisions': [{'revision_id': d['revision_id'], 'verdict': d['verdict']} for d in decisions],
            'tools': {k: SCHEMAS[k].model_json_schema() for k in sorted(self.available)},
        }

    @staticmethod
    def needs_more(decisions, iterations, stop):
        return not stop and iterations < 3 and (not decisions or any(d['verdict'] == 'unknown' for d in decisions))
