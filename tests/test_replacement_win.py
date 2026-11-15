"""Windows replacement assets: image decoding edge cases, PyAV video containers, screenshot for game cards."""
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows decoding paths")
DATA = Path(__file__).parent / "data"


def test_images_with_unicode_paths_and_unusual_formats(tmp_path):
    from vcstudio.platform.win.imaging import ImageDecodeError, decode_bgra
    d = tmp_path / "Картинки для замены"
    d.mkdir()
    rgba = np.zeros((20, 30, 4), np.uint8)
    rgba[:, :15] = (0, 0, 255, 255)                      # opaque red (BGRA) / transparent right half
    rgba[:, 15:] = (0, 255, 0, 0)
    p = d / "логотип.png"
    cv2.imencode(".png", rgba)[1].tofile(str(p))         # cv2.imwrite cannot write Unicode paths either
    img = decode_bgra(p)
    assert img.shape == (20, 30, 4)
    assert tuple(img[5, 5]) == (0, 0, 255, 255) and tuple(img[5, 25]) == (0, 0, 0, 0)    # premultiplied
    p16 = d / "16bit.png"
    cv2.imencode(".png", (np.ones((8, 8, 3), np.uint16) * 65535))[1].tofile(str(p16))
    assert decode_bgra(p16)[0, 0].tolist() == [255, 255, 255, 255]
    pg = d / "grey.jpg"
    cv2.imencode(".jpg", np.full((8, 8), 200, np.uint8))[1].tofile(str(pg))
    g = decode_bgra(pg)
    assert g.shape == (8, 8, 4) and abs(int(g[0, 0, 0]) - 200) <= 2 and g[0, 0, 3] == 255
    from PIL import Image
    gif = d / "anim.gif"
    Image.new("RGB", (10, 6), (10, 20, 200)).save(gif)   # OpenCV 4.x does not read GIF: Pillow does
    gi = decode_bgra(gif)
    assert gi.shape == (6, 10, 4) and gi[0, 0, 2] == 10 and gi[0, 0, 0] == 200
    big = d / "big.png"
    cv2.imencode(".png", np.zeros((100, 400, 3), np.uint8))[1].tofile(str(big))
    assert decode_bgra(big, max_side=200).shape == (50, 200, 4)
    bad = d / "broken.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(ImageDecodeError):
        decode_bgra(bad)


def test_asset_library_imports_unicode_named_files(tmp_path):
    from vcstudio.replacement.assets import AssetLibrary
    src = tmp_path / "фото карточки.png"
    cv2.imencode(".png", np.full((40, 30, 4), 128, np.uint8))[1].tofile(str(src))
    lib = AssetLibrary(tmp_path / "assets")
    a = lib.import_file(src)
    assert (a.kind, a.width, a.height, a.name) == ("image", 30, 40, "фото карточки")
    assert lib.image(a).shape == (40, 30, 4) and lib.thumbnail(a).shape[2] == 4


@pytest.mark.parametrize("ext,codec", [(".mkv", "libx264"), (".webm", "libvpx-vp9")])
def test_more_containers_through_pyav(tmp_path, ext, codec):
    import av

    from vcstudio.replacement.assets import AssetLibrary
    from vcstudio.replacement.video import open_decoder, probe
    p = tmp_path / f"clip{ext}"
    try:
        with av.open(str(p), "w") as c:
            s = c.add_stream(codec, rate=25)
            s.width, s.height, s.pix_fmt = 64, 48, "yuv420p"
            for i in range(25):
                f = av.VideoFrame.from_ndarray(np.full((48, 64, 3), i * 10, np.uint8), format="bgr24")
                for pkt in s.encode(f):
                    c.mux(pkt)
            for pkt in s.encode():
                c.mux(pkt)
    except (av.error.FFmpegError, ValueError) as e:
        pytest.skip(f"{codec} encoder not in this PyAV build: {e}")
    info = probe(p)
    assert (info.width, info.height, info.decoder) == (64, 48, "pyav") and abs(info.duration - 1.0) < 0.1
    dec = open_decoder(p, info, 0.5, None)
    t, img = next(iter(dec))
    dec.close()
    assert abs(t - 0.48) < 0.05 and img.shape == (48, 64, 4)
    a = AssetLibrary(tmp_path / "assets").import_file(p)
    assert a.kind == "video" and a.decoder == "pyav"


def test_screenshot_of_the_primary_display():
    from vcstudio.game import cardframe as cf
    try:
        img = cf.screenshot()
    except cf.CalibrationError as e:
        pytest.skip(f"no interactive desktop: {e}")
    assert img.ndim == 3 and img.shape[2] == 3 and img.dtype == np.uint8 and img.shape[0] > 100
