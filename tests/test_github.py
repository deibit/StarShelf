import asyncio
import base64
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from starshelf.app import create_app
from starshelf.db import initialize, make_engine
from starshelf.github import GitHub, GitHubError
from starshelf.models import SyncState
from starshelf.sync import apply_snapshot, synchronize, update_state
from conftest import entry, ACCOUNT


def run(coro):
    return asyncio.run(coro)


def test_rest_pagination_and_star_dates():
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.headers['accept'] == 'application/vnd.github.star+json'
        if request.url.params['page']=='1':
            return httpx.Response(200,json=[entry()],headers={'Link':'<https://api.github.com/user/starred?page=2>; rel="next"'})
        return httpx.Response(200,json=[entry(2)])
    async def go():
        async with GitHub('not-a-real-token',httpx.MockTransport(handler)) as g:
            result=await g.starred(lambda *_:None)
            assert len(result)==2 and result[0]['starred_at']=='2026-01-02T03:00:00Z'
    run(go())
    assert len(requests)==2


def test_graphql_lists_paginate_both_levels():
    import json
    def handler(request):
        payload=json.loads(request.content)
        v=payload['variables']
        if 'viewer' in payload['query']:
            return httpx.Response(200,json={'data':{'viewer':{'lists':{'nodes':[{'id':'L2','name':'B'}] if v['cursor'] else [{'id':'L1','name':'A'}],
                'pageInfo':{'hasNextPage':not bool(v['cursor']),'endCursor':'next'}}}}})
        return httpx.Response(200,json={'data':{'node':{'items':{'nodes':[{'databaseId':2 if v['cursor'] else 1}],
            'pageInfo':{'hasNextPage':not bool(v['cursor']),'endCursor':'next'}}}}})
    async def go():
        async with GitHub('fake',httpx.MockTransport(handler)) as g:
            lists=await g.lists(lambda *_:None)
            assert [i['repository_ids'] for i in lists] == [[1,2],[1,2]]
    run(go())


@pytest.mark.parametrize('status,headers,expected',[(401,{},401),(403,{},403),(429,{'retry-after':'60'},429),(403,{'x-ratelimit-remaining':'0','x-ratelimit-reset':'123'},429),(500,{},502)])
def test_api_errors(status,headers,expected):
    async def go():
        async with GitHub('secret',httpx.MockTransport(lambda _:httpx.Response(status,headers=headers))) as g:
            with pytest.raises(GitHubError) as exc:await g.identity()
            assert exc.value.status==expected
            assert 'secret' not in str(exc.value)
    run(go())


def test_readme_and_network_error():
    async def go():
        def handler(request):return httpx.Response(200,json={'encoding':'base64','content':base64.b64encode(b'# Hello').decode(),'size':7})
        async with GitHub('fake',httpx.MockTransport(handler)) as g:
            assert await g.readme('a','b')=='# Hello'
        def offline(request):raise httpx.ConnectError('secret-containing-error',request=request)
        async with GitHub('fake',httpx.MockTransport(offline)) as g:
            with pytest.raises(GitHubError,match='Cannot reach GitHub'):await g.identity()
    run(go())


def test_failed_sync_is_atomic_and_lists_fallback(tmp_path):
    engine=make_engine(tmp_path/'sync.db');initialize(engine)
    apply_snapshot(engine,[entry()],[{'id':'L','name':'Old','repository_ids':[1]}],ACCOUNT)
    class Fake:
        async def identity(self):return ACCOUNT
        async def starred(self,_):raise GitHubError('Page two failed')
    run(synchronize(engine,Fake()))
    with Session(engine) as s:
        state=s.get(SyncState,1);assert state.last_sync_status=='error';assert state.number_of_repositories==1
    class ListsUnavailable(Fake):
        async def starred(self,_):return [entry(),entry(2)]
        async def lists(self,_):raise GitHubError('Unsupported schema')
    run(synchronize(engine,ListsUnavailable()))
    with Session(engine) as s:
        state=s.get(SyncState,1);assert state.last_sync_status=='success';assert 'lists unavailable' in state.warning
    from starshelf.models import StarList
    with Session(engine) as s:assert s.get(StarList,'L').name=='Old'
    class WrongAccount(ListsUnavailable):
        async def identity(self):return {'id':99,'login':'other'}
    run(synchronize(engine,WrongAccount()))
    with Session(engine) as s:assert s.get(SyncState,1).last_sync_status=='error'


@pytest.mark.parametrize('scopes,allowed',[('repo',True),('public_repo',True),('read:user',False),(None,False)])
def test_writes_check_permissions(scopes,allowed):
    methods=[]
    def handler(request):
        methods.append(request.method)
        if request.method=='GET':return httpx.Response(200,json={'private':False},headers={'x-oauth-scopes':scopes} if scopes is not None else {})
        return httpx.Response(204)
    async def go():
        async with GitHub('fake',httpx.MockTransport(handler)) as g:
            if allowed:await g.set_star('demo','alpha',False)
            else:
                with pytest.raises(GitHubError):await g.set_star('demo','alpha',False)
    run(go());assert methods==(['GET','DELETE'] if allowed else ['GET'])


def test_star_api_changes_only_after_success(tmp_path):
    fail={'value':True}
    def handler(request):
        if request.url.path=='/user':return httpx.Response(200,json=ACCOUNT)
        if request.url.path=='/repos/demo/alpha':return httpx.Response(200,json={'private':False},headers={'x-oauth-scopes':'repo'})
        return httpx.Response(503 if fail['value'] else 204)
    app=create_app(tmp_path/'write.db',github_factory=lambda:GitHub('fake',httpx.MockTransport(handler)))
    with TestClient(app,headers={'X-StarShelf':'1'}) as c:
        apply_snapshot(app.state.engine,[entry()],[],ACCOUNT)
        c.patch('/api/repositories/1/annotation',json={'personal_notes':'Keep'})
        assert c.put('/api/repositories/1/star',json={'starred':False,'confirmed':True}).status_code==502
        assert c.get('/api/repositories/1').json()['is_starred'] is True
        fail['value']=False
        assert c.put('/api/repositories/1/star',json={'starred':False,'confirmed':True}).status_code==200
        repo=c.get('/api/repositories/1').json();assert repo['is_starred'] is False;assert repo['annotation']['personal_notes']=='Keep'
        assert c.put('/api/repositories/1/star',json={'starred':True}).status_code==200


def test_interrupted_sync_recovery(tmp_path):
    engine=make_engine(tmp_path/'recover.db');initialize(engine)
    update_state(engine,last_sync_status='running')
    initialize(engine)
    with Session(engine) as s:
        assert s.get(SyncState,1).last_sync_status=='error'


def test_manual_sync_api_reports_authentication_failure(tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module('starshelf.app')
    async def missing_token():
        raise GitHubError('GitHub CLI is missing. Install gh, then run gh auth login.', 401)
    monkeypatch.setattr(module, 'cli_token', missing_token)
    app = create_app(tmp_path / 'auth.db')
    with TestClient(app, headers={'X-StarShelf': '1'}) as client:
        assert client.post('/api/sync').status_code == 202
        # A second request yields the event loop to the in-process sync task.
        state = client.get('/api/sync/status').json()
        assert state['last_sync_status'] == 'error'
        assert 'GitHub CLI is missing' in state['last_sync_error']
        assert client.get('/api/repositories').json()['total'] == 0


def test_malformed_snapshot_rolls_back_all_changes(tmp_path):
    from starshelf.models import Repository
    engine = make_engine(tmp_path / 'atomic.db'); initialize(engine)
    apply_snapshot(engine, [entry()], [], ACCOUNT)
    broken = entry(2); broken.pop('starred_at')
    with pytest.raises(KeyError):
        apply_snapshot(engine, [entry(1, 'changed/name'), broken], [], ACCOUNT)
    with Session(engine) as session:
        repo = session.get(Repository, 1)
        assert repo.full_name == 'demo/alpha' and repo.is_starred
        assert session.get(Repository, 2) is None
