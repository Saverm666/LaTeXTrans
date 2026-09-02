import os
import tempfile
import unittest

from src.formats.latex.bilingual import (
    ensure_original_pdf,
    interleaved_page_order,
    pair_pdf_pages,
    spread_dimensions,
    variant_filename,
)


class PairPdfPagesTest(unittest.TestCase):
    def test_pairs_equal_length(self):
        self.assertEqual(pair_pdf_pages(2, 2), [(1, 1), (2, 2)])

    def test_pads_shorter_side_with_none(self):
        self.assertEqual(
            pair_pdf_pages(2, 4),
            [(1, 1), (2, 2), (None, 3), (None, 4)],
        )
        self.assertEqual(
            pair_pdf_pages(3, 1),
            [(1, 1), (2, None), (3, None)],
        )

    def test_empty_when_both_zero(self):
        self.assertEqual(pair_pdf_pages(0, 0), [])


class InterleavedPageOrderTest(unittest.TestCase):
    def test_original_then_translation_per_page(self):
        self.assertEqual(
            interleaved_page_order(2, 2),
            [('orig', 1), ('trans', 1), ('orig', 2), ('trans', 2)],
        )

    def test_keeps_leftover_pages_from_longer_side(self):
        self.assertEqual(
            interleaved_page_order(1, 3),
            [('orig', 1), ('trans', 1), ('trans', 2), ('trans', 3)],
        )
        self.assertEqual(
            interleaved_page_order(3, 1),
            [('orig', 1), ('trans', 1), ('orig', 2), ('orig', 3)],
        )


class VariantFilenameTest(unittest.TestCase):
    def test_uses_mono_bilingual_sidebyside_suffixes(self):
        self.assertEqual(variant_filename('ch', '2601.19597', 'mono'), 'ch_2601.19597_mono.pdf')
        self.assertEqual(variant_filename('ch', '2601.19597', 'bilingual'), 'ch_2601.19597_bilingual.pdf')
        self.assertEqual(variant_filename('ch', '2601.19597', 'sidebyside'), 'ch_2601.19597_sidebyside.pdf')


class SpreadDimensionsTest(unittest.TestCase):
    def test_places_original_left_and_translation_right(self):
        width, height, left_scale, right_scale, left_width = spread_dimensions(
            (100, 200),
            (150, 200),
        )
        self.assertEqual(height, 200)
        self.assertEqual(left_scale, 1)
        self.assertEqual(right_scale, 1)
        self.assertEqual(left_width, 100)
        self.assertEqual(width, 250)

    def test_scales_to_shared_height(self):
        width, height, left_scale, right_scale, left_width = spread_dimensions(
            (100, 100),
            (100, 200),
        )
        self.assertEqual(height, 200)
        self.assertEqual(left_scale, 2)
        self.assertEqual(right_scale, 1)
        self.assertEqual(left_width, 200)
        self.assertEqual(width, 300)

    def test_keeps_empty_side_as_same_size_slot(self):
        width, height, left_scale, right_scale, left_width = spread_dimensions((100, 200), None)
        self.assertEqual(height, 200)
        self.assertEqual(width, 200)
        self.assertEqual(left_width, 100)
        self.assertEqual(right_scale, 1)


class EnsureOriginalPdfTest(unittest.TestCase):
    def test_returns_existing_preferred_pdf_without_download_or_compile(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = os.path.join(tmp, 'paper')
            os.makedirs(project)
            existing = os.path.join(project, 'paper.pdf')
            with open(existing, 'wb') as handle:
                handle.write(b'%PDF-1.4')
            calls = []

            def download_original():
                calls.append('download')
                return None

            def compile_original():
                calls.append('compile')
                return None

            self.assertEqual(
                ensure_original_pdf(
                    project,
                    download_original=download_original,
                    compile_original=compile_original,
                ),
                existing,
            )
            self.assertEqual(calls, [])

    def test_downloads_arxiv_pdf_before_compiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = os.path.join(tmp, '2601.19597')
            os.makedirs(project)
            downloaded = os.path.join(tmp, 'arxiv.pdf')
            with open(downloaded, 'wb') as handle:
                handle.write(b'%PDF-1.4 arxiv')
            calls = []

            def download_original():
                calls.append('download')
                return downloaded

            def compile_original():
                calls.append('compile')
                return None

            result = ensure_original_pdf(
                project,
                download_original=download_original,
                compile_original=compile_original,
            )
            dest = os.path.join(project, '2601.19597.pdf')
            self.assertEqual(result, dest)
            self.assertEqual(calls, ['download'])
            with open(dest, 'rb') as handle:
                self.assertEqual(handle.read(), b'%PDF-1.4 arxiv')

    def test_compiles_when_arxiv_download_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = os.path.join(tmp, 'paper')
            os.makedirs(project)
            compiled = os.path.join(tmp, 'build', 'main.pdf')
            os.makedirs(os.path.dirname(compiled))
            with open(compiled, 'wb') as handle:
                handle.write(b'%PDF-1.4 original')
            calls = []

            def download_original():
                calls.append('download')
                return None

            def compile_original():
                calls.append('compile')
                return compiled

            result = ensure_original_pdf(
                project,
                download_original=download_original,
                compile_original=compile_original,
            )
            self.assertEqual(result, os.path.join(project, 'paper.pdf'))
            self.assertEqual(calls, ['download', 'compile'])

    def test_uses_other_local_pdf_if_download_fails_before_compile(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = os.path.join(tmp, 'paper')
            os.makedirs(project)
            other = os.path.join(project, 'ms.pdf')
            with open(other, 'wb') as handle:
                handle.write(b'%PDF-1.4 local')
            calls = []

            def download_original():
                calls.append('download')
                return None

            def compile_original():
                calls.append('compile')
                return None

            self.assertEqual(
                ensure_original_pdf(
                    project,
                    download_original=download_original,
                    compile_original=compile_original,
                ),
                other,
            )
            self.assertEqual(calls, ['download'])

    def test_returns_none_when_compile_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = os.path.join(tmp, 'paper')
            os.makedirs(project)
            self.assertIsNone(ensure_original_pdf(project, compile_original=lambda: None))
