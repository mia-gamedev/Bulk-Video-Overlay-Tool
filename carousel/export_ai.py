"""Illustrator export.

A native multi-artboard .ai can only be written by Illustrator itself, so the export is two files:

    <name>_slides.pdf                        every slide as a vector page:
        background   full-resolution photo, clipped to the 1080x1440 page
        shade        gradient objects with their blend mode (Multiply / Normal)
        logo         placed image
        text         one live text object per line in NeutraText-Book / TrajanPro3
    Make <name>.ai (run in Illustrator).jsx  File > Scripts > Other Script... in Illustrator:
                                             one artboard per slide, each page pasted in place,
                                             saved as a native <name>.ai next to the script

Video slides use their first frame as the background image.
"""
import functools
from pathlib import Path

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

import layout as L
import render as R

W, H = R.W, R.H
FONT_NAMES = {"neutra": "NeutraText-Book", "trajan": "TrajanPro3-Regular"}


@functools.lru_cache(maxsize=None)
def _font(key):
    name = FONT_NAMES[key]
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, L.font_path(key)))
    return name


def _image_source(path):
    """Pass JPEGs through untouched (no recompression) unless they need EXIF rotation."""
    path = Path(path)
    if path.suffix.lower() in (".jpg", ".jpeg"):
        with Image.open(path) as im:
            if im.getexif().get(0x0112, 1) == 1:
                return str(path)
    return ImageReader(R.load_source(path))


def _draw_background(c, slide, mdir):
    src = R.source_still(slide, mdir)
    if src is None:
        return
    im = R.load_source(src)
    sw, sh, x, y = R.crop_params(im.width, im.height, slide["crop"])
    c.saveState()
    clip = c.beginPath()
    clip.rect(0, 0, W, H)
    c.clipPath(clip, stroke=0, fill=0)
    # canvas coords are top-left based, PDF is bottom-left
    c.drawImage(_image_source(src), -x, H + y - sh, sw, sh)
    c.restoreState()


def _draw_shade(c, slide, els):
    """Every shade layer (text shade, logo gradient) as its own object with its blend mode."""
    for sh, mask in R.shade_layers(slide, els):
        img = Image.new("RGBA", (W, H), R._hex_rgb(sh["color"]) + (0,))
        img.putalpha(mask)
        c.saveState()
        c.setBlendMode("Multiply" if sh["mode"] == "multiply" else "Normal")
        c.drawImage(ImageReader(img), 0, 0, W, H, mask="auto")
        c.restoreState()


def _draw_logo(c, els):
    """Logo and imported-preset graphics, placed from their full-resolution files."""
    for e in els:
        src = R.LOGO if e["id"] == "logo" else e.get("hires") if e["id"] == "graphic" else None
        if src and Path(src).exists():
            w, h = e["img"].size
            c.drawImage(ImageReader(Image.open(src).convert("RGBA")), e["x"], H - e["y"] - h,
                        w, h, mask="auto")


def _draw_text(c, slide):
    c.setFillColorRGB(*(v / 255 for v in L.CFG["color"]))
    for r in R.text_runs(slide):
        name = _font(r["font"])
        cs = r["tracking"] * r["size"]
        # re-align with the PDF font metrics so right/centre edges land exactly
        w = pdfmetrics.stringWidth(r["text"], name, r["size"]) + cs * (len(r["text"]) - 1)
        x = {"left": r["ax"], "center": r["ax"] - w / 2, "right": r["ax"] - w}[r["align"]]
        t = c.beginText()
        t.setTextOrigin(x, H - r["baseline"])
        t.setFont(name, r["size"])
        t.setCharSpace(cs)
        t.textOut(r["text"])
        c.drawText(t)


def draw_slide(c, slide, mdir):
    c.setPageSize((W, H))
    els = R.elements(slide)
    _draw_background(c, slide, mdir)
    _draw_shade(c, slide, els)
    _draw_logo(c, els)
    _draw_text(c, slide)
    c.showPage()


def _new_canvas(path, title):
    # start on a real font so no unused Helvetica reference ends up in the file
    c = canvas.Canvas(str(path), pagesize=(W, H), pageCompression=1,
                      initialFontName=_font("neutra"), initialFontSize=36)
    c.setTitle(title)
    c.setCreator("Carousel Builder")
    return c


JSX_TEMPLATE = r"""// Builds a native Illustrator file from the Carousel Builder export.
// Illustrator: File > Scripts > Other Script... and choose this file.
// Keep it next to %(pdf)s. The fonts used on the slides must be installed.
#target illustrator
(function () {
    var PDF = %(pdf_js)s, OUT = %(ai_js)s, NAMES = %(names_js)s;
    var W = 1080, H = 1440, GAP = 100;
    var here = File($.fileName).parent;
    var src = File(here.fsName + "/" + PDF), out = File(here.fsName + "/" + OUT);
    if (!src.exists) { alert("Can't find " + PDF + " next to this script."); return; }
    var level = app.userInteractionLevel;
    app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
    var pageWas = app.preferences.PDFFileOptions.pageToOpen;
    try {
        var doc = app.documents.add(DocumentColorSpace.RGB, W, H, NAMES.length,
                                    DocumentArtboardLayout.GridByRow, GAP, Math.min(NAMES.length, 6));
        for (var i = 0; i < NAMES.length; i++) {
            doc.artboards[i].name = NAMES[i];
            app.preferences.PDFFileOptions.pageToOpen = i + 1;
            var page = app.open(src);
            app.executeMenuCommand("selectall");
            app.copy();
            page.close(SaveOptions.DONOTSAVECHANGES);
            app.activeDocument = doc;
            doc.artboards.setActiveArtboardIndex(i);
            app.executeMenuCommand("deselectall");
            app.executeMenuCommand("pasteInPlace");     // same position, relative to the artboard
        }
        doc.artboards.setActiveArtboardIndex(0);
        var opts = new IllustratorSaveOptions();
        opts.pdfCompatible = true;
        opts.embedICCProfile = true;
        doc.saveAs(out, opts);
    } finally {
        app.preferences.PDFFileOptions.pageToOpen = pageWas;
        app.userInteractionLevel = level;
    }
    alert("Saved " + NAMES.length + " artboards to\n" + out.fsName);
})();
"""


def export_ai(slides, mdir, out_dir, name="carousel"):
    """Write <name>_slides.pdf (one vector page per slide) and the Illustrator script that
    turns it into a native multi-artboard <name>.ai. Returns the written file names."""
    import json
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = f"{name}_slides.pdf"
    c = _new_canvas(out_dir / pdf, name)
    for s in slides:
        draw_slide(c, s, mdir)
    c.save()
    jsx = f"Make {name}.ai (run in Illustrator).jsx"
    names = [R.slide_stem(s) for s in slides]
    # ExtendScript reads .jsx as UTF-8 with a BOM; json.dumps gives valid JS string literals
    (out_dir / jsx).write_text(JSX_TEMPLATE % {
        "pdf": pdf, "pdf_js": json.dumps(pdf), "ai_js": json.dumps(f"{name}.ai"),
        "names_js": json.dumps(names)}, encoding="utf-8-sig")
    return [pdf, jsx]
