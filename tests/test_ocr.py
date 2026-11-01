"""Scanned PDFs: pages without a text layer go to local OCR (the ocr extra). OCR itself is mocked here."""
import io
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("CUELINE_DATA", tempfile.mkdtemp(prefix="cueline-test-"))

from cueline import sources  # noqa: E402


def blank_pdf(pages: int) -> bytes:  # pages with no text layer, like a scan
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def fake_ocr(_data, pages):
    return {n: f"Text read from page {n}." for n in pages}


class ScannedPdf(unittest.TestCase):
    def test_scanned_pages_are_read_with_page_markers(self):
        with mock.patch.object(sources, "_ocr_ready", return_value=True), \
                mock.patch.object(sources, "_ocr_pages", side_effect=fake_ocr):
            r = sources.from_file("scan.pdf", blank_pdf(2))
        self.assertEqual(r["content"], "[p.1]\nText read from page 1.\n\n[p.2]\nText read from page 2.")
        self.assertEqual(r["meta"]["pages"], 2)

    def test_without_the_add_on_the_error_says_how_to_install_it(self):
        with mock.patch.object(sources, "_ocr_ready", return_value=False):
            with self.assertRaisesRegex(ValueError, r"cueline\[ocr\]"):
                sources.from_file("scan.pdf", blank_pdf(1))

    def test_long_scans_stop_at_the_cap_and_say_so(self):
        with mock.patch.object(sources, "_ocr_ready", return_value=True), \
                mock.patch.object(sources, "_ocr_pages", side_effect=fake_ocr) as ocr, \
                mock.patch.object(sources, "OCR_MAX_PAGES", 2):
            r = sources.from_file("long.pdf", blank_pdf(5))
        self.assertEqual(ocr.call_args.args[1], [1, 2])
        self.assertIn("[p.3]\n[Only the first 2 scanned pages were read.", r["content"])


@unittest.skipUnless(sources._ocr_ready(), "needs the ocr extra")
class RealOcr(unittest.TestCase):
    def test_reads_a_picture_of_text(self):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (1240, 600), "white")
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 40)
        except OSError:
            font = ImageFont.load_default(size=40)
        ImageDraw.Draw(img).text((100, 250), "Charge per outcome, not per hour.", fill="black", font=font)
        buf = io.BytesIO()
        img.save(buf, format="PDF", resolution=150)
        content = sources.from_file("scan.pdf", buf.getvalue())["content"]
        self.assertTrue(content.startswith("[p.1]"))
        self.assertIn("outcome", content.lower())
