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


def test_evaluation_catalog_and_selection_are_allowlisted():
    client=TestClient(create_app())
    result=client.get('/api/evaluations/catalog')
    assert result.status_code==200
    assert any(x['id']=='qwen' for x in result.json()['experiments'])
    assert client.get('/api/evaluations?experiment=../../secret').status_code==422
    assert client.get('/api/evaluations?experiment=qwen').json()['model']['model_id']=='Qwen/Qwen3-1.7B'
    assert client.get('/api/evaluations/agent?version=../../secret').status_code==422
    agent=client.get('/api/evaluations/agent').json()
    assert agent['status'] in ('measured','not_measured')


def test_every_published_model_recomputes_and_keeps_provider_metadata():
    from fitwitness.evaluation.metrics import summarize
    client=TestClient(create_app())
    for experiment in client.get('/api/evaluations/catalog').json()['experiments']:
        report=client.get('/api/evaluations',params={'experiment':experiment['id']}).json()
        for method in report['methods']:
            rows=[r for r in report['predictions'] if r['method']==method['id']]
            assert method['metrics']==summarize(rows)
        if experiment['id']=='claude':
            assert report['protocol']['seeds'] is None
            assert report['model']['provider']=='anthropic'
            assert len([r for r in report['predictions'] if r['method']=='anthropic'])==72


def test_retrieval_report_endpoint_has_explicit_status():
    result=TestClient(create_app()).get('/api/evaluations/retrieval')
    assert result.status_code==200
    assert result.json()['status'] in ('measured','not_measured')
