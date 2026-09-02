import json
import os
import tempfile
import unittest
from pathlib import Path

from src.formats.latex.checkpoint import (
    MAP_FILES,
    STAGES,
    config_fingerprint,
    latextrans_dir,
    read_checkpoint,
    resolve_resume_stage,
    restore_snapshot,
    save_checkpoint,
    save_snapshot,
    snapshot_dir,
    write_checkpoint,
)


class ResolveResumeStageTest(unittest.TestCase):
    def test_starts_at_parse_when_no_checkpoint(self):
        self.assertEqual(resolve_resume_stage(None, 'abc'), 'parse')

    def test_fresh_always_starts_at_parse(self):
        checkpoint = {'stage': 'translate', 'status': 'completed', 'config_fingerprint': 'abc'}
        self.assertEqual(resolve_resume_stage(checkpoint, 'abc', fresh=True), 'parse')

    def test_fingerprint_mismatch_starts_at_parse(self):
        checkpoint = {'stage': 'translate', 'status': 'completed', 'config_fingerprint': 'old'}
        self.assertEqual(resolve_resume_stage(checkpoint, 'new'), 'parse')

    def test_completed_stage_advances_to_next(self):
        checkpoint = {'stage': 'parse', 'status': 'completed', 'config_fingerprint': 'abc'}
        self.assertEqual(resolve_resume_stage(checkpoint, 'abc'), 'translate')
        checkpoint = {'stage': 'translate', 'status': 'completed', 'config_fingerprint': 'abc'}
        self.assertEqual(resolve_resume_stage(checkpoint, 'abc'), 'repair')
        checkpoint = {'stage': 'repair', 'status': 'completed', 'config_fingerprint': 'abc'}
        self.assertEqual(resolve_resume_stage(checkpoint, 'abc'), 'compile')

    def test_completed_compile_is_done(self):
        checkpoint = {'stage': 'compile', 'status': 'completed', 'config_fingerprint': 'abc'}
        self.assertIsNone(resolve_resume_stage(checkpoint, 'abc'))

    def test_running_or_failed_rewinds_to_previous_stage(self):
        checkpoint = {'stage': 'translate', 'status': 'running', 'config_fingerprint': 'abc'}
        self.assertEqual(resolve_resume_stage(checkpoint, 'abc'), 'parse')
        checkpoint = {'stage': 'repair', 'status': 'failed', 'config_fingerprint': 'abc'}
        self.assertEqual(resolve_resume_stage(checkpoint, 'abc'), 'translate')

    def test_compile_failed_retries_compile(self):
        checkpoint = {'stage': 'compile', 'status': 'failed', 'config_fingerprint': 'abc'}
        self.assertEqual(resolve_resume_stage(checkpoint, 'abc'), 'compile')
        checkpoint = {'stage': 'compile', 'status': 'running', 'config_fingerprint': 'abc'}
        self.assertEqual(resolve_resume_stage(checkpoint, 'abc'), 'compile')

    def test_parse_failure_restarts_parse(self):
        checkpoint = {'stage': 'parse', 'status': 'failed', 'config_fingerprint': 'abc'}
        self.assertEqual(resolve_resume_stage(checkpoint, 'abc'), 'parse')


class ConfigFingerprintTest(unittest.TestCase):
    def test_changes_when_model_or_language_changes(self):
        config_a = {
            'source_language': 'en',
            'target_language': 'ch',
            'mode': 0,
            'llm_config': {'model': 'a', 'repair_model': 'r'},
        }
        config_b = dict(config_a)
        config_b['llm_config'] = {'model': 'b', 'repair_model': 'r'}
        self.assertNotEqual(config_fingerprint(config_a), config_fingerprint(config_b))
        self.assertEqual(config_fingerprint(config_a), config_fingerprint(dict(config_a)))


class SnapshotRoundTripTest(unittest.TestCase):
    def test_save_and_restore_maps(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for name in MAP_FILES:
                (work / name).write_text(json.dumps({'from': 'work', 'file': name}), encoding='utf-8')
            save_snapshot(str(work), 'parse')
            for name in MAP_FILES:
                (work / name).write_text(json.dumps({'from': 'dirty'}), encoding='utf-8')
            restore_snapshot(str(work), 'parse')
            restored = json.loads((work / MAP_FILES[0]).read_text(encoding='utf-8'))
            self.assertEqual(restored['from'], 'work')
            self.assertTrue((Path(snapshot_dir(str(work), 'parse')) / MAP_FILES[0]).exists())

    def test_checkpoint_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = str(Path(tmp))
            write_checkpoint(work, 'repair', 'running', 'fp1')
            data = read_checkpoint(work)
            self.assertEqual(data['stage'], 'repair')
            self.assertEqual(data['status'], 'running')
            self.assertEqual(data['config_fingerprint'], 'fp1')
            save_checkpoint(work, {'stage': 'repair', 'status': 'completed', 'config_fingerprint': 'fp1'})
            self.assertEqual(read_checkpoint(work)['status'], 'completed')
            self.assertTrue(os.path.isdir(latextrans_dir(work)))

    def test_stages_are_coarse_four_ring(self):
        self.assertEqual(STAGES, ('parse', 'translate', 'repair', 'compile'))


if __name__ == '__main__':
    unittest.main()
