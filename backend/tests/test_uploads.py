from __future__ import annotations

import asyncio
import unittest
from io import BytesIO

import fitz
from fastapi import HTTPException, UploadFile
from PIL import Image

from backend.app import _read_document_upload


def image_bytes(format_name: str = "PNG") -> bytes:
    output = BytesIO()
    Image.new("RGB", (300, 450), "white").save(output, format=format_name)
    return output.getvalue()


def jpeg_with_software_tag() -> bytes:
    output = BytesIO()
    exif = Image.Exif()
    exif[0x0131] = "Adobe Photoshop test marker"
    Image.new("RGB", (300, 450), "white").save(output, format="JPEG", exif=exif)
    return output.getvalue()


def webp_with_software_tag() -> bytes:
    output = BytesIO()
    exif = Image.Exif()
    exif[0x0131] = "Adobe Photoshop WebP marker"
    Image.new("RGB", (300, 450), "white").save(
        output, format="WEBP", lossless=True, exif=exif
    )
    return output.getvalue()


class DocumentUploadTests(unittest.TestCase):
    def read_upload(self, filename: str, payload: bytes) -> tuple[str, bytes]:
        upload = UploadFile(filename=filename, file=BytesIO(payload))
        return asyncio.run(_read_document_upload(upload))

    def test_png_is_normalized_to_a_one_page_pdf(self) -> None:
        name, payload = self.read_upload("scan.png", image_bytes())

        self.assertEqual(name, "scan.png")
        self.assertTrue(payload.startswith(b"%PDF-"))
        with fitz.open(stream=payload, filetype="pdf") as document:
            self.assertEqual(len(document), 1)
            self.assertAlmostEqual(document[0].rect.width, 72.0)
            self.assertAlmostEqual(document[0].rect.height, 108.0)

    def test_jpeg_is_accepted_case_insensitively(self) -> None:
        name, payload = self.read_upload("PHONE_SCAN.JPEG", image_bytes("JPEG"))

        self.assertEqual(name, "PHONE_SCAN.JPEG")
        self.assertTrue(payload.startswith(b"%PDF-"))

    def test_all_advertised_image_formats_are_normalized(self) -> None:
        formats = {
            "scan.jpg": "JPEG",
            "scan.png": "PNG",
            "scan.webp": "WEBP",
            "scan.tiff": "TIFF",
        }

        for filename, format_name in formats.items():
            with self.subTest(filename=filename):
                _, payload = self.read_upload(filename, image_bytes(format_name))
                self.assertTrue(payload.startswith(b"%PDF-"))

    def test_embedded_image_keeps_forensic_software_metadata(self) -> None:
        _, payload = self.read_upload("edited.jpg", jpeg_with_software_tag())

        with fitz.open(stream=payload, filetype="pdf") as document:
            image_xref = document.get_page_images(0, full=True)[0][0]
            embedded = document.extract_image(image_xref)["image"]

        self.assertIn(b"Adobe Photoshop test marker", embedded)

    def test_webp_forensic_software_metadata_is_carried_into_pdf(self) -> None:
        _, payload = self.read_upload("edited.webp", webp_with_software_tag())

        with fitz.open(stream=payload, filetype="pdf") as document:
            producer = document.metadata.get("producer", "")

        self.assertIn("Adobe Photoshop WebP marker", producer)

    def test_pdf_payload_is_not_rewritten(self) -> None:
        original = b"%PDF-1.7\nunchanged"

        name, payload = self.read_upload("document.pdf", original)

        self.assertEqual(name, "document.pdf")
        self.assertEqual(payload, original)

    def test_corrupt_image_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            self.read_upload("broken.png", b"not an image")

        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("not a valid image", str(raised.exception.detail))

    def test_unsupported_extension_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            self.read_upload("scan.bmp", image_bytes())

        self.assertEqual(raised.exception.status_code, 415)


if __name__ == "__main__":
    unittest.main()
