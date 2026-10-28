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
    assert len(s["media_urls"]) == 1
    assert len(s["alt_media_urls"]) == 1      # the link after "oder:"


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


def test_illustrator_export(slides, tmp_path):
    import export_ai
    import project as P
    import render as R
    built = [P.new_slide(s) for s in P.expand_alternatives(slides)]
    runs = R.text_runs(built[3])
    assert [r["text"] for r in runs] == built[3]["lines"]            # one live text object per line
    assert {r["font"] for r in R.text_runs(built[0])} == {"neutra", "trajan"}
    files = export_ai.export_ai(built, tmp_path / "media", tmp_path, "test")
    assert files == ["test_slides.pdf", "Make test.ai (run in Illustrator).jsx"]
    import preset_import
    assert len(preset_import._fitz().open(str(tmp_path / "test_slides.pdf"))) == len(built)   # a page per slide
    jsx = (tmp_path / files[1]).read_text(encoding="utf-8-sig")
    assert '"test_slides.pdf"' in jsx and '"test.ai"' in jsx
    assert '["slide01", "slide01_alt", "slide02"' in jsx                 # one named artboard per slide
    try:
        import esprima
    except ImportError:
        return
    esprima.parseScript("\n".join(l for l in jsx.splitlines() if not l.startswith("#target")))



def test_oder_becomes_alt_slide(slides):
    import project as P
    import render as R
    expanded = P.expand_alternatives(slides)
    assert [(s["index"], s["alt"]) for s in expanded][:3] == [(1, 0), (1, 1), (2, 0)]
    alt = P.new_slide(expanded[1])
    assert alt["media"][0]["url"] == slides[0]["alt_media_urls"][0]
    assert alt["headline"] == ["LIVE", "TASTING"]                      # same text as slide 1
    assert R.slide_stem(alt) == "slide01_alt"
    assert R.slide_stem(P.new_slide(expanded[0])) == "slide01"


def _fake_title_ai(path):
    """A GET-TO-KNOW-like artboard: kicker + title centred, a logo bar, body text at the bottom."""
    import layout as L
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    for name, key in (("NeutraText-Book", "neutra"), ("TrajanPro3-Regular", "trajan")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, L.font_path(key)))
    c = canvas.Canvas(str(path), pagesize=(1080, 1440))
    c.rect(440, 1440 - 200, 200, 40, stroke=0, fill=1)               # "logo"
    def line(text, font, size, tr, baseline):
        w = pdfmetrics.stringWidth(text, font, size) + tr * size * (len(text) - 1)
        t = c.beginText(540 - w / 2, 1440 - baseline)
        t.setFont(font, size); t.setCharSpace(tr * size); t.textOut(text); c.drawText(t)
    line("WHY WE DO THINGS", "NeutraText-Book", 36, 0.3, 691)
    line("PRUNING", "TrajanPro3-Regular", 50, 0.05, 751)
    line("FOR US PRUNING IS NOT ROUTINE", "NeutraText-Book", 32, 0.3, 1267)
    c.showPage(); c.save()


def test_import_title_preset(tmp_path, monkeypatch):
    import preset_import as PI
    import project as P
    import render as R
    monkeypatch.setattr(PI, "USER_DIR", tmp_path)
    _fake_title_ai(tmp_path / "title.ai")
    key, p, warnings = PI.import_preset(tmp_path / "title.ai", "Test Know", 1)
    assert warnings == []
    assert p["order"] == ["kicker", "headline"]
    kick, head = p["slots"]["kicker"], p["slots"]["headline"]
    assert (kick["font"], head["font"]) == ("neutra", "trajan")
    assert kick["align"] == head["align"] == "center"
    assert abs(kick["tracking"] - 0.3) < 0.02 and abs(head["size"] - 50) < 0.1
    assert p["body_size"] == 32 and p["body_default_zone"].startswith("bottom")
    assert p["graphics"]["rect"][2] == pytest.approx(200, abs=3)          # logo kept as a layer
    assert "TEST KNOW" in p["aliases"]
    # the brief picks it by name and fills kicker + headline
    s = P.new_slide(brief_parser.parse_brief(
        "Slide 1 (Typo wie TEST KNOW)\nKlein Typo:\nMeet the team\nHarvest\n")[0])
    assert s["preset"] == key and s["slots"] == {"kicker": "Meet the team", "headline": "Harvest"}
    els = {e["id"]: e for e in R.elements(s)}
    assert {"t_kicker", "t_headline", "graphic"} <= set(els)
    e = els["t_headline"]
    assert abs(e["x"] + e["img"].width / 2 - 540) < 4                    # still centred
    del R.PRESETS[key], brief_parser.PRESETS[key]


def test_linear_shade_spans_full_width():
    import render as R
    from PIL import Image
    shade = {"on": True, "shape": "linear", "opacity": 1.0, "softness": 10, "spread": 0,
             "mode": "normal", "color": "#000000"}
    low = [{"id": "body", "img": Image.new("RGBA", (200, 100)), "x": 800, "y": 1200}]
    mask = R.shade_mask({"shade": shade}, low)
    assert mask.getpixel((0, 1250)) == mask.getpixel((1079, 1250)) == 255   # whole width
    assert mask.getpixel((500, 1439)) == 255                                 # down to the edge
    assert mask.getpixel((500, 1200 - 15)) == 128                            # halfway up the 30px fade
    assert mask.getpixel((500, 1100)) == 0
    high = [{"id": "body", "img": Image.new("RGBA", (200, 100)), "x": 100, "y": 150}]
    assert R.shade_mask({"shade": shade}, high).getpixel((900, 0)) == 255    # hangs from the top


def test_export_options_and_slide_save(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv("CAROUSEL_PROJECTS", str(tmp_path / "projects"))
    import app as A
    import dialogs
    import project as P
    monkeypatch.setattr(P, "ROOT", tmp_path / "projects")
    c = A.app.test_client()
    proj = c.post("/api/projects", json={"brief": BRIEF, "name": "opts"}).json
    name = proj["name"]
    # options: own folder (via the folder dialog), PNG only, no PDF
    out = tmp_path / "my exports"
    monkeypatch.setattr(dialogs, "pick_folder", lambda **k: str(out))
    o = c.post(f"/api/p/{name}/export_options", json={"pick_folder": True, "jpg": False, "pdf": False}).json
    assert o["folder"] == str(out) and o["png"] and not o["jpg"] and o["ai"]
    c.post(f"/api/p/{name}/export", json={})
    while (st := c.get(f"/api/p/{name}/export").json)["running"]:
        time.sleep(0.2)
    assert st["error"] is None
    assert (out / "slide03.png").exists() and not (out / "slide03.jpg").exists()
    assert (out / "slide01_alt.png").exists() and not (out / "preview.pdf").exists()
    root = P.project_dir(name)
    assert (root / f"{name}_slides.pdf").exists()                       # Illustrator files in the root
    assert (root / f"Make {name}.ai (run in Illustrator).jsx").exists()
    assert not list(out.glob("*.ai")) and not list(out.glob("*.jsx"))
    # single slide: PNG only, saved where the dialog says
    target = tmp_path / "elsewhere" / "my slide.png"
    target.parent.mkdir()
    monkeypatch.setattr(dialogs, "save_as", lambda *a, **k: str(target))
    assert c.post(f"/api/p/{name}/slide/2/save_png").json["saved"] == str(target)
    assert target.exists() and len(list(target.parent.iterdir())) == 1
    monkeypatch.setattr(dialogs, "save_as", lambda *a, **k: None)
    assert c.post(f"/api/p/{name}/slide/2/save_png").json == {"cancelled": True}


def test_logo_gradient_from_top():
    import render as R
    from PIL import Image
    els = [{"id": "logo", "img": Image.new("RGBA", (566, 53)), "x": 257, "y": 156},
           {"id": "title", "img": Image.new("RGBA", (700, 300)), "x": 100, "y": 900}]
    slide = {"shade": {"on": False},
             "logo_shade": {"on": True, "opacity": 1.0, "softness": 20, "spread": 0, "mode": "normal"}}
    layers = R.shade_layers(slide, els)
    assert len(layers) == 1                                  # only the logo gradient is on
    mask = layers[0][1]
    assert mask.getpixel((0, 0)) == mask.getpixel((1079, 100)) == 255   # from the top, full width
    assert mask.getpixel((540, 209 + 30)) == 128                         # halfway down the 60px fade
    assert mask.getpixel((540, 900)) == 0                                # title area untouched
    slide["shade"] = {"on": True, "mode": "normal"}
    slide["logo_shade"]["mode"] = "multiply"
    assert [sh["mode"] for sh, _ in R.shade_layers(slide, els)] == ["multiply", "normal"]
    assert R.shade_multiplier(slide, els) is not None                    # video gets the multiply layer


def test_ai_settings_toggle_and_keys(tmp_path, monkeypatch):
    import anthropic
    import ai_parser
    import app as A
    import settings
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    c = A.app.test_client()
    ai = c.get("/api/settings").json["ai"]
    assert ai["active"] == "claude"                                       # Anthropic by default
    assert all(p["source"] is None for p in ai["providers"].values())
    # toggle the API used by "Parse with AI"
    assert c.post("/api/settings/provider", json={"provider": "deepseek"}).json["ai"]["active"] == "deepseek"
    assert settings.provider() == "deepseek"
    assert c.post("/api/settings/provider", json={"provider": "openai"}).status_code == 400
    # keys a provider rejects are not saved
    def reject(provider, key):
        raise anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)
    monkeypatch.setattr(ai_parser, "check_key", reject)
    r = c.post("/api/settings/key", json={"provider": "deepseek", "key": "sk-wrong"})
    assert r.status_code == 400 and "DeepSeek API rejected" in r.json["error"]
    assert settings.saved_key("deepseek") is None
    # good keys are saved, but only their last 4 characters are ever sent back
    monkeypatch.setattr(ai_parser, "check_key", lambda provider, key: None)
    c.post("/api/settings/key", json={"provider": "claude", "key": "  sk-ant-api03-secretsecret-WXYZ  "})
    r = c.post("/api/settings/key", json={"provider": "deepseek", "key": "sk-deepsecret-1234"}).json["ai"]
    assert r["providers"]["claude"]["hint"] == "…WXYZ" and r["providers"]["deepseek"]["hint"] == "…1234"
    assert "secret" not in c.get("/api/settings").get_data(as_text=True)
    # the same SDK client, pointed at the chosen API
    ds = ai_parser.client("deepseek")
    assert ds.api_key == "sk-deepsecret-1234" and "api.deepseek.com/anthropic" in str(ds.base_url)
    assert ai_parser.client("claude").api_key == "sk-ant-api03-secretsecret-WXYZ"
    # remove one, the other stays; old claude_api_key files are still read
    r = c.post("/api/settings/key", json={"provider": "claude", "key": ""}).json["ai"]["providers"]
    assert r["claude"]["source"] is None and r["deepseek"]["source"] == "settings"
    (tmp_path / "settings.json").write_text('{"claude_api_key": "sk-ant-old-ABCD"}')
    assert settings.saved_key("claude") == "sk-ant-old-ABCD"


def test_parse_with_ai_via_deepseek(monkeypatch):
    import json as _json
    import types
    import ai_parser
    import settings
    monkeypatch.setattr(settings, "provider", lambda: "deepseek")
    monkeypatch.setattr(settings, "key", lambda p: "sk-test")
    sent = {}
    answer = {"slides": [
        {"index": "2", "preset": "body", "body": "Text", "media_type": "video",
         "media_urls": "https://x/v", "headline": None},
        {"index": 1, "preset": "made-up", "headline": ["LIVE", "TASTING"], "subline": "A | B",
         "media_type": "image", "media_urls": ["https://x/a"], "alt_media_urls": ["https://x/b"]}]}

    class Messages:
        def create(self, **kw):
            sent.update(kw)
            text = "```json\n" + _json.dumps(answer) + "\n```"          # fenced, like many models do
            return types.SimpleNamespace(stop_reason="end_turn",
                                         content=[types.SimpleNamespace(type="text", text=text)])

    made = {}
    def fake_client(provider=None, key=None, **kw):
        made["provider"] = provider
        return types.SimpleNamespace(messages=Messages())
    monkeypatch.setattr(ai_parser, "client", fake_client)
    slides = ai_parser.parse_brief("Slide 1 …")
    assert made["provider"] == "deepseek" and sent["model"] == "deepseek-chat"
    assert "betas" not in sent and "JSON" in sent["system"]
    assert [s["index"] for s in slides] == [1, 2]                         # sorted, "2" -> 2
    assert slides[0]["preset"] == "body"                                   # unknown preset -> body
    assert slides[0]["alt_media_urls"] == ["https://x/b"]
    assert slides[1]["media_urls"] == ["https://x/v"] and slides[1]["headline"] == []


def test_server_knows_when_its_code_is_outdated(monkeypatch):
    import app as A
    c = A.app.test_client()
    assert c.get("/api/status").json["outdated"] is False
    monkeypatch.setattr(A, "STARTED_STAMP", "older-code")          # files changed after start
    assert c.get("/api/status").json["outdated"] is True


def test_illustrator_text_is_one_object_per_block(slides):
    import export_ai
    import project as P
    built = [P.new_slide(s) for s in P.expand_alternatives(slides)]
    title = export_ai.text_blocks(built[0])
    assert [b["lines"] for b in title] == [                              # 2 objects, not 5
        ["SUMMER EDITION", "& HOUSE RIESLING", "01.06.2030 | 18:00 UHR"], ["LIVE", "TASTING"]]
    assert title[1]["font"] == "TrajanPro3-Regular" and title[1]["tracking"] == 50
    body = export_ai.text_blocks(built[3])
    assert len(body) == 1 and len(body[0]["lines"]) == 4                 # one body object
    assert body[0]["leading"] == round(36 * 1.2, 2) and body[0]["tracking"] == 300
    lst = export_ai.text_blocks(built[-1])                                # list slide
    assert len(lst) == 1 and "" in lst[0]["lines"]                       # spacer lines kept
