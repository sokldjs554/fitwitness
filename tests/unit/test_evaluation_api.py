from fastapi.testclient import TestClient
from fitwitness.api.app import create_app


def test_published_evaluation_has_reproducible_manifest():
    # This endpoint is public static evidence and does not initialize a session or DB.
    client=TestClient(create_app())
    result=client.get('/api/evaluations')
    assert result.status_code==200
    body=result.json()
    assert body.get('schema_version')==1
    assert body.get('protocol',{}).get('dataset_hash')
    assert body.get('methods')


def test_report_metrics_recompute_from_every_raw_prediction():
    from fitwitness.evaluation.metrics import summarize
    body=TestClient(create_app()).get('/api/evaluations').json()
    for method in body['methods']:
        rows=[r for r in body['predictions'] if r['method']==method['id']]
        assert len(rows)==body['protocol']['case_count']*body['protocol']['repeats']
        assert method['metrics']==summarize(rows)
    assert {x['provider'] for x in body['blocked']}=={'openai','anthropic'}
