"""Preview clock == cut clock, whatever the source container's start time.

Synthetic TEST fixtures only (ffmpeg lavfi): testsrc2 draws a distinct picture on
every frame and the chirp gives every instant its own pitch, so a frame or audio
window found in two files is the same moment of the source.
"""
import array
import json
import math
import subprocess

import pytest

from instazap.media import split

FRAME = 1 / 30
TOLERANCE = FRAME + .001


def command(*args):
    return subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-filter_threads', '1',
                                    *map(str, args)])


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                               '-of', 'json', str(path)]))


def fixture(tmp_path, kind, video=6.0, audio=6.0):
    """zero/positive: MP4 starting at 0 or +5 s (AAC priming puts audio 21 ms earlier);
    transport: MPEG-TS at ~1.4 s with video 21 ms after audio; negative: Matroska at -0.021 s."""
    suffix = {'transport': 'ts', 'negative': 'mkv'}.get(kind, 'mp4')
    path = tmp_path / f'synthetic-{kind}.{suffix}'
    args = ['-f', 'lavfi', '-i', f'testsrc2=s=96x128:r=30:d={video}',
            '-f', 'lavfi', '-i', f'aevalsrc=0.3*sin(2*PI*(300*t+3*t*t)):s=48000:d={audio}',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac']
    if kind == 'positive':
        args += ['-output_ts_offset', '5']
    command(*args, path)
    start = float(probe(path)['format']['start_time'])
    assert {'zero': start == 0, 'positive': start > 4.9, 'transport': start > 1,
            'negative': start < 0}[kind], (kind, start)
    return path


def frames(path):
    """Decoded frames of the first video stream with times on the file's own origin."""
    origin = float(probe(path)['format'].get('start_time', 0))
    times = [float(f['best_effort_timestamp_time']) - origin for f in json.loads(subprocess.check_output(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_frames', '-show_entries',
         'frame=best_effort_timestamp_time', '-of', 'json', str(path)]))['frames']]
    raw = command('-i', path, '-map', '0:v:0', '-fps_mode', 'passthrough', '-s', '96x128',
                  '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-')
    size = 96 * 128 * 3
    return times, [raw[i:i + size] for i in range(0, len(raw), size)]


def mad(a, b):
    return sum(abs(x - y) for x, y in zip(a[::5], b[::5], strict=True)) / len(a[::5])


def shown(times, pictures, t):
    """What a player displays at time t: the last frame presented at or before t."""
    return pictures[max(i for i, start in enumerate(times) if start <= t + 1e-6)]


def same_moment(picture, pictures):
    """picture matches exactly one source moment (neighbors differ visibly)."""
    scores = sorted(mad(picture, p) for p in pictures)
    return scores[0]


def pcm(path):
    # Trim AAC end padding to the declared duration (Matroska declares none per stream).
    audio = next(s for s in probe(path)['streams'] if s['codec_type'] == 'audio')
    trim = ['-af', f"atrim=duration={audio['duration']}"] if 'duration' in audio else []
    return array.array('h', command('-i', path, '-vn', *trim, '-ar', '48000', '-ac', '1', '-f', 's16le', '-'))


def correlation(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))


def wait_preview(client, app, data, headers):
    job_id = client.post('/api/jobs/upload', content=data, headers=headers).json()['id']
    assert app.state.worker.once()
    job = client.get(f'/api/jobs/{job_id}').json()
    assert job['state'] == 'awaiting_cut', job
    return job


@pytest.mark.parametrize('kind', ['zero', 'positive', 'transport', 'negative'])
def test_preview_and_cut_share_one_clock(site, tmp_path, kind):
    """Regression: c843c0b served the raw source, so Chromium showed 4.978 s for a +5 s file
    while the cut used the demuxer-relative timeline (a ~5 s jump into unseen content)."""
    client, app = site
    source = fixture(tmp_path, kind)
    job = wait_preview(client, app, source.read_bytes(), {'x-mode': 'manual'})
    preview = tmp_path / 'preview.mp4'
    response = client.get(f"/api/jobs/{job['id']}/source")
    assert response.status_code == 200 and response.headers['content-type'] == 'video/mp4'
    preview.write_bytes(response.content)
    data = probe(preview)
    # A clock any browser reads the same way: starts at the cut origin, covers the measured duration.
    assert all(0 <= float(s['start_time']) <= TOLERANCE for s in data['streams'])
    assert 0 <= float(data['format']['start_time']) <= TOLERANCE
    assert float(data['format']['duration']) == pytest.approx(job['source_duration'], abs=TOLERANCE)
    preview_times, preview_pictures = frames(preview)
    assert all(abs(t * 30 - round(t * 30)) < 1e-3 for t in preview_times)  # Same 30 fps grid as the parts.
    source_times, source_pictures = frames(source)
    preview_audio, source_audio = pcm(preview), pcm(source)
    for index, cut in enumerate((0.0, 1.2345, 3.71, job['source_duration'] - .05)):
        folder = tmp_path / f'cut-{index}'
        folder.mkdir()
        parts = split(source, folder, cut_start=cut)
        first = command('-i', folder / parts[0]['name'], '-frames:v', '1', '-s', '96x128',
                        '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-')
        displayed = shown(preview_times, preview_pictures, cut)
        # The part starts with the picture the player displays at the cut (same source moment)...
        assert mad(first, displayed) <= same_moment(first, source_pictures) + 3, (kind, cut)
        # ...which is the source frame on screen at some instant within ±1/60 s of the cut (30 fps re-timing).
        nearby = [p for i, (t, p) in enumerate(zip(source_times, source_pictures, strict=True))
                  if t <= max(cut, source_times[0]) + 1 / 60 + 1e-3
                  and (source_times[i + 1:] or [math.inf])[0] > cut - 1 / 60 - 1e-3]
        assert min(mad(first, p) for p in nearby) == pytest.approx(same_moment(first, source_pictures), abs=.01)
        # And it sounds like the preview at that point: audio begins where the picture does.
        start = math.floor(round(cut * 10**6) * 30 / 10**6) * 48000 // 30
        out = pcm(folder / parts[0]['name'])
        length = min(1920, len(out), len(preview_audio) - start, len(source_audio) - start)
        if length >= 480:
            assert correlation(out[:length], preview_audio[start:start + length]) > .95, (kind, cut)
            assert correlation(out[:length], source_audio[start:start + length]) > .95, (kind, cut)
        # Nothing after the cut is lost: the parts' audio reaches the end of the source audio.
        total = sum(len(pcm(folder / p['name'])) for p in parts)
        assert total >= len(source_audio) - start - 48, (kind, cut, total)


def test_end_to_end_api_cut_on_positive_offset(site, tmp_path):
    client, app = site
    source = fixture(tmp_path, 'positive')
    job = wait_preview(client, app, source.read_bytes(), {'x-mode': 'manual'})
    preview = tmp_path / 'preview.mp4'
    preview.write_bytes(client.get(f"/api/jobs/{job['id']}/source").content)
    times, pictures = frames(preview)
    assert client.post(f"/api/jobs/{job['id']}/cut", json={'start': 2.4567}).status_code == 202
    assert app.state.worker.once()
    ready = client.get(f"/api/jobs/{job['id']}").json()
    assert ready['state'] == 'ready'
    part = tmp_path / 'part.mp4'
    part.write_bytes(client.get(f"/api/jobs/{job['id']}/files/{ready['parts'][0]['name']}").content)
    first = command('-i', part, '-frames:v', '1', '-s', '96x128', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-')
    assert mad(first, shown(times, pictures, 2.4567)) <= same_moment(first, pictures) + 3
    assert sum(p['duration'] for p in ready['parts']) == pytest.approx(job['source_duration'] - 2.4333333,
                                                                       abs=TOLERANCE)


def test_audio_only_tail_is_visible_and_cuttable(site, tmp_path):
    """Video ends at 1 s, audio at 4 s: the preview holds the last picture through the tail."""
    client, app = site
    source = fixture(tmp_path, 'zero', video=1.0, audio=4.0)
    job = wait_preview(client, app, source.read_bytes(), {'x-mode': 'manual'})
    assert job['source_duration'] == pytest.approx(4.0, abs=.03)
    preview = tmp_path / 'preview.mp4'
    preview.write_bytes(client.get(f"/api/jobs/{job['id']}/source").content)
    video = next(s for s in probe(preview)['streams'] if s['codec_type'] == 'video')
    assert float(video['duration']) >= job['source_duration'] - TOLERANCE
    times, pictures = frames(preview)
    _, source_pictures = frames(source)
    assert mad(shown(times, pictures, 3.0), source_pictures[-1]) <= same_moment(source_pictures[-1], pictures) + 3
    assert client.post(f"/api/jobs/{job['id']}/cut", json={'start': 3.0}).status_code == 202
    assert app.state.worker.once()
    ready = client.get(f"/api/jobs/{job['id']}").json()
    assert ready['state'] == 'ready'
    assert ready['parts'][0]['duration'] == pytest.approx(1.0, abs=TOLERANCE)
    part = tmp_path / 'tail.mp4'
    part.write_bytes(client.get(f"/api/jobs/{job['id']}/files/{ready['parts'][0]['name']}").content)
    first = command('-i', part, '-frames:v', '1', '-s', '96x128', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-')
    assert mad(first, source_pictures[-1]) <= same_moment(first, source_pictures) + 3
    assert correlation(pcm(part)[:1920], pcm(source)[3 * 48000:3 * 48000 + 1920]) > .95


def test_waiting_job_from_older_version_gets_a_normalized_preview(site, tmp_path, monkeypatch):
    client, app = site
    source = fixture(tmp_path, 'positive')
    job = wait_preview(client, app, source.read_bytes(), {'x-mode': 'manual'})
    folder = app.state.store.files / job['id']
    (folder / 'preview.mp4').unlink()  # c843c0b kept only the raw source while waiting.
    app.state.store.recover()
    assert client.get(f"/api/jobs/{job['id']}").json()['state'] == 'queued'
    monkeypatch.setattr('instazap.worker.Worker.fetch', lambda *args: pytest.fail('no new download'))
    assert app.state.worker.once()
    again = client.get(f"/api/jobs/{job['id']}").json()
    assert again['state'] == 'awaiting_cut' and again['source_duration'] == job['source_duration']
    assert sorted(p.name for p in folder.iterdir()) == ['preview.mp4', 'source']
