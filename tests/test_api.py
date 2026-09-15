import os
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

for key in ('POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DB'):
    os.environ[key] = 'test'

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def client():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    def override():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


def create(client):
    response = client.post('/monitors', json={'name': 'Example', 'url': 'https://example.com'})
    assert response.status_code == 201
    return response.json()['id']


@pytest.mark.parametrize('payload', [
    {'name': '', 'url': 'https://example.com'},
    {'name': '   ', 'url': 'https://example.com'},
    {'name': 'x' * 101, 'url': 'https://example.com'},
    {'name': 'x', 'url': 'file:///etc/passwd'},
    {'name': 'x', 'url': 'invalid'},
])
def test_invalid_create(client, payload):
    assert client.post('/monitors', json=payload).status_code == 422


@pytest.mark.parametrize('field', ['name', 'url', 'is_active'])
def test_null_patch(client, field):
    monitor_id = create(client)
    assert client.patch(f'/monitors/{monitor_id}', json={field: None}).status_code == 422


def test_crud(client):
    monitor_id = create(client)
    path = f'/monitors/{monitor_id}'
    assert client.get('/health').json() == {'status': 'ok'}
    assert client.get('/db-health').status_code == 200
    assert client.patch(path, json={}).status_code == 200
    assert client.patch(path, json={'is_active': False}).json()['is_active'] is False
    assert client.patch(path, json={'url': 'https://example.org'}).json()['url'] == 'https://example.org/'
    assert len(client.get('/monitors').json()) == 1
    assert client.get(path + '/checks').json() == []
    assert client.delete(path).status_code == 204
    for method, suffix in [('get', ''), ('get', '/checks'), ('post', '/check'), ('delete', ''), ('patch', '')]:
        kwargs = {'json': {}} if method == 'patch' else {}
        assert getattr(client, method)(path + suffix, **kwargs).status_code == 404


@pytest.mark.parametrize('outcome,expected_up,expected_error', [
    (200, True, None), (302, True, None), (503, False, None),
    (httpx.ReadTimeout(''), False, 'timeout'),
    (httpx.ConnectError('sensitive target details'), False, 'ConnectError'),
])
def test_check_history(client, monkeypatch, outcome, expected_up, expected_error):
    @contextmanager
    def stream(method, url, **kwargs):
        assert kwargs == {'timeout': 5.0, 'follow_redirects': False}
        if isinstance(outcome, Exception):
            raise outcome
        yield httpx.Response(outcome)
    monkeypatch.setattr('app.checker.httpx.stream', stream)
    monitor_id = create(client)
    path = f'/monitors/{monitor_id}'
    first = client.post(path + '/check')
    assert first.status_code == 200
    result = first.json()
    assert result['is_up'] is expected_up
    assert result['error'] == expected_error
    assert result['response_time_ms'] >= 0
    assert result['status_code'] == (outcome if isinstance(outcome, int) else None)
    second = client.post(path + '/check').json()
    assert client.get(path + '/checks?limit=1').json()[0]['id'] == second['id']
    assert client.get(path + '/checks?limit=1&offset=1').json()[0]['id'] == result['id']
    other_id = create(client)
    assert client.get(f'/monitors/{other_id}/checks').json() == []
    assert client.delete(path).status_code == 409
    assert len(client.get(path + '/checks').json()) == 2
    assert client.patch(path, json={'is_active': False}).status_code == 200


@pytest.mark.parametrize('query', ['limit=0', 'limit=101', 'offset=-1'])
def test_pagination_validation(client, query):
    monitor_id = create(client)
    assert client.get(f'/monitors/{monitor_id}/checks?{query}').status_code == 422
    assert client.get('/monitors?' + query).status_code == 422


def test_db_unavailable(client):
    from sqlalchemy.exc import OperationalError
    class BrokenSession:
        def execute(self, _):
            raise OperationalError('SELECT 1', {}, Exception('secret'))
        def rollback(self):
            pass
    app.dependency_overrides[get_db] = lambda: BrokenSession()
    assert client.get('/db-health').status_code == 503
    assert client.get('/health').status_code == 200
