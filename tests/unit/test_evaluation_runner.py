from types import SimpleNamespace
import json
import pytest


@pytest.mark.parametrize('provider,expected_seeds', [('local',[1701,1702]),('anthropic',None)])
def test_protocol_does_not_claim_unapplied_api_seeds(tmp_path,monkeypatch,provider,expected_seeds):
    from fitwitness.evaluation import runner
    case=dict(case_id='empty-evidence',family_id='a',category='missing',query='inspect',
              requirements=[],facts=[],expected='unknown',revision_id='r')
    monkeypatch.setattr(runner,'build_cases',lambda root:[case])
    class RecordedResponse:
        metadata={'model_id':'test-only'}
        def predict(self,payload,seed):
            return '{"verdict":"unknown","citations":[]}',dict(input_tokens=1,output_tokens=1,cost_usd=0)
    monkeypatch.setattr(runner,'LocalModel',lambda path:RecordedResponse())
    monkeypatch.setattr(runner,'APIModel',lambda *args:RecordedResponse())
    runner.run(SimpleNamespace(output=str(tmp_path/'result'),corpus='unused',provider=provider,
                               repeats=2,model_path='unused',model='test',max_cost_usd=1))
    report=json.loads((tmp_path/'result/report.json').read_text())
    assert report['protocol']['seeds']==expected_seeds
    assert len([r for r in report['predictions'] if r['method']==provider])==2
