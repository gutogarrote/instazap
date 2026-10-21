import json
import logging
import math
from fractions import Fraction
from pathlib import Path

from .process import run

logger = logging.getLogger(__name__)

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


def presentation_timeline(path: Path, cancel=None):
    """Measure decoded presentation, excluding AAC priming and overstated packet tails.

    Keep timestamps relative to the demuxer's origin. Do not
    reset each stream independently: that would erase legitimate A/V offsets.
    """
    data = json.loads(run(['ffprobe', '-v', 'error', '-threads', '1',
                          '-protocol_whitelist', 'file', '-format_whitelist', INPUT_FORMATS,
                          '-show_frames', '-show_streams', '-show_format', '-show_entries',
                          'format=start_time:stream=index,codec_type,sample_rate:'
                          'frame=stream_index,best_effort_timestamp_time,duration_time,nb_samples',
                          '-of', 'json', path], cancel=cancel))
    streams = {s['index']: s for s in data['streams']}
    origin = float(data['format'].get('start_time', 0))
    ends = {}
    for frame in data['frames']:
        stream = streams[frame['stream_index']]
        if stream['codec_type'] not in ('video', 'audio'):
            continue
        timestamp = frame.get('best_effort_timestamp_time')
        if timestamp is None:
            raise MediaError('Não foi possível medir os quadros deste vídeo.')
        length = (int(frame['nb_samples']) / int(stream['sample_rate'])
                  if stream['codec_type'] == 'audio' else float(frame.get('duration_time', 0)))
        # A shorter presentation duration signals encoder end padding. A longer
        # one cannot manufacture samples absent from the decoded frame.
        if stream['codec_type'] == 'audio' and frame.get('duration_time'):
            length = min(length, float(frame['duration_time']))
        kind = stream['codec_type']
        ends[kind] = max(ends.get(kind, 0), float(timestamp) - origin + length)
    if not ends or not 0 < max(ends.values()) <= 240:
        raise MediaError('Não foi possível medir o conteúdo dentro do limite de 240 segundos.')
    return ends


def presentation_duration(path: Path):
    return max(presentation_timeline(path).values())


def validate_first_part(value):
    if type(value) is not int or value not in (30, 60):
        raise ValueError('A primeira parte deve ter 30 ou 60 segundos.')
    return value


def split(source: Path, directory: Path, progress=lambda *_: None, cancel=None, first_part_seconds=60):
    validate_first_part(first_part_seconds)
    duration, width, height, audio = probe(source)
    timeline = presentation_timeline(source, cancel=cancel)
    measured = max(timeline.values())
    # Container durations remain subject to the input limit; splitting uses samples/frames.
    logger.info('media stage=measure declared=%.6f presentation=%.6f', duration, measured)
    duration = measured
    count = 1 + math.ceil(max(0, duration - first_part_seconds) / 60)
    offset = 0
    parts = []
    for index in range(count):
        progress(index, count)
        name = f'video-parte-{index + 1:02}-de-{count:02}.mp4'
        target = directory / name
        ceiling = first_part_seconds if index == 0 else 60
        span = min(ceiling, duration - offset)
        # Trim after filtering: input seeking can discard the frame straddling
        # a cut at 29.97 fps/VFR and restart the fps conversion with a new phase.
        args = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '1',
                '-filter_threads', '1', '-protocol_whitelist', 'file', '-format_whitelist', INPUT_FORMATS,
                '-i', str(source), '-ss', str(offset), '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn',
                '-t', f'{span:.9f}', '-vf',
                f'scale={width}:{height},setsar=1,tpad=stop_mode=clone:stop_duration={duration:.9f},fps=30',
                '-c:v', 'libx264', '-threads', '1', '-preset', 'veryfast', '-crf', '23',
                '-maxrate', '2500k', '-bufsize', '5000k', '-pix_fmt', 'yuv420p',
                '-af', 'aresample=48000:async=1:first_pts=0',
                '-c:a', 'aac', '-b:a', '128k', '-ar', '48000',
                '-map_metadata', '-1', '-metadata:s:v', 'rotate=0',
                '-movflags', '+faststart', str(target)]
        try:
            run(args, cancel=cancel)
        except Exception:
            logger.warning('media stage=encode part=%d expected=%.6f ceiling=%d', index + 1, span, ceiling)
            raise
        result = json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
                                 '-of', 'json', target], timeout=30))
        actual = float(result['format']['duration'])
        for kind, end in timeline.items():
            expected = min(span, max(0, end - offset))
            stream = next((s for s in result['streams'] if s['codec_type'] == kind), {})
            stream_actual = float(stream.get('duration', 0))
            tolerance = 1 / 30 + .001 if kind == 'video' else 1 / 48000 + .001
            if stream_actual > ceiling or stream_actual < expected - tolerance:
                logger.warning('media stage=validate part=%d stream=%s actual=%.6f expected=%.6f ceiling=%d',
                               index + 1, kind, stream_actual, expected, ceiling)
                raise MediaError('Não foi possível preservar o final dentro do limite desta parte. Tente outro arquivo.')
        # 30 fps quantization (one frame) plus MP4 millisecond rounding.
        if actual > ceiling or actual < span - (1 / 30 + .001):
            logger.warning('media stage=validate part=%d actual=%.6f expected=%.6f ceiling=%d',
                           index + 1, actual, span, ceiling)
            raise MediaError('Não foi possível preservar o final dentro do limite desta parte. Tente outro arquivo.')
        offset += span
        parts.append({'name': name, 'duration': actual, 'bytes': target.stat().st_size, 'audio': audio})
    return parts
