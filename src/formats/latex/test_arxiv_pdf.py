import os
import tempfile
import unittest

from src.formats.latex.arxiv_pdf import (
    arxiv_pdf_url,
    download_arxiv_pdf,
    infer_arxiv_id,
    is_pdf_bytes,
)


class ArxivPdfUrlTest(unittest.TestCase):
    def test_uses_arxiv_pdf_endpoint(self):
        self.assertEqual(
            arxiv_pdf_url('2601.19597'),
            'https://arxiv.org/pdf/2601.19597.pdf',
        )


class InferArxivIdTest(unittest.TestCase):
    def test_uses_modern_arxiv_id_folder(self):
        self.assertEqual(infer_arxiv_id('/tmp/tex/2601.19597'), '2601.19597')
        self.assertEqual(infer_arxiv_id('2601.19597v2'), '2601.19597v2')

    def test_extracts_id_from_prefixed_folder(self):
        self.assertEqual(infer_arxiv_id('arXiv-2504.06261v2'), '2504.06261v2')

    def test_returns_none_when_not_arxiv(self):
        self.assertIsNone(infer_arxiv_id('my-local-paper'))


class IsPdfBytesTest(unittest.TestCase):
    def test_accepts_pdf_magic(self):
        self.assertTrue(is_pdf_bytes(b'%PDF-1.4\n'))

    def test_rejects_html(self):
        self.assertFalse(is_pdf_bytes(b'<!DOCTYPE html>'))


class DownloadArxivPdfTest(unittest.TestCase):
    def test_writes_fetched_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, '2601.19597', '2601.19597.pdf')
            path = download_arxiv_pdf(
                '2601.19597',
                dest,
                fetch=lambda url: b'%PDF-1.5 official',
            )
            self.assertEqual(path, dest)
            with open(dest, 'rb') as handle:
                self.assertEqual(handle.read(), b'%PDF-1.5 official')

    def test_rejects_non_pdf_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, 'paper.pdf')
            self.assertIsNone(
                download_arxiv_pdf('2601.19597', dest, fetch=lambda url: b'<html>not found</html>')
            )
            self.assertFalse(os.path.exists(dest))

    def test_returns_none_when_fetch_raises(self):
        def boom(url):
            raise RuntimeError('network')

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, 'paper.pdf')
            self.assertIsNone(download_arxiv_pdf('2601.19597', dest, fetch=boom))
