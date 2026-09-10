import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.utils.export_path import (
    copy_pdf_to_path,
    decode_picker_bytes,
    default_export_directory,
    load_directory_from_picker_output,
    load_persisted_export_directory,
    normalize_user_directory,
    parse_picked_directory,
    persist_export_directory,
    resolve_save_path,
    sanitize_download_filename,
)


class SanitizeDownloadFilenameTest(unittest.TestCase):
    def test_strips_directories_and_ensures_pdf(self):
        self.assertEqual(sanitize_download_filename('../evil/paper'), 'paper.pdf')
        self.assertEqual(sanitize_download_filename('my paper.PDF'), 'my paper.PDF')
        self.assertEqual(sanitize_download_filename('ch_paper_mono.pdf'), 'ch_paper_mono.pdf')


class ResolveSavePathTest(unittest.TestCase):
    def test_joins_directory_and_safe_name(self):
        path = resolve_save_path('/tmp/out', 'note')
        self.assertEqual(Path(path).name, 'note.pdf')
        self.assertEqual(Path(path).parent, Path('/tmp/out'))

    def test_rejects_empty_directory(self):
        with self.assertRaises(ValueError):
            resolve_save_path('  ', 'a.pdf')

    def test_copies_pdf_into_selected_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.pdf'
            source.write_bytes(b'%PDF-test')

            result = copy_pdf_to_path(str(source), str(root / 'chosen'), 'saved paper')

            self.assertEqual(result, root / 'chosen' / 'saved paper.pdf')
            self.assertEqual(result.read_bytes(), b'%PDF-test')


class NormalizeUserDirectoryTest(unittest.TestCase):
    def test_converts_windows_drive_path_to_wsl(self):
        converted = normalize_user_directory(r'C:\Users\123\Desktop')
        self.assertEqual(converted, Path('/mnt/c/Users/123/Desktop'))


class DefaultExportDirectoryTest(unittest.TestCase):
    def test_prefers_windows_downloads_folder(self):
        with TemporaryDirectory() as tmp:
            users = Path(tmp) / 'Users'
            downloads = users / '123' / 'Downloads'
            downloads.mkdir(parents=True)
            (users / 'Public').mkdir()
            self.assertEqual(
                default_export_directory('/should-not-use', users_root=str(users)),
                str(downloads),
            )

    def test_falls_back_when_downloads_missing(self):
        with TemporaryDirectory() as tmp:
            users = Path(tmp) / 'Users'
            users.mkdir()
            self.assertEqual(
                default_export_directory('/tmp/fallback', users_root=str(users)),
                '/tmp/fallback',
            )


class ParsePickedDirectoryTest(unittest.TestCase):
    def test_converts_windows_picker_path(self):
        self.assertEqual(
            parse_picked_directory('C:\\Users\\123\\Downloads\\papers\n'),
            '/mnt/c/Users/123/Downloads/papers',
        )

    def test_returns_none_when_cancelled(self):
        self.assertIsNone(parse_picked_directory(''))
        self.assertIsNone(parse_picked_directory(None))

    def test_keeps_chinese_directory_names(self):
        self.assertEqual(
            parse_picked_directory('C:\\Users\\123\\Downloads\\论文翻译\n'),
            '/mnt/c/Users/123/Downloads/论文翻译',
        )


class DecodePickerBytesTest(unittest.TestCase):
    def test_decodes_utf8_chinese_path(self):
        raw = 'C:\\Users\\123\\下载\n'.encode('utf-8')
        self.assertEqual(decode_picker_bytes(raw), 'C:\\Users\\123\\下载\n')

    def test_decodes_gbk_chinese_path(self):
        raw = 'C:\\Users\\123\\下载\n'.encode('gbk')
        self.assertEqual(decode_picker_bytes(raw), 'C:\\Users\\123\\下载\n')

    def test_decodes_utf16_le_chinese_path(self):
        raw = 'C:\\Users\\123\\下载\n'.encode('utf-16')
        self.assertEqual(decode_picker_bytes(raw).strip(), 'C:\\Users\\123\\下载')


class LoadPickerResultTest(unittest.TestCase):
    def test_reads_utf8_sidecar_file_with_chinese_path(self):
        with TemporaryDirectory() as tmp:
            sidecar = Path(tmp) / 'picked.txt'
            sidecar.write_text('C:\\Users\\123\\文档\\论文', encoding='utf-8')
            result = load_directory_from_picker_output(str(sidecar).encode('utf-8'))
            self.assertEqual(result, '/mnt/c/Users/123/文档/论文')


class PersistExportDirectoryTest(unittest.TestCase):
    def test_round_trips_chinese_windows_path(self):
        with TemporaryDirectory() as tmp:
            state_file = str(Path(tmp) / 'export_dir')
            saved = persist_export_directory(
                r'C:\Users\123\文档\论文',
                state_file=state_file,
            )
            self.assertEqual(saved, '/mnt/c/Users/123/文档/论文')
            self.assertEqual(
                load_persisted_export_directory(state_file=state_file),
                '/mnt/c/Users/123/文档/论文',
            )

    def test_returns_none_when_state_file_missing(self):
        with TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / 'missing')
            self.assertIsNone(load_persisted_export_directory(state_file=missing))
