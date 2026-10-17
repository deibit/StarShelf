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


def test_distinct_commit_activity_dates_and_sorting(client):
    engine = client.app.state.engine
    snapshot = [entry(), entry(2), entry(3)]
    for item in snapshot:
        item['repo']['pushed_at'] = '2024-08-19T14:03:04Z'
    apply_snapshot(engine, snapshot, None, ACCOUNT, {
        1: ('2016-11-17T21:30:21Z', 'master'),
        2: ('2025-01-01T00:00:00Z', 'main'),
        3: (None, None),
    })
    for direction, expected in [('asc', [1, 2, 3]), ('desc', [2, 1, 3])]:
        data = client.get(f'/api/repositories?sort=last_commit_at&direction={direction}').json()
        assert [repo['id'] for repo in data['items']] == expected
    repo = client.get('/api/repositories/1').json()
    assert repo['last_commit_at'] == '2016-11-17T21:30:21Z'
    assert repo['pushed_at'] == '2024-08-19T14:03:04Z'
    assert repo['default_branch'] == 'master'


def test_existing_catalog_upgrade_preserves_data(client):
    from starshelf.db import initialize
    engine = client.app.state.engine
    client.patch('/api/repositories/1/annotation', json={'personal_notes': 'Migration keeps notes'})
    with engine.begin() as connection:
        connection.exec_driver_sql('DROP INDEX ix_repository_last_commit_at')
        connection.exec_driver_sql('ALTER TABLE repository DROP COLUMN last_commit_at')
        connection.exec_driver_sql('ALTER TABLE repository DROP COLUMN default_branch')
    initialize(engine)
    initialize(engine)  # The upgrade must also be safe on subsequent startups.
    assert client.get('/api/repositories/1').json()['last_commit_at'] is None
    assert client.get('/api/repositories/1').json()['annotation']['personal_notes'] == 'Migration keeps notes'
    assert client.get('/api/repositories?q=Migration').json()['total'] == 1
    with engine.connect() as connection:
        indexes = {row[1] for row in connection.exec_driver_sql('PRAGMA index_list(repository)')}
        assert 'ix_repository_last_commit_at' in indexes


def test_owner_filter_and_other_repository_count(client):
    apply_snapshot(client.app.state.engine, [entry(), entry(2, 'Demo/beta'),
        entry(3, 'demo-tools/third'), entry(4, 'demo/old'), entry(5, 'different/fifth')], [], ACCOUNT)
    with Session(client.app.state.engine) as session:
        repo = session.get(Repository, 4)
        repo.is_starred = False
        session.add(repo)
        session.commit()
    assert client.get('/api/repositories/1').json()['other_owner_repositories_count'] == 2
    assert client.get('/api/repositories/4').json()['other_owner_repositories_count'] == 2
    assert client.get('/api/repositories/5').json()['other_owner_repositories_count'] == 0
    data = client.get('/api/repositories?owner=DEMO&scope=all&sort=name&direction=asc&per_page=2').json()
    assert data['total'] == 3 and [repo['id'] for repo in data['items']] == [1, 2]
    assert client.get('/api/repositories?owner=demo&scope=all&sort=name&direction=asc&page=2&per_page=2').json()['items'][0]['id'] == 4
    assert client.get('/api/repositories?owner=demo').json()['total'] == 2
    assert client.get('/api/repositories?owner=demo&q=SQLite&language=Python').json()['total'] == 2
    assert client.get('/api/repositories?owner=demo-tools').json()['total'] == 1
    assert client.get('/api/repositories', params={'owner': "demo' OR 1=1 --"}).json()['total'] == 0
