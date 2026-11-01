"""Loopback-only TikTok downloader. Run: python app.py."""
import re
import os
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from flask import Flask, jsonify, request, send_file
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / 'downloads'
app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config.update(MAX_CONTENT_LENGTH=4096, TRUSTED_HOSTS=['localhost', '127.0.0.1'])
if os.environ.get('RENDER_EXTERNAL_HOSTNAME'):
    app.config['TRUSTED_HOSTS'].append(os.environ['RENDER_EXTERNAL_HOSTNAME'])
for hostname in os.environ.get('EXTRA_TRUSTED_HOSTS', '').split(','):
    if hostname.strip():
        app.config['TRUSTED_HOSTS'].append(hostname.strip())
jobs = {}
lock = threading.RLock()
pool = ThreadPoolExecutor(max_workers=2)
TOKEN = secrets.token_urlsafe(32)
TTL = 3600
MAX_FILE_BYTES = int(os.environ.get('MAX_FILE_MB', '128')) * 1024 * 1024
MAX_STORAGE_BYTES = int(os.environ.get('MAX_STORAGE_MB', '512')) * 1024 * 1024
janitor_started = False
MAX_ATTEMPTS = 3
RETRY_DELAYS = (2, 4)


def cleanup_expired():
    with lock:
        now = time.time()
        for key, job in list(jobs.items()):
            if job.get('finished') and now - job['finished'] > TTL:
                shutil.rmtree(DOWNLOADS / key, ignore_errors=True)
                del jobs[key]
        if DOWNLOADS.exists():
            for directory in DOWNLOADS.iterdir():
                if (directory.is_dir() and re.fullmatch(r'[a-f0-9]{32}', directory.name)
                        and directory.name not in jobs and now - directory.stat().st_mtime > TTL):
                    shutil.rmtree(directory, ignore_errors=True)


def cleanup_loop():
    while True:
        try:
            cleanup_expired()
        except OSError:
            app.logger.exception('Temporary file cleanup failed')
        time.sleep(60)


def storage_used():
    total = 0
    for path in DOWNLOADS.rglob('*'):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            pass  # A failed download may remove its partial file concurrently.
    return total


def validate_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('กรุณาวางลิงก์ TikTok ที่ถูกต้อง')
    parsed = urlsplit(value.strip())
    if (parsed.scheme != 'https' or parsed.hostname not in
            {'www.tiktok.com', 'tiktok.com', 'm.tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com'}
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('รองรับเฉพาะลิงก์ HTTPS ของ TikTok เท่านั้น')
    if not (re.fullmatch(r'/@[^/]+/video/\d+/?', parsed.path)
            or (parsed.hostname in {'vm.tiktok.com', 'vt.tiktok.com'}
                and re.fullmatch(r'/[A-Za-z0-9]+/?', parsed.path))
            or re.fullmatch(r'/t/[A-Za-z0-9]+/?', parsed.path)):
        raise ValueError('กรุณาใช้ลิงก์วิดีโอหรือลิงก์แชร์ ไม่ใช่ลิงก์โปรไฟล์')
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))


def select_format(info):
    """Only progressive, playable MP4; reject watermarked download variants."""
    formats = [f for f in info.get('formats', []) if
               f.get('ext') == 'mp4' and f.get('url')
               and f.get('vcodec') not in (None, 'none', 'bytevc2')
               and f.get('acodec') not in (None, 'none')
               and 'watermark' not in (f.get('format_note') or '').lower()
               and 'unplayable' not in (f.get('format_note') or '').lower()
               and not (f.get('format_id') or '').startswith('download')]
    if not formats:
        raise ValueError('ไม่พบไฟล์ MP4 พร้อมเสียงจากแหล่ง playback ที่ไม่มีลายน้ำ')
    return max(formats, key=lambda f: (
        (f.get('width') or 0) * (f.get('height') or 0),
        f.get('tbr') or 0, f.get('filesize') or 0, f.get('fps') or 0))


def update(job_id, **values):
    with lock:
        jobs[job_id].update(values)


class QuietLogger:
    def debug(self, message):
        pass
    info = debug
    warning = debug
    error = debug


def download_attempt(job_id, url):
    directory = DOWNLOADS / job_id
    try:
        directory.mkdir(parents=True, exist_ok=True)
        attempt = jobs[job_id].get('attempt', 1)
        update(job_id, state='extracting',
               message=f'กำลังค้นหาไฟล์คุณภาพสูงสุด… (ครั้งที่ {attempt}/{MAX_ATTEMPTS})')
        started = time.monotonic()

        def progress(data):
            size = data.get('downloaded_bytes', 0)
            if size > MAX_FILE_BYTES or time.monotonic() - started > 600:
                raise ValueError(f'ไฟล์ใหญ่เกิน {MAX_FILE_BYTES // 1048576} MB หรือใช้เวลาดาวน์โหลดเกิน 10 นาที')
            total = data.get('total_bytes') or data.get('total_bytes_estimate')
            update(job_id, state='downloading', message='กำลังดาวน์โหลดไฟล์ต้นฉบับ…',
                   percent=min(99, round(size * 100 / total)) if total else None)

        options = {
            'quiet': True, 'logger': QuietLogger(), 'noplaylist': True,
            'socket_timeout': 20, 'retries': 2, 'extractor_retries': 2,
            'max_filesize': MAX_FILE_BYTES,
            'outtmpl': str(directory / 'video.%(ext)s'),
            'progress_hooks': [progress], 'overwrites': True,
            # Prevent a shared link from falling back to a generic website extractor.
            'allowed_extractors': ['TikTok', r'vm\.tiktok'],
        }
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info or info.get('_type', 'video') != 'video':
                raise ValueError('ลิงก์นี้ไม่ใช่วิดีโอเดี่ยวที่รองรับ')
            chosen = select_format(info)
            if (chosen.get('filesize') or 0) > MAX_FILE_BYTES:
                raise ValueError(f'ไฟล์ใหญ่เกิน {MAX_FILE_BYTES // 1048576} MB')
            update(job_id, title=info.get('title') or 'TikTok video',
                   creator=info.get('uploader') or '',
                   resolution=f"{chosen.get('width') or '?'} × {chosen.get('height') or '?'}")
            # Reuse extracted metadata and source request headers, without re-extraction.
            info['formats'] = [chosen]
            ydl.params['format'] = chosen['format_id']
            ydl.process_ie_result(info, download=True)
        output = directory / 'video.mp4'
        if not output.is_file() or not output.stat().st_size:
            raise ValueError('ดาวน์โหลดไม่สำเร็จ หรือไฟล์เกินขนาดที่รองรับ')
        update(job_id, state='ready', percent=100, message='ไฟล์พร้อมแล้ว',
               bytes=output.stat().st_size, finished=time.time())
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def download(job_id, url):
    """Retry the same queued job with fresh metadata, cookies and file URLs."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        update(job_id, attempt=attempt, max_attempts=MAX_ATTEMPTS, percent=None,
               title='', creator='', resolution='', retry_in_seconds=None)
        try:
            download_attempt(job_id, url)
            return
        except Exception as error:
            retryable = isinstance(error, (DownloadError, TimeoutError, ConnectionError))
            # Do not log exception text: upstream errors can include signed media URLs.
            app.logger.warning('Download job %s attempt %d failed (%s)',
                               job_id, attempt, type(error).__name__)
            if retryable and attempt < MAX_ATTEMPTS:
                delay = RETRY_DELAYS[attempt - 1]
                update(job_id, state='retrying', percent=None, retry_in_seconds=delay,
                       message=f'ดาวน์โหลดไม่สำเร็จ กำลังลองใหม่ครั้งที่ {attempt + 1}/{MAX_ATTEMPTS} ใน {delay} วินาที…')
                time.sleep(delay)
                continue
            message = str(error) if isinstance(error, ValueError) else (
                f'ดาวน์โหลดไม่สำเร็จหลังลอง {attempt} ครั้ง กรุณาลองอีกครั้งภายหลัง '
                'หรือใช้ลิงก์วิดีโอสาธารณะอื่น')
            update(job_id, state='error', message=message, percent=None,
                   retry_in_seconds=None, finished=time.time())
            return


@app.before_request
def protect_local_app():
    global janitor_started
    with lock:
        if not janitor_started:
            janitor_started = True
            threading.Thread(target=cleanup_loop, daemon=True, name='file-cleanup').start()
    if request.path.startswith('/api/') and request.headers.get('X-App-Token') != TOKEN:
        return jsonify(error='กรุณาเปิดหน้าแอปใหม่'), 403


@app.after_request
def headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/')
def index():
    html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8').replace('__TOKEN__', TOKEN)
    if os.environ.get('RENDER_EXTERNAL_HOSTNAME'):
        html = html.replace('LOCAL STUDIO', 'WEB STUDIO').replace('พร้อมใช้งานบนเครื่องคุณ', 'พร้อมใช้งานออนไลน์')
    return html


@app.get('/healthz')
def health():
    return jsonify(status='ok')


@app.post('/api/jobs')
def create_job():
    payload = request.get_json(silent=True)
    try:
        url = validate_url(payload.get('url') if isinstance(payload, dict) else None)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    with lock:
        cleanup_expired()
        active = sum(j['state'] not in ('ready', 'error') for j in jobs.values())
        used = storage_used()
        if used + (active + 1) * MAX_FILE_BYTES > MAX_STORAGE_BYTES:
            return jsonify(error='พื้นที่ชั่วคราวเต็ม กรุณารอให้ระบบล้างไฟล์เก่า'), 429
        if active >= 4 or len(jobs) >= 20:
            return jsonify(error='คิวเต็ม กรุณารอสักครู่ (เก็บได้ 20 รายการต่อชั่วโมง)'), 429
        job_id = secrets.token_hex(16)
        jobs[job_id] = dict(state='queued', message='รอคิวดาวน์โหลด…', percent=None)
        pool.submit(download, job_id, url)
    return jsonify(id=job_id), 202


@app.get('/api/jobs/<job_id>')
def job_status(job_id):
    with lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify(error='ไม่พบรายการนี้ กรุณาดาวน์โหลดใหม่'), 404
        return jsonify(**job)


@app.get('/files/<job_id>')
def file_download(job_id):
    with lock:
        job = jobs.get(job_id)
        if not job or job['state'] != 'ready':
            return jsonify(error='ไฟล์ยังไม่พร้อมหรือหมดอายุแล้ว'), 404
        return send_file(DOWNLOADS / job_id / 'video.mp4', as_attachment=True,
                         download_name=f'tiktok-{job_id[:8]}.mp4', mimetype='video/mp4')


if __name__ == '__main__':
    # Download state is in memory; remove abandoned files from earlier sessions.
    shutil.rmtree(DOWNLOADS, ignore_errors=True)
    print('Open http://127.0.0.1:8787 — press Ctrl+C to stop')
    app.run(host='127.0.0.1', port=8787, debug=False, threaded=True)
