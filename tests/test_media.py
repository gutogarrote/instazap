import array
import json
import subprocess

import pytest

from instazap.media import MediaError, probe, split


def info(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format',
                                              '-show_streams', '-of', 'json', str(path)]))


@pytest.mark.parametrize('duration,expected', [(120, [60, 60]), (240, [60]*4), (60, [60]),
                                               (61.2, [60, 1.2]), (119.9, [60, 59.9])])
def test_real_splits_and_tail(media, tmp_path, duration, expected):
    source = media(duration)
    parts = split(source, tmp_path)
    assert len(parts) == len(expected)
    for part, wanted in zip(parts, expected, strict=True):
        target = tmp_path / part['name']
        result = info(target)
        actual = float(result['format']['duration'])
        assert 0 < actual <= 60
        assert actual == pytest.approx(wanted, abs=.025)
        v, a = result['streams']
        assert v['codec_name'] == 'h264' and v['pix_fmt'] == 'yuv420p'
        assert a['codec_name'] == 'aac'
        assert float(a['duration']) <= 60
        assert float(v['duration']) <= 60
        # Decode independently, including the entire tail: no reliance on preceding files.
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-i', str(target),
                        '-f', 'null', '-'], check=True)
        packets = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v',
                            '-show_packets', '-show_entries', 'packet=pts_time,duration_time,flags',
                            '-of', 'json', str(target)]))['packets']
        assert 'K' in packets[0]['flags']
        assert max(float(p['pts_time']) + float(p['duration_time']) for p in packets) == pytest.approx(wanted, abs=.034)
    final = tmp_path / parts[-1]['name']
    # Synthetic fixture turns red at the end. Check final content survived, not just metadata.
    pixels = subprocess.check_output(['ffmpeg', '-v', 'error', '-sseof', '-0.1', '-i', str(final),
              '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', '-'])
    assert pixels and sum(pixels[::3]) / len(pixels[::3]) > 200
    assert sum(pixels[2::3]) / len(pixels[2::3]) < 40
    pcm = subprocess.check_output(['ffmpeg', '-v', 'error', '-sseof', '-0.1', '-i', str(final),
                                  '-vn', '-f', 's16le', '-'])
    samples = array.array('h', pcm)
    assert max(abs(v) for v in samples) > 1000


def test_overlimit_is_rejected_without_output(media, tmp_path):
    with pytest.raises(MediaError, match='240'):
        split(media(240.1), tmp_path)
    assert not list(tmp_path.iterdir())


def test_no_audio(media, tmp_path):
    parts = split(media(2.3, audio=False), tmp_path)
    assert not parts[0]['audio']
    result = info(tmp_path / parts[0]['name'])
    assert len(result['streams']) == 1
    assert float(result['format']['duration']) == pytest.approx(2.3, abs=.034)


@pytest.mark.parametrize('size,rotation,sar,expected', [('96x64', 90, None, (64, 96)),
                                                       ('64x96', 0, None, (64, 96)),
                                                       ('96x64', 0, '2/1', (192, 64)),
                                                       ('1920x1080', 0, None, (1280, 720))])
def test_orientation_aspect(media, tmp_path, size, rotation, sar, expected):
    source = media(1, size=size, rotation=rotation, sar=sar)
    assert probe(source)[1:3] == expected
    parts = split(source, tmp_path)
    video = info(tmp_path / parts[0]['name'])['streams'][0]
    assert (video['width'], video['height']) == expected
    assert video['sample_aspect_ratio'] == '1:1'
    assert not any(s.get('rotation') for s in video.get('side_data_list', []))


def test_local_playlist_cannot_read_other_files(media, tmp_path):
    source = tmp_path / 'source'
    source.write_text(f'ffconcat version 1.0\nfile {media(1)}\n')
    with pytest.raises(RuntimeError):
        probe(source)
    source.write_text(f'#EXTM3U\n#EXTINF:1,\n{media(1)}\n#EXT-X-ENDLIST\n')
    with pytest.raises(RuntimeError):
        probe(source)
