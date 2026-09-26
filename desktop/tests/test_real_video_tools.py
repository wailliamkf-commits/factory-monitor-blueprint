"""Boundaries that matter when inspecting real local media."""
import importlib.util
from pathlib import Path
import hashlib
import sys
import numpy as np
import pytest

SCRIPTS = Path(__file__).parents[1] / 'scripts'
sys.path.insert(0,str(SCRIPTS))

def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f'{name}.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

inspect = module('inspect_real_video')
review = module('review_real_video')

def test_crop_never_silently_clamps_wrong_layout():
    frame = np.zeros((20, 30, 3), dtype=np.uint8)
    for rect in [[0,0,31,20],[-1,0,30,20],[0,0,0,20],[True,0,2,2],[0,0,1.5,2]]:
        with pytest.raises(ValueError):
            inspect.pixel_crop(frame, rect)
    assert inspect.pixel_crop(frame,[10,10,20,10]).shape == (10,20,3)

def test_hash_streams_large_media_without_path_read_bytes(tmp_path, monkeypatch):
    path = tmp_path / 'clip.dat'
    value = b'x' * 2_500_000
    path.write_bytes(value)
    monkeypatch.setattr(Path, 'read_bytes', lambda _: pytest.fail('full-file memory allocation'))
    assert inspect.file_sha256(path) == hashlib.sha256(value).hexdigest()

@pytest.mark.parametrize('value,expected',[('bytes=0-0',(0,0)),('bytes=4-',(4,99)),('bytes=-10',(90,99)),('bytes=80-200',(80,99))])
def test_video_seek_ranges(value, expected):
    assert review.range_bounds(value,100) == expected

@pytest.mark.parametrize('value',['bytes=100-','bytes=4-1','bytes=-0','bytes=','bytes=1-2,3-4','oops'])
def test_invalid_media_ranges_are_rejected(value):
    with pytest.raises(ValueError):
        review.range_bounds(value,100)


def test_layout_identity_and_intervals_cannot_mix_channels():
    import copy
    profile = {'size':[30,20], 'segments':[{'id':'a','start':0,'end':4,
                'cameras':[{'id':'one','crop':[0,0,30,20],'clock':[0,0,10,5]}]}]}
    inspect.validate_profile(profile)
    for invalid in ['duplicate_layout','duplicate_camera','overlap','nan']:
        bad=copy.deepcopy(profile)
        if invalid=='duplicate_camera':
            bad['segments'][0]['cameras'] *= 2
        elif invalid=='nan':
            bad['segments'][0]['end'] = float('nan')
        else:
            segment=copy.deepcopy(bad['segments'][0])
            segment.update(start=5,end=10)
            if invalid=='overlap': segment.update(id='b',start=3)
            bad['segments'].append(segment)
        with pytest.raises(ValueError):
            inspect.validate_profile(bad)


def test_model_configuration_isolated_before_import_and_rejects_wrong_class(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace
    weights = tmp_path / 'weights.pt'
    weights.write_bytes(b'unit fixture')
    for key in ['YOLO_OFFLINE','YOLO_AUTOINSTALL','YOLO_CONFIG_DIR']:
        monkeypatch.setenv(key, '')
    class Settings(dict):
        @property
        def file(self):
            return Path(os.environ['YOLO_CONFIG_DIR']) / 'Ultralytics' / 'settings.json'
    settings = Settings(sync=True)
    def model(_):
        assert Path(os.environ['YOLO_CONFIG_DIR']).is_dir()
        assert os.environ['YOLO_OFFLINE'] == 'true'
        assert os.environ['YOLO_AUTOINSTALL'] == 'false'
        assert settings['sync'] is False
        return SimpleNamespace(names={0:'person'})
    monkeypatch.setitem(sys.modules,'ultralytics',SimpleNamespace(YOLO=model,settings=settings))
    inspect.load_person_model(weights,tmp_path/'output')
    monkeypatch.setitem(sys.modules,'ultralytics',SimpleNamespace(
        YOLO=lambda _:SimpleNamespace(names={0:'product'}),settings=settings))
    with pytest.raises(ValueError,match='person'):
        inspect.load_person_model(weights,tmp_path/'output2')
    shared = SimpleNamespace(file=tmp_path/'shared'/'settings.json')
    monkeypatch.setitem(sys.modules,'ultralytics',SimpleNamespace(YOLO=model,settings=shared))
    with pytest.raises(RuntimeError,match='not isolated'):
        inspect.load_person_model(weights,tmp_path/'output3')


def test_native_capture_packet_rejects_incomplete_and_oversized_images():
    import io
    capture=module('capture_window_probe')
    header=capture.HEADER
    good=header.pack(1.0,2.0,0,2,1,8)+bytes([1,2,3,255,4,5,6,255])
    callback,pts,image=capture.read_packet(io.BytesIO(good))
    assert (callback,pts)==(1.0,2.0)
    assert image.tolist()==[[[1,2,3],[4,5,6]]]
    for bad in [header.pack(1,2,0,5000,1,20000),header.pack(1,2,0,2,1,7),
                header.pack(1,2,1,2,1,8)+bytes(8)]:
        with pytest.raises(ValueError): capture.read_packet(io.BytesIO(bad))
    with pytest.raises(EOFError): capture.read_packet(io.BytesIO(good[:-1]))
