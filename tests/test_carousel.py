import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "carousel"))
sys.path.insert(0, str(ROOT))

import brief_parser  # noqa: E402
import layout as L  # noqa: E402
import media as M  # noqa: E402

BRIEF = (ROOT / "tests" / "fixtures" / "livetasting.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def slides():
    return brief_parser.parse_brief(BRIEF)


def test_parses_five_slides(slides):
    assert [s["index"] for s in slides] == [1, 2, 3, 4, 5]


def test_title_slide(slides):
    s = slides[0]
    assert s["preset"] == "ontour"
    assert s["headline"] == ["LIVE", "TASTING"]
    assert s["subline"] == "SUMMER EDITION & HOUSE RIESLING | 01.06.2030 | 18:00 UHR"
    assert s["media_type"] == "image"
    assert len(s["media_urls"]) == 2          # "oder:" alternative kept


def test_media_types_and_text(slides):
    assert [s["media_type"] for s in slides] == ["image", "video", "image", "video", "image"]
    assert all(s["preset"] == "body" for s in slides[1:])
    assert slides[2]["body"] == "Während Ihr die Weine probiert, geben wir Einblicke in unsere Arbeit."
    assert slides[4]["body"].splitlines()[0] == "So seid Ihr dabei:"


def test_gettoknow_detected():
    s = brief_parser.parse_brief("Slide 1 (Typo wie GET TO KNOW)\nKlein Typo:\nWhy we do things\nPruning\n")[0]
    assert s["preset"] == "gettoknow"
    assert s["headline"] == ["Why we do things", "Pruning"]


@pytest.mark.parametrize("i", [1, 2, 3])
def test_body_wrap_4_to_6_lines(slides, i):
    lines, size = L.wrap_body(slides[i]["body"])
    assert 4 <= len(lines) <= 6
    assert all(L.measure(l, "neutra", size, L.BODY["tracking"]) <= L.BODY["max_width"] for l in lines)


def test_wrap_matches_designer(slides):
    assert L.wrap_body(slides[2]["body"])[0] == [
        "WÄHREND IHR DIE", "WEINE PROBIERT,", "GEBEN WIR EINBLICKE", "IN UNSERE ARBEIT."]
    assert L.wrap_body(slides[3]["body"])[0][0] == "DAS HIGHLIGHT –"


def test_list_mode(slides):
    lines, size = L.wrap_body(slides[4]["body"])
    assert size == L.LIST_SIZE
    assert lines[:2] == ["SO SEID IHR DABEI:", "TICKET SICHERN"]
    assert "" in lines


def test_subline_split():
    spec = L.CFG["presets"]["ontour"]["subline"]
    assert L.wrap_subline("SUMMER EDITION & HOUSE RIESLING | 01.06.2030 | 18:00 UHR", spec) == [
        "SUMMER EDITION", "& HOUSE RIESLING", "01.06.2030 | 18:00 UHR"]


def test_upload_matching():
    slides = [{"index": 1, "media_type": "image", "media_sel": 0,
               "media": [{"status": "ok", "kind": "image"}]},
              {"index": 2, "media_type": "video", "media_sel": 0,
               "media": [{"status": "needs_file", "kind": "video",
                          "expected_name": "Livetasting Carousel Slide 3.mp4"}]}]
    assert M.match_upload("Livetasting Carousel Slide 3.mp4", slides) == (1, 0)
    assert M.match_upload("whatever.mov", slides) == (1, 0)
    assert M.match_upload("photo.jpg", slides) is None


def test_shade_settings_and_modes():
    import render as R
    from PIL import Image
    assert R.shade_settings({"shade": True})["on"] is True          # old boolean projects
    assert R.shade_settings({"shade": None})["on"] is False
    slide = {"shade": {"on": True, "opacity": 1.0, "softness": 0, "spread": 10,
                       "mode": "multiply", "color": "#808080"}}
    els = [{"id": "body", "img": Image.new("RGBA", (100, 50)), "x": 500, "y": 700},
           {"id": "logo", "img": Image.new("RGBA", (100, 50)), "x": 0, "y": 0}]
    assert R.shade_rects(slide, els) == [(490, 690, 610, 760)]       # logo gets no shade
    bg = Image.new("RGB", (R.W, R.H), (200, 100, 50))
    out = R.apply_shade(bg, slide, els)
    assert out.getpixel((550, 725)) == (100, 50, 25)                 # multiply by 50% grey
    assert out.getpixel((10, 10)) == (200, 100, 50)                  # outside untouched
    slide["shade"]["mode"] = "normal"
    assert R.apply_shade(bg, slide, els).getpixel((550, 725)) == (128, 128, 128)


def test_download_name_matching(tmp_path):
    f = tmp_path / "Livetasting  Carousel Slide 4 (1).mp4"
    f.write_bytes(b"x" * 10)
    assert M.find_download("Livetasting Carousel Slide 4.mp4", tmp_path) is None   # size not settled yet
    assert M.find_download("Livetasting Carousel Slide 4.mp4", tmp_path) == f
    assert M.find_download("Carousel Slide 5.mp4", tmp_path) is None
