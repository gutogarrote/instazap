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


MANUAL_CEILING = 90
PREVIEW_SIDE = 640
FRAME = Fraction(1, 30)


def validate_cut(value, duration):
    """A cut is a finite JSON number in [0, duration). Booleans/strings are not numbers here."""
    message = 'Escolha um ponto de corte entre o início e o fim do vídeo.'
    if type(value) not in (int, float):
        raise ValueError(message)
    try:
        value = float(value)
    except OverflowError:
        raise ValueError(message) from None
    if not math.isfinite(value) or not 0 <= value < duration:
        raise ValueError(message)
    return value


def cut_plan(cut, duration):
    """Exact remainder plan: the fewest equal parts of at most 90 s.

    The chosen point (1 µs resolution) goes down to the 30 fps output grid, at
    most one frame earlier, so the first output frame is the one shown there.
    Exact fractions (no float ceil can add an empty part); inner boundaries sit
    on the grid, so each part is within one frame of remainder/count. Rounding
    them down keeps parts >= 60 s when that is feasible; rounding them up keeps
    long parts <= 90 s.
    """
    measured = Fraction(duration)
    # Microsecond resolution first (ffprobe's): 17.4 is binary 17.3999…, which must not lose a frame.
    exact = Fraction(validate_cut(cut, float(measured)))
    cut = Fraction(round(exact * 1_000_000), 1_000_000)
    duration = Fraction(round(measured * 1_000_000), 1_000_000)
    if cut >= duration:  # Rounding must not move a valid cut onto the end (an empty plan).
        cut, duration = exact, measured
    start = math.floor(cut / FRAME) * FRAME
    remainder = duration - start
    count = math.ceil(remainder / MANUAL_CEILING)
    grid = math.floor if remainder / count < 75 else math.ceil
    bounds = [start + grid(remainder * i / count / FRAME) * FRAME for i in range(count)] + [duration]
    return [(bounds[i], bounds[i + 1] - bounds[i]) for i in range(count)]


def seconds(value):
    # Floor to microseconds: ffmpeg rescales to the nearest frame/sample, and a
    # rounded-up decimal could exclude the frame exactly at a grid boundary.
    micros = math.floor(Fraction(value) * 1_000_000)
    return f'{micros // 1_000_000}.{micros % 1_000_000:06d}'


def video_filter(width, height, duration):
    # Shared by parts and preview: the same fps=30 grid from the common origin picks the same source frames.
    return f'scale={width}:{height},setsar=1,tpad=stop_mode=clone:stop_duration={duration:.9f},fps=30'


AUDIO_FILTER = 'aresample=48000:async=1:first_pts=0'


def prepare_preview(source: Path, target: Path, cancel=None):
    """Validate limits; write a small local preview on the exact timeline used for cuts.

    Browsers disagree on container start times (Chromium shows raw timestamps,
    e.g. 4.978 s for a stream starting there). The preview is encoded like the
    parts: timestamps relative to the demuxer's common origin (A/V offsets kept),
    the same 30 fps grid and the last frame held through an audio-only tail. So
    player time t shows the frame a cut at t begins with. Returns the decoded
    presentation duration of the source.
    """
    _, width, height, _ = probe(source)
    duration = max(presentation_timeline(source, cancel=cancel).values())
    shrink = min(1, PREVIEW_SIDE / max(width, height))
    width, height = max(2, int(width * shrink) // 2 * 2), max(2, int(height * shrink) // 2 * 2)
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '1', '-filter_threads', '1',
         '-protocol_whitelist', 'file', '-format_whitelist', INPUT_FORMATS, '-i', str(source),
         '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn', '-t', seconds(duration),
         '-vf', video_filter(width, height, duration),
         '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast', '-crf', '30', '-g', '30',
         '-pix_fmt', 'yuv420p', '-af', AUDIO_FILTER, '-c:a', 'aac', '-b:a', '96k', '-ar', '48000',
         '-map_metadata', '-1', '-metadata:s:v', 'rotate=0', '-movie_timescale', '48000',
         '-movflags', '+faststart', str(target)], cancel=cancel)
    data = json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', target], timeout=30))
    starts = [float(s.get('start_time', 0)) for s in data['streams']]
    ends = [float(s.get('start_time', 0)) + float(s.get('duration', 0)) for s in data['streams']]
    # Never offer a preview whose clock could differ from the cut timeline.
    if min(starts) < 0 or min(starts) > 1 / 30 + .001 or max(ends) < duration - (1 / 30 + .001):
        logger.warning('media stage=preview starts=%s end=%.6f expected=%.6f', starts, max(ends), duration)
        raise MediaError('Não foi possível preparar a prévia deste vídeo. Use “Dividir o vídeo inteiro”.')
    return duration


def split(source: Path, directory: Path, progress=lambda *_: None, cancel=None, first_part_seconds=60,
          cut_start=None):
    validate_first_part(first_part_seconds)
    duration, width, height, audio = probe(source)
    timeline = presentation_timeline(source, cancel=cancel)
    measured = max(timeline.values())
    # Container durations remain subject to the input limit; splitting uses samples/frames.
    logger.info('media stage=measure declared=%.6f presentation=%.6f', duration, measured)
    duration = measured
    if cut_start is None:
        count = 1 + math.ceil(max(0, duration - first_part_seconds) / 60)
        ceilings = [first_part_seconds] + [60] * (count - 1)
        plan, offset = [], 0
        for ceiling in ceilings:
            span = min(ceiling, duration - offset)
            plan.append((offset, span))
            offset += span
    else:
        try:
            plan = cut_plan(cut_start, duration)
        except ValueError as exc:
            raise MediaError('O ponto de corte ficou fora da duração do vídeo.') from exc
        ceilings = [MANUAL_CEILING] * len(plan)
        logger.info('media stage=plan start=%.6f parts=%d', float(plan[0][0]), len(plan))
    count = len(plan)
    parts = []
    for index, ((offset, span), ceiling) in enumerate(zip(plan, ceilings, strict=True)):
        progress(index, count)
        name = f'video-parte-{index + 1:02}-de-{count:02}.mp4'
        target = directory / name
        # Trim after filtering: input seeking can discard the frame straddling
        # a cut at 29.97 fps/VFR and restart the fps conversion with a new phase.
        args = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '1',
                '-filter_threads', '1', '-protocol_whitelist', 'file', '-format_whitelist', INPUT_FORMATS,
                '-i', str(source), '-ss', seconds(offset), '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn',
                '-t', seconds(span), '-vf',
                video_filter(width, height, duration),
                '-c:v', 'libx264', '-threads', '1', '-preset', 'veryfast', '-crf', '23',
                '-maxrate', '2500k', '-bufsize', '5000k', '-pix_fmt', 'yuv420p',
                '-af', AUDIO_FILTER,
                '-c:a', 'aac', '-b:a', '128k', '-ar', '48000',
                '-map_metadata', '-1', '-metadata:s:v', 'rotate=0',
                '-movie_timescale', '48000', '-movflags', '+faststart', str(target)]
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
        parts.append({'name': name, 'duration': actual, 'bytes': target.stat().st_size, 'audio': audio})
    return parts
