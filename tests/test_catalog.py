from sqlmodel import Session
from starshelf.models import Repository, RepositoryAnnotation
from starshelf.sync import apply_snapshot
from conftest import entry, ACCOUNT


def test_persistence_and_detail(client):
    assert client.get('/api/health').json() == {"status":"ok"}
    repo = client.get('/api/repositories/1').json()
    assert repo['full_name'] == 'demo/alpha'
    assert len(repo['lists']) == 2
    assert repo['annotation']['review_status'] == 'unreviewed'
    assert client.get('/api/lists').json()[0]['count'] >= 1


def test_filters_sort_pagination(client):
    response = client.get('/api/repositories', params={'language':'TypeScript','topic':'frontend','list_id':'UL_1'})
    assert [r['id'] for r in response.json()['items']] == [2]
    assert client.get('/api/repositories?unclassified=true').json()['items'][0]['id'] == 3
    assert [r['id'] for r in client.get('/api/repositories?sort=stars&direction=desc').json()['items']] == [3,2,1]
    data = client.get('/api/repositories?sort=name&direction=asc&per_page=1&page=2').json()
    assert data['total'] == 3 and data['items'][0]['id'] == 2
    assert client.get('/api/repositories?archived=true').json()['total'] == 0
    assert client.get('/api/repositories?sort=invalid').status_code == 422
    assert client.get('/api/repositories?page=0').status_code == 422


def test_annotations_fts_and_export_import(client):
    assert client.get('/api/repositories?q=secret').json()['total'] == 0
    patch = {'personal_notes':'Secret research for café indexes','review_status':'interesting'}
    assert client.patch('/api/repositories/1/annotation',json=patch).status_code == 200
    for query in ['SECRET','research cafe','sqlite','data','python','demo alpha']:
        data = client.get('/api/repositories',params={'q':query,'review_status':'interesting','sort':'relevance'}).json()
        assert data['total'] == 1, (query,data)
    for query in ['"','NOT OR *','"; DROP TABLE repository; --','(']:
        assert client.get('/api/repositories',params={'q':query}).status_code == 200
    backup = client.get('/api/annotations/export').json()
    client.patch('/api/repositories/1/annotation',json={'personal_notes':'Changed'})
    assert client.post('/api/annotations/import',json=backup).json()['imported'] == 1
    assert client.get('/api/repositories?q=secret').json()['total'] == 1
    assert client.get('/api/repositories?q=Changed').json()['total'] == 0
    backup['annotations'].append({'repository_id':999,'personal_notes':'unknown','review_status':'reviewed'})
    assert client.post('/api/annotations/import',json=backup).json()['skipped_repository_ids'] == [999]
    backup['annotations'][0]['review_status'] = 'bad'
    assert client.post('/api/annotations/import',json=backup).status_code == 422
    assert client.patch('/api/repositories/1/annotation',json={'review_status':'bad'}).status_code == 422


def test_snapshot_idempotency_rename_and_unstar_preserves_notes(client):
    client.patch('/api/repositories/1/annotation',json={'personal_notes':'Keep me'})
    entries = [entry(1,'new-owner/renamed'),entry(2,'demo/beta')]
    for _ in range(2):
        apply_snapshot(client.app.state.engine,entries,[],ACCOUNT)
    assert client.get('/api/repositories').json()['total'] == 2
    assert client.get('/api/repositories?scope=all').json()['total'] == 3
    assert client.get('/api/repositories/1').json()['full_name'] == 'new-owner/renamed'
    assert client.get('/api/repositories/1').json()['annotation']['personal_notes'] == 'Keep me'
    apply_snapshot(client.app.state.engine,[],None,ACCOUNT)
    assert client.get('/api/repositories?scope=unstarred&q=Keep').json()['items'][0]['id'] == 1
    assert client.get('/api/repositories?q=renamed&scope=all').json()['total'] == 1


def test_security_and_validation(client):
    assert client.get('/api/repositories/999').status_code == 404
    assert client.patch('/api/repositories/1/annotation',json={},headers={'X-StarShelf':''}).status_code == 403
    assert client.post('/api/sync',headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.get('/api/health',headers={'Host':'evil.example'}).status_code == 400
    assert client.put('/api/repositories/1/star',json={'starred':False}).status_code == 400
    assert client.post('/api/annotations/import',json={'version':1,'account_id':99,'annotations':[]}).status_code == 409


def test_fts_database_triggers(client):
    with Session(client.app.state.engine) as s:
        repo = s.get(Repository,1)
        repo.description = 'New unique foobarword'
        s.add(repo)
        s.add(RepositoryAnnotation(repository_id=1,personal_notes='temporaryword'))
        s.commit()
    assert client.get('/api/repositories?q=foobarword').json()['total'] == 1
    with Session(client.app.state.engine) as s:
        s.delete(s.get(RepositoryAnnotation,1));s.commit()
    assert client.get('/api/repositories?q=temporaryword').json()['total'] == 0
