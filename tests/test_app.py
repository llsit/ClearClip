import pytest
import app as module


@pytest.mark.parametrize('url', [
    'https://evil.com/a', 'https://tiktok.com.evil.com/@x/video/123',
    'http://www.tiktok.com/@x/video/123', 'https://user@www.tiktok.com/@x/video/123',
    'https://www.tiktok.com:123/@x/video/123', 'https://www.tiktok.com/@x',
    'https://www.tiktok.com/@x/photo/123', None, 'https://127.0.0.1/a',
])
def test_reject_url(url):
    with pytest.raises(ValueError):
        module.validate_url(url)


@pytest.mark.parametrize('url', [
    'https://www.tiktok.com/@creator/video/123456',
    'https://vt.tiktok.com/ABC123/', 'https://vm.tiktok.com/ABC123/',
    'https://www.tiktok.com/t/ABC123/',
])
def test_valid_url(url):
    assert module.validate_url(url + '?tracking=1') == url


def fmt(id, width=1080, height=1920, **kwargs):
    return dict(format_id=id, width=width, height=height, ext='mp4',
                url='https://example.com/video.mp4', vcodec='h264', acodec='aac', **kwargs)


def test_format_selection():
    high = fmt('play-hd')
    assert module.select_format({'formats': [
        fmt('play-low', 540, 960), high,
        fmt('download', 2160, 3840),
        fmt('marked', 2160, 3840, format_note='watermarked'),
    ]}) == high


def test_no_watermarked_fallback():
    with pytest.raises(ValueError):
        module.select_format({'formats': [fmt('download')]})


def test_split_stream_plan_prefers_hd_and_requires_audio():
    hd = fmt('hd', 1080, 1920)
    hd['acodec'] = 'none'
    low = fmt('low', 576, 1024)
    audio = dict(format_id='audio', url='https://example.com/audio', vcodec='none', acodec='aac', ext='m4a')
    assert module.select_plan({'formats':[low, hd, audio]}) == [hd, audio]
    assert module.select_plan({'formats':[low, hd]}) == [low]
    marked = dict(hd, format_note='watermarked')
    assert module.select_plan({'formats':[low, marked, audio]}) == [low]
    with pytest.raises(ValueError):
        module.select_plan({'formats':[hd]})


def test_video_only_rejected():
    video = fmt('video-only')
    video['acodec'] = 'none'
    with pytest.raises(ValueError):
        module.select_format({'formats': [video]})


@pytest.fixture
def client(monkeypatch, tmp_path):
    module.jobs.clear()
    monkeypatch.setattr(module, 'janitor_started', True)
    monkeypatch.setattr(module, 'DOWNLOADS', tmp_path)
    return module.app.test_client()


def test_api_validation(client):
    assert client.get('/').status_code == 200
    assert client.post('/api/jobs', json={'url':'x'}).status_code == 403
    auth = {'X-App-Token':module.TOKEN}
    assert client.post('/api/jobs', json={'url':'x'}, headers=auth).status_code == 400
    assert client.post('/api/jobs', json=[], headers=auth).status_code == 400
    assert client.get('/api/jobs/unknown', headers=auth).status_code == 404
    assert client.get('/files/unknown').status_code == 404
    assert client.get('/', headers={'Host':'evil.com'}).status_code == 400


def test_download_lifecycle(client, monkeypatch, tmp_path):
    monkeypatch.setattr(module, 'DOWNLOADS', tmp_path)
    class FakeYDL:
        def __init__(self, options):
            self.params = options
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, url, download=False):
            video_only = fmt('1080-video-only')
            video_only['acodec'] = 'none'
            assert list(self.params['format']({'formats': [video_only, fmt('play')]}))[0]['format_id'] == 'play'
            return {'id':'123', 'title':'Sample', 'formats':[fmt('play')],
                    'requested_formats':[video_only], '__files_to_move':{'missing.mp4':'video.mp4'},
                    '__postprocessors':['stale-merger'], 'requested_downloads':[video_only]}
        def process_ie_result(self, info, download=True):
            assert info['formats'][0]['format_id'] == 'play'
            assert not any(key in info for key in ('requested_formats', 'requested_downloads', '__files_to_move', '__postprocessors'))
            from pathlib import Path
            Path(self.params['outtmpl'].replace('%(ext)s', 'mp4')).write_bytes(b'fake-mp4')
    monkeypatch.setattr(module, 'YoutubeDL', FakeYDL)
    monkeypatch.setattr(module.pool, 'submit', lambda func, *args: func(*args))
    auth = {'X-App-Token':module.TOKEN}
    response = client.post('/api/jobs', json={'url':'https://www.tiktok.com/@x/video/123'}, headers=auth)
    assert response.status_code == 202
    id = response.json['id']
    assert client.get('/api/jobs/' + id, headers=auth).json['state'] == 'ready'
    file = client.get('/files/' + id)
    assert file.data == b'fake-mp4'
    assert 'attachment' in file.headers['Content-Disposition']
    file.close()


def test_queue_cap(client):
    module.jobs.update({str(i): {'state':'queued'} for i in range(4)})
    assert client.post('/api/jobs', json={'url':'https://vt.tiktok.com/ABC/'},
                       headers={'X-App-Token':module.TOKEN}).status_code == 429


def test_short_link_extractor_enabled():
    with module.YoutubeDL({'quiet': True, 'allowed_extractors': ['TikTok', r'vm\.tiktok']}) as ydl:
        assert 'TikTok' in ydl._ies
        assert 'TikTokVM' in ydl._ies


def test_health_and_hosted_label(client, monkeypatch):
    assert client.get('/healthz').json == {'status': 'ok', 'version': module.APP_VERSION}
    monkeypatch.setenv('RENDER_EXTERNAL_HOSTNAME', 'demo.onrender.com')
    assert b'WEB STUDIO' in client.get('/').data


def test_storage_reservation(client, monkeypatch):
    monkeypatch.setattr(module, 'MAX_STORAGE_BYTES', 1)
    response = client.post('/api/jobs', json={'url':'https://vt.tiktok.com/ABC/'},
                           headers={'X-App-Token':module.TOKEN})
    assert response.status_code == 429


def test_cleanup_preserves_active_jobs(client):
    import time
    expired, active = 'a' * 32, 'b' * 32
    for key in [expired, active]:
        folder = module.DOWNLOADS / key
        folder.mkdir()
        (folder / 'video.mp4').write_bytes(b'video')
    module.jobs[expired] = {'state':'ready', 'finished':time.time() - module.TTL - 1}
    module.jobs[active] = {'state':'downloading'}
    module.cleanup_expired()
    assert expired not in module.jobs
    assert not (module.DOWNLOADS / expired).exists()
    assert (module.DOWNLOADS / active / 'video.mp4').is_file()


@pytest.mark.parametrize('failures,phase', [(0, 'transfer'), (1, 'extract'), (2, 'transfer'), (3, 'transfer')])
def test_retry_download(client, monkeypatch, failures, phase):
    from pathlib import Path
    sessions, sleeps, retry_states = [], [], []
    class FlakyYDL:
        def __init__(self, options):
            self.params = options
            sessions.append(self)
            self.attempt = len(sessions)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, url, download=False):
            folder = Path(self.params['outtmpl']).parent
            assert not (folder / 'video.mp4.part').exists()
            if phase == 'extract' and self.attempt <= failures:
                raise module.DownloadError('temporary extraction failure')
            video = fmt('play')
            video['url'] = f'https://example.com/fresh-{self.attempt}.mp4'
            return {'id':'123', 'formats':[video]}
        def process_ie_result(self, info, download=True):
            output = Path(self.params['outtmpl'].replace('%(ext)s', 'mp4'))
            assert info['formats'][0]['url'].endswith(f'fresh-{self.attempt}.mp4')
            if self.attempt <= failures:
                output.with_suffix('.mp4.part').write_bytes(b'incomplete')
                raise module.DownloadError('HTTP Error 403: signed URL expired')
            output.write_bytes(b'complete-video')
    def sleep(delay):
        sleeps.append(delay)
        retry_states.append(dict(module.jobs['retry-test']))
        assert module.jobs['retry-test']['state'] == 'retrying'
        assert 'finished' not in module.jobs['retry-test']
        assert not (module.DOWNLOADS / 'retry-test').exists()
    monkeypatch.setattr(module, 'YoutubeDL', FlakyYDL)
    monkeypatch.setattr(module.time, 'sleep', sleep)
    module.jobs['retry-test'] = {'state':'queued'}
    module.download('retry-test', 'https://vt.tiktok.com/ABC/')
    job = module.jobs['retry-test']
    assert len(sessions) == min(failures + 1, 3)
    assert sleeps == [2, 4][:min(failures, 2)]
    assert job['state'] == ('error' if failures == 3 else 'ready')
    if failures == 3:
        assert not (module.DOWNLOADS / 'retry-test').exists()
        assert '3' in job['message']
        assert 'signed URL' not in job['message']
    else:
        assert (module.DOWNLOADS / 'retry-test' / 'video.mp4').read_bytes() == b'complete-video'
    assert job['retry_in_seconds'] is None


def test_permanent_error_not_retried(client, monkeypatch):
    attempts = []
    def fail(job_id, url):
        attempts.append(job_id)
        raise ValueError('ไฟล์ใหญ่เกินขนาด')
    monkeypatch.setattr(module, 'download_attempt', fail)
    module.jobs['invalid'] = {'state':'queued'}
    module.download('invalid', 'https://vt.tiktok.com/ABC/')
    assert attempts == ['invalid']
    assert module.jobs['invalid']['state'] == 'error'
    assert module.jobs['invalid']['message'] == 'ไฟล์ใหญ่เกินขนาด'
