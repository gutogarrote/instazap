import json
import math
from fractions import Fraction
from pathlib import Path

from .process import run

INPUT_FORMATS = 'mov,matroska,webm,avi,mpeg,mpegts,ogg,flv'


class MediaError(ValueError):
    pass


def probe(path: Path):
    data = json.loads(run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file',
                          '-format_whitelist', INPUT_FORMATS, '-show_streams', '-show_format', '-of', 'json', path], timeout=30))
    streams = data.get('streams', [])
    videos = [s for s in streams if s['codec_type'] == 'video' and not s.get('disposition', {}).get('attached_pic')]
    if len(videos) != 1:
        raise MediaError('Envie um arquivo com uma única faixa de vídeo.')
    video = videos[0]
    durations = [float(data['format'].get('duration', 0))]
    durations += [float(s['duration']) for s in streams if s.get('duration') not in (None, 'N/A')]
    duration = max(durations)
    if not math.isfinite(duration) or duration <= 0:
        raise MediaError('Não foi possível medir a duração do vídeo.')
    if duration > 240:
        raise MediaError('O vídeo ultrapassa 4 minutos (240 segundos). Nada foi cortado; envie um vídeo menor.')
    w, h = video['width'], video['height']
    if w < 2 or h < 2 or max(w, h) > 8192 or w * h > 20_000_000:
        raise MediaError('Resolução de entrada não suportada (máximo 20 megapixels e 8192 pixels por lado).')
    sar = Fraction(video.get('sample_aspect_ratio', '1:1').replace(':', '/')) if video.get('sample_aspect_ratio') not in (None, 'N/A', '0:1') else 1
    w *= float(sar)
    rotation = next((s.get('rotation', 0) for s in video.get('side_data_list', []) if 'rotation' in s), 0)
    if abs(rotation) % 180 == 90:
        w, h = h, w
    scale = min(1, 1280 / max(w, h), 720 / min(w, h))
    width, height = max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)
    return duration, width, height, any(s['codec_type'] == 'audio' for s in streams)


def split(source: Path, directory: Path, progress=lambda *_: None, cancel=None):
    duration, width, height, audio = probe(source)
    count = math.ceil(duration / 60)
    parts = []
    for index in range(count):
        progress(index, count)
        name = f'video-parte-{index + 1:02}-de-{count:02}.mp4'
        target = directory / name
        span = min(60, duration - index * 60)
        args = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '1',
                '-filter_threads', '1', '-protocol_whitelist', 'file', '-format_whitelist', INPUT_FORMATS, '-ss', str(index * 60),
                '-i', str(source), '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn',
                '-t', f'{span:.9f}', '-vf', f'scale={width}:{height},setsar=1,fps=30',
                '-c:v', 'libx264', '-threads', '1', '-preset', 'veryfast', '-crf', '23',
                '-maxrate', '2500k', '-bufsize', '5000k', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '128k', '-ar', '48000',
                '-map_metadata', '-1', '-metadata:s:v', 'rotate=0',
                '-movflags', '+faststart', str(target)]
        run(args, cancel=cancel)
        result = json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
                                 '-of', 'json', target], timeout=30))
        actual = float(result['format']['duration'])
        if actual > 60 or actual < span - 0.05:
            raise MediaError('Não foi possível preservar o final dentro do limite de 60 s. Tente outro arquivo.')
        parts.append({'name': name, 'duration': actual, 'bytes': target.stat().st_size, 'audio': audio})
    return parts
