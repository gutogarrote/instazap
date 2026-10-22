"""Synthetic unequal timelines and malformed AAC packet duration; no private assets."""
import array
import json
import math
import struct
import subprocess

import pytest

from instazap.media import presentation_duration, split


def command(*args):
    return subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1',
                                    '-filter_threads', '1', *map(str, args)])


def metadata(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                                               '-show_format', '-of', 'json', str(path)]))


def pcm(path):
    audio = next(s for s in metadata(path)['streams'] if s['codec_type'] == 'audio')
    return array.array('h', command('-i', path, '-vn', '-af', f"atrim=duration={audio['duration']}",
                                    '-ar', '48000', '-ac', '1', '-f', 's16le', '-'))


def inflate_audio_tail(path):
    """Overstate only the final AAC packet by 3396 samples (70.75ms).

    Like the reproduction, stts/container claim time that the AAC frame cannot
    decode. The preceding packet timing and all encoded content stay unchanged.
    """
    data = bytearray(path.read_bytes())

    def boxes(start, end):
        while start < end:
            size, kind = struct.unpack_from('>I4s', data, start)
            yield start, start + size, kind
            start += size

    def walk(start, end, audio=False):
        for pos, stop, kind in boxes(start, end):
            body = pos + 8
            if kind == b'trak':
                audio = b'soun' in data[body:stop]
            if kind in (b'moov', b'trak', b'mdia', b'minf', b'stbl'):
                walk(body, stop, audio)
            elif kind == b'stts' and audio:
                entries = struct.unpack_from('>I', data, body + 4)[0]
                last = body + 8 + (entries - 1) * 8
                assert struct.unpack_from('>I', data, last)[0] == 1
                value = struct.unpack_from('>I', data, last + 4)[0]
                struct.pack_into('>I', data, last + 4, value + 3396)
            elif kind == b'edts' and audio:
                walk(body, stop, audio)
            elif kind == b'elst' and audio:
                value = struct.unpack_from('>I', data, body + 8)[0]
                struct.pack_into('>I', data, body + 8, value + 71)
            elif kind in (b'mvhd', b'tkhd', b'mdhd') and (audio or kind == b'mvhd'):
                assert data[body] == 0
                position = body + (20 if kind == b'tkhd' else 16)
                value = struct.unpack_from('>I', data, position)[0]
                struct.pack_into('>I', data, position, value + (3396 if kind == b'mdhd' else 71))
    walk(0, len(data))
    path.write_bytes(data)


def fixture(tmp_path, video, audio, rate='30', offset=0, vfr=False):
    source = tmp_path / 'synthetic.mp4'
    vf = f"drawbox=color=red:t=fill:enable='gte(t,{video - .15})'"
    if vfr:
        vf += ",select='not(eq(mod(n,11),0))'"
    command('-f', 'lavfi', '-i', f'color=blue:s=64x48:r={rate}:d={video}',
            '-f', 'lavfi', '-i', f'aevalsrc=0.3*sin(2*PI*(300*t+3*t*t)):s=48000:d={audio}',
            '-vf', vf, '-fps_mode', 'vfr', '-c:v', 'libx264', '-threads', '1',
            '-preset', 'ultrafast', '-c:a', 'aac', '-output_ts_offset', offset, source)
    return source


@pytest.mark.parametrize('first', [30, 60])
@pytest.mark.parametrize('case', ['audio_longer', 'video_longer', 'inflated_aac', 'ntsc', 'vfr_offset'])
def test_unequal_presentation_and_content(tmp_path, first, case):
    video, audio = (61.1, 61.192) if case != 'video_longer' else (61.2, 61.108)
    source = fixture(tmp_path, video, audio, rate='30000/1001' if case == 'ntsc' else '30',
                     offset=5 if case == 'vfr_offset' else 0, vfr=case == 'vfr_offset')
    measured = presentation_duration(source)
    if case == 'inflated_aac':
        inflate_audio_tail(source)
        assert float(metadata(source)['format']['duration']) - measured > .05
        # Removing the short final presentation duration exposes at most one
        # AAC frame of previously signaled encoder padding (1024/48000).
        assert presentation_duration(source) == pytest.approx(measured, abs=1024/48000)
        measured = presentation_duration(source)
    original = pcm(source)
    parts = split(source, tmp_path, first_part_seconds=first)
    assert len(parts) == 2
    joined = array.array('h')
    for index, part in enumerate(parts):
        target = tmp_path / part['name']
        data = metadata(target)
        cap = first if index == 0 else 60
        assert 0 < float(data['format']['duration']) <= cap
        assert all(float(s['duration']) <= cap for s in data['streams'])
        assert all(abs(float(s['start_time'])) < 1/48000 for s in data['streams'])
        command('-i', target, '-f', 'null', '-')  # Independent full decode.
        joined.extend(pcm(target))
    assert sum(p['duration'] for p in parts) == pytest.approx(measured, abs=1/30 + .001)
    # Compare actual sound across the join and at the audio tail, sample for sample.
    # A chirp detects lost/repeated samples that duration sums or a constant tone miss.
    assert len(joined) >= len(original) - 48
    for start in (first * 48000 - 2400, first * 48000, len(original) - 2400):
        a, b = original[start:start+1920], joined[start:start+1920]
        assert len(a) == len(b) == 1920
        correlation = sum(x*y for x, y in zip(a, b, strict=True)) / math.sqrt(
            sum(x*x for x in a) * sum(y*y for y in b))
        assert correlation > .95
    pixels = command('-sseof', '-0.06', '-i', tmp_path / parts[-1]['name'],
                     '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-threads', '1', '-')
    assert pixels and sum(pixels[::3]) / len(pixels[::3]) > 200


def test_audio_tail_validation_and_sanitized_diagnostics(media, tmp_path, monkeypatch, caplog):
    import instazap.media as module
    source = media(1)
    original_run = module.run

    def missing_audio_tail(args, **kwargs):
        output = original_run(args, **kwargs)
        if args[0] == 'ffprobe' and args[-1].parent == tmp_path:
            result = json.loads(output)
            next(s for s in result['streams'] if s['codec_type'] == 'audio')['duration'] = '0.800'
            return json.dumps(result).encode()
        return output

    monkeypatch.setattr(module, 'run', missing_audio_tail)
    with pytest.raises(module.MediaError, match='preservar'):
        split(source, tmp_path)
    assert 'stage=validate part=1 stream=audio actual=0.800000 expected=1.000000 ceiling=60' in caplog.text
    assert str(source) not in caplog.text and str(tmp_path) not in caplog.text


@pytest.mark.parametrize('first', [30, 60])
def test_ntsc_frame_straddling_join_survives(tmp_path, first):
    source = tmp_path / 'ntsc-marker.mp4'
    marker = math.floor(first * 30000 / 1001)
    command('-f', 'lavfi', '-i', 'color=blue:s=64x48:r=30000/1001:d=61.2',
            '-vf', f"drawbox=color=red:t=fill:enable='eq(n,{marker})'",
            '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast', source)
    parts = split(source, tmp_path, first_part_seconds=first)
    # The source frame begins just before the cut and presents across it. Global
    # 30 fps conversion puts it at the first frame of the second part.
    pixels = command('-i', tmp_path / parts[1]['name'], '-frames:v', '1',
                     '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-threads', '1', '-')
    assert sum(pixels[::3]) / len(pixels[::3]) > 200
    assert sum(pixels[2::3]) / len(pixels[2::3]) < 40


def test_fractional_equal_joins_preserve_chirp_and_frames(tmp_path):
    from instazap.media import partition
    source = fixture(tmp_path, 205.1, 205.092, rate='30000/1001')
    measured = presentation_duration(source)
    spans = partition(measured, 15)
    cuts = [float(sum(spans[:i])) for i in range(1, len(spans))]
    # Mark frames straddling ALL cuts, including the fractional equal cuts.
    marked = tmp_path / 'marked.mp4'
    markers = [math.floor(cut * 30000 / 1001) for cut in cuts]
    vf = ','.join(f"drawbox=color=red:t=fill:enable='eq(n,{n})'" for n in markers)
    command('-i', source, '-vf', vf, '-c:v', 'libx264', '-threads', '1',
            '-preset', 'ultrafast', '-c:a', 'copy', marked)
    original = pcm(marked)
    parts = split(marked, tmp_path, first_part_seconds=15)
    assert len(parts) == 4
    joined = array.array('h')
    for part in parts:
        joined.extend(pcm(tmp_path / part['name']))
    # Chirp correlation at each cut and the final samples detects omitted/repeated content.
    for cut in cuts + [len(original)/48000 - .05]:
        start = round(cut * 48000)
        a, b = original[start:start+1920], joined[start:start+1920]
        assert len(a) == len(b) == 1920
        correlation = sum(x*y for x, y in zip(a, b, strict=True)) / math.sqrt(
            sum(x*x for x in a) * sum(y*y for y in b))
        assert correlation > .95
    for previous, following in zip(parts, parts[1:], strict=False):
        # Fractional cuts fall between output frames. The straddling source
        # frame may land on either side, but must survive near each join.
        pixels = command('-sseof', '-0.07', '-i', tmp_path / previous['name'], '-frames:v', '2',
                         '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-threads', '1', '-')
        pixels += command('-i', tmp_path / following['name'], '-frames:v', '2',
                          '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-threads', '1', '-')
        frame_bytes = 64 * 48 * 3
        assert any(sum(pixels[i:i+frame_bytes:3]) / (frame_bytes/3) > 180
                   for i in range(0, len(pixels), frame_bytes))


def test_original_audio_delay_survives_equal_splits(tmp_path):
    source = tmp_path / 'delayed-audio.mp4'
    command('-f', 'lavfi', '-i', 'color=blue:s=64x48:r=30:d=150.1',
            '-itsoffset', '0.25', '-f', 'lavfi', '-i',
            'aevalsrc=0.3*sin(2*PI*(300*t+3*t*t)):s=48000:d=149.85',
            '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast', '-c:a', 'aac', source)
    normalized = tmp_path / 'original.pcm'
    command('-i', source, '-vn', '-af', 'aresample=48000:async=1:first_pts=0',
            '-ac', '1', '-f', 's16le', normalized)
    original = array.array('h', normalized.read_bytes())
    parts = split(source, tmp_path, first_part_seconds=30)
    joined = array.array('h')
    for part in parts:
        joined.extend(pcm(tmp_path / part['name']))
    assert max(abs(v) for v in joined[:int(.20*48000)]) < 50
    for start in [int(.3*48000), 30*48000, 90*48000, 149*48000]:
        a, b = original[start:start+1920], joined[start:start+1920]
        correlation = sum(x*y for x, y in zip(a, b, strict=True)) / math.sqrt(
            sum(x*x for x in a) * sum(y*y for y in b))
        assert correlation > .95
