import base64
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from backend.mesh import export_mesh
from backend.providers.fal import FalProvider, image_data_uri


def test_hosted_crop_keeps_requested_pixels(tmp_path):
    source = tmp_path / 'source.jpg'
    Image.new('RGB', (360, 640), 'green').save(source)
    uri = image_data_uri(source, [.25, .25, .75, .75])
    decoded = Image.open(io.BytesIO(base64.b64decode(uri.split(',')[1])))
    assert decoded.size == (180, 320)


@pytest.mark.parametrize('url_as_object', [False, True])
def test_official_fal_response_contract(tmp_path, monkeypatch, url_as_object):
    source = tmp_path / 'source.jpg'
    Image.new('RGB', (64, 128), 'green').save(source)
    fixture = tmp_path / 'fixture.glb'
    export_mesh([[-.3, 0, 0], [.3, 0, 0], [0, -2, 0], [0, -1, .2]],
                [[0, 1, 2], [0, 3, 1], [0, 2, 3], [1, 3, 2]], fixture)
    called = []

    def subscribe(endpoint, arguments):
        called.append((endpoint, arguments))
        assert arguments['include_3d_keypoints'] is False
        assert arguments['export_meshes'] is True
        return {'model_glb': {'url': 'https://example.com/output.glb'} if url_as_object else 'https://example.com/output.glb',
                'metadata': {'people': [{'person_id': 0}]}}

    monkeypatch.setitem(sys.modules, 'fal_client', SimpleNamespace(subscribe=subscribe))

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url):
            return SimpleNamespace(content=fixture.read_bytes(), raise_for_status=lambda: None)

    monkeypatch.setattr('backend.providers.fal.httpx.Client', Client)
    info = FalProvider().reconstruct(source, [0, 0, 1, 1], tmp_path / 'result.glb')
    assert called[0][0] == 'fal-ai/sam-3/3d-body'
    assert info['height'] == pytest.approx(2)
    assert info['provider'] == 'fal'
    assert info['cameraToViewer'] == [1, 1, 1]  # Never double-flip an already exported GLB.


def test_ambiguous_subject_rejected(tmp_path, monkeypatch):
    image = tmp_path / 'source.jpg'
    Image.new('RGB', (64, 128), 'green').save(image)
    monkeypatch.setitem(sys.modules, 'fal_client', SimpleNamespace(subscribe=lambda *a, **k: {'metadata': {'people': [{}, {}]}}))
    with pytest.raises(RuntimeError, match='Detected 2 people'):
        FalProvider().reconstruct(image, [0, 0, 1, 1], tmp_path / 'result.glb')
