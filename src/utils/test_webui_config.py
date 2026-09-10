import tempfile
import unittest
from pathlib import Path

import toml

from src.utils.webui_config import merge_ui_into_config, save_config_file


class MergeUiIntoConfigTest(unittest.TestCase):
    def test_writes_llm_and_language_fields(self):
        config = {
            'sys_name': 'LaTeXTrans',
            'version': '0.1.14',
            'tex_sources_dir': 'tex source',
            'output_dir': 'outputs',
            'paper_list': [],
            'llm_config': {'model': 'old', 'api_key': 'old-key'},
        }
        ui = {
            'source_language': 'en',
            'target_language': 'ch',
            'model': 'gpt-5.6-luna',
            'url': 'http://example/v1',
            'key': 'secret',
            'repair_model': 'fix',
            'repair_url': 'http://repair',
            'repair_key': 'rk',
            'mode': 1,
            'update_term': 'True',
            'user_term': 'GAN=生成对抗网络',
        }
        merged = merge_ui_into_config(config, ui)
        self.assertEqual(merged['tex_sources_dir'], 'tex source')
        self.assertEqual(merged['source_language'], 'en')
        self.assertEqual(merged['target_language'], 'ch')
        self.assertEqual(merged['mode'], 1)
        self.assertEqual(merged['llm_config']['model'], 'gpt-5.6-luna')
        self.assertEqual(merged['llm_config']['base_url'], 'http://example/v1')
        self.assertEqual(merged['llm_config']['api_key'], 'secret')
        self.assertEqual(merged['llm_config']['repair_model'], 'fix')
        self.assertEqual(merged['llm_config']['repair_base_url'], 'http://repair')
        self.assertEqual(merged['llm_config']['repair_api_key'], 'rk')


class SaveConfigFileTest(unittest.TestCase):
    def test_round_trips_api_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'default.toml')
            config = merge_ui_into_config(
                {'sys_name': 'LaTeXTrans', 'llm_config': {}},
                {
                    'source_language': 'en',
                    'target_language': 'ch',
                    'model': 'm',
                    'url': 'http://u',
                    'key': 'k',
                    'repair_model': '',
                    'repair_url': '',
                    'repair_key': '',
                    'mode': 0,
                    'update_term': 'False',
                    'user_term': '',
                },
            )
            save_config_file(path, config)
            loaded = toml.load(path)
            self.assertEqual(loaded['llm_config']['base_url'], 'http://u')
            self.assertEqual(loaded['llm_config']['api_key'], 'k')
            self.assertEqual(loaded['llm_config']['model'], 'm')
