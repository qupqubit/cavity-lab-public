"""A ZIP upgrade must not reuse stale HTML, asset validators, or API results."""
import os
import re

from fastapi.testclient import TestClient
import pytest

from cavity_fem import api


@pytest.fixture
def ui_client(tmp_path, monkeypatch):
    directory = tmp_path/'static'
    directory.mkdir()
    (directory/'index.html').write_text(
        '<html data-app-version="__APP_VERSION__" data-asset-version="__ASSET_VERSION__">'
        '<link rel="stylesheet" href="/static/style.css?v=__ASSET_VERSION__">'
        '<script src="/static/app.js?v=__ASSET_VERSION__"></script></html>', encoding='utf-8')
    (directory/'style.css').write_text('body {color: red}', encoding='utf-8')
    (directory/'app.js').write_text('window.revision=1;', encoding='utf-8')
    monkeypatch.setattr(api, 'ROOT', tmp_path)
    monkeypatch.setattr(api, 'DATA', tmp_path/'data')
    mount = next(route for route in api.app.routes if route.path == '/static')
    monkeypatch.setattr(mount, 'app', api.UncachedStaticFiles(directory=directory))
    with TestClient(api.app, base_url='http://127.0.0.1:8768') as client:
        yield client, directory


def asset_urls(response):
    return re.findall(r'(?:src|href)="(/static/[^\"]+)"', response.text)


def assert_no_store(response):
    assert 'no-store' in response.headers['cache-control']
    assert response.headers['pragma'] == 'no-cache'
    assert response.headers['expires'] == '0'


def test_html_assets_api_and_errors_are_not_stored(ui_client):
    client, _ = ui_client
    for url in ('/', '/static/app.js', '/static/style.css', '/api/health', '/api/info', '/not-found'):
        response = client.get(url)
        assert_no_store(response)
    rejected = client.post('/api/jobs', json={}, headers={'origin': 'https://unrelated.example'})
    assert rejected.status_code == 403
    assert_no_store(rejected)
    rejected_host = client.get('/api/info', headers={'host': 'unrelated.example'})
    assert rejected_host.status_code == 400
    assert_no_store(rejected_host)


def test_index_uses_one_content_token_for_all_assets(ui_client):
    client, _ = ui_client
    response = client.get('/')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/html')
    assert '__ASSET_VERSION__' not in response.text and '__APP_VERSION__' not in response.text
    token = response.headers['x-asset-version']
    assert response.headers['x-app-version'] == api.__version__
    assert token.startswith(api.__version__+'-')
    assert len(asset_urls(response)) == 2
    assert all(url.endswith('?v='+token) for url in asset_urls(response))
    assert client.get('/').headers['x-asset-version'] == token


@pytest.mark.parametrize('asset', ['app.js', 'style.css'])
def test_same_size_same_timestamp_replacement_changes_asset_token_and_sends_body(ui_client, asset):
    client, directory = ui_client
    first_index = client.get('/')
    old_url = next(url for url in asset_urls(first_index) if asset in url)
    old_asset = client.get(old_url)
    path = directory/asset
    before = path.stat()
    old_content = path.read_bytes()
    new_content = old_content.replace(b'=1', b'=2') if asset.endswith('.js') else old_content.replace(b'red', b'tan')
    assert len(new_content) == len(old_content) and new_content != old_content
    path.write_bytes(new_content)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    current = client.get(old_url, headers={'if-none-match': old_asset.headers['etag']})
    assert current.status_code == 200 and current.content == new_content
    assert_no_store(current)
    current = client.get(old_url, headers={'if-modified-since': old_asset.headers['last-modified']})
    assert current.status_code == 200 and current.content == new_content
    second_index = client.get('/')
    assert second_index.headers['x-asset-version'] != first_index.headers['x-asset-version']
    assert set(asset_urls(second_index)).isdisjoint(asset_urls(first_index))


def test_application_version_changes_asset_urls_and_diagnostics(ui_client, monkeypatch):
    client, _ = ui_client
    before = client.get('/')
    monkeypatch.setattr(api, '__version__', '9.9.99')
    after = client.get('/')
    assert after.headers['x-app-version'] == '9.9.99'
    assert 'data-app-version="9.9.99"' in after.text
    assert after.headers['x-asset-version'].startswith('9.9.99-')
    assert after.headers['x-asset-version'] != before.headers['x-asset-version']
    assert client.get('/api/info').json()['app_version'] == '9.9.99'


def test_old_conditional_headers_never_return_304_for_html_or_static(ui_client):
    client, _ = ui_client
    headers = {'if-none-match': '*', 'if-modified-since': 'Tue, 19 Jan 2038 03:14:07 GMT'}
    for url in ('/', '/static/app.js', '/static/style.css'):
        response = client.get(url, headers=headers)
        assert response.status_code == 200 and response.content
        assert_no_store(response)
    response = client.head('/static/app.js', headers=headers)
    assert response.status_code == 200 and not response.content
    assert_no_store(response)


def test_distributed_index_contains_asset_and_app_version_placeholders():
    template = (api.ROOT/'static/index.html').read_text(encoding='utf-8')
    assert 'style.css?v=__ASSET_VERSION__' in template
    assert 'app.js?v=__ASSET_VERSION__' in template
    assert '__APP_VERSION__' in template
