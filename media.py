"""Real container conversion with bounded time and output size."""
import subprocess
import time
from pathlib import Path

MIME_TYPES = {'mp4': 'video/mp4', 'mov': 'video/quicktime', 'avi': 'video/x-msvideo'}


def convert_video(source, target_format, max_bytes, timeout=600):
    if target_format not in MIME_TYPES:
        raise ValueError('รองรับเฉพาะ MP4, MOV และ AVI')
    source = Path(source)
    if target_format == 'mp4':
        return source
    import imageio_ffmpeg
    output = source.with_suffix('.' + target_format)
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
               '-nostdin', '-y', '-threads', '1', '-i', str(source),
               '-map', '0:v:0', '-map', '0:a:0', '-map_metadata', '-1']
    if target_format == 'mov':
        command += ['-c', 'copy', '-movflags', '+faststart']
    else:
        command += ['-c:v', 'mpeg4', '-q:v', '3', '-pix_fmt', 'yuv420p',
                    '-c:a', 'libmp3lame', '-b:a', '192k', '-threads', '1']
    command += [str(output)]
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        started = time.monotonic()
        while process.poll() is None:
            if output.exists() and output.stat().st_size > max_bytes:
                raise ValueError('ไฟล์หลังแปลงใหญ่เกินขนาดที่รองรับ กรุณาเลือก MP4')
            if time.monotonic() - started > timeout:
                raise ValueError('แปลงไฟล์เกิน 10 นาที กรุณาเลือก MP4 แล้วลองใหม่')
            time.sleep(0.2)
        if process.returncode != 0 or not output.exists() or not output.stat().st_size:
            raise ValueError('แปลงรูปแบบวิดีโอไม่สำเร็จ กรุณาเลือก MP4 แล้วลองใหม่')
        if output.stat().st_size > max_bytes:
            raise ValueError('ไฟล์หลังแปลงใหญ่เกินขนาดที่รองรับ กรุณาเลือก MP4')
        return output
    except Exception:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        output.unlink(missing_ok=True)
        raise
