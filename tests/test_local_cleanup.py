"""Local data is removed only after confirmed cloud cleanup."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import lab
(Path(__file__).resolve().parents[1] / '.local').mkdir(exist_ok=True)


class LocalCleanup(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.local')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / '.local').mkdir()
        (root / 'terraform-selectel').mkdir()
        patcher = patch.multiple(lab, ROOT=root, LOCAL=root / '.local',
                                 STATE=root / 'terraform-selectel/terraform.tfstate',
                                 DEPLOYMENT=root / '.local/deployment.json')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_preserves_config_and_credentials_and_removes_runtime_data(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.local') as directory:
            root = Path(directory)
            local = root / '.local'
            local.mkdir()
            for name in ('lab-config.json', 'operator.json', 'deployment.json', 'app-secrets.json', 'key'):
                (local / name).write_text(name)
            (local / 'auth-test-example').mkdir()
            (local / 'auth-test-example/fixture').write_text('synthetic')
            (root / 'terraform-selectel').mkdir()
            state = root / 'terraform-selectel/terraform.tfstate'
            state.write_text(json.dumps({'resources': []}))
            state.with_name(state.name + '.backup').write_text('old-state')
            with patch.multiple(lab, ROOT=root, LOCAL=local, STATE=state, state_resources=lambda: []):
                lab.cleanup_local_data()
            self.assertEqual({p.name for p in local.iterdir()}, {'lab-config.json', 'operator.json'})
            self.assertFalse(state.exists())
            self.assertFalse(state.with_name(state.name + '.backup').exists())

    def test_live_state_blocks_local_cleanup(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.local') as directory:
            root = Path(directory)
            with patch.multiple(lab, ROOT=root, LOCAL=root / '.local', STATE=root / 'state'), \
                 patch.object(lab, 'state_resources', return_value=[{}]):
                with self.assertRaises(ValueError):
                    lab.cleanup_local_data()

    def test_api_failure_preserves_local_data(self):
        context = {'project_id': 'a' * 32, 'region': 'ru-9', 'config': {'name_prefix': 'otus-dz05'}}
        with patch.object(lab, 'DEPLOYMENT') as deployment, \
             patch.object(lab, 'load_deployment', return_value=context), \
             patch.object(lab, 'tf', return_value='{}'), patch.object(lab, 'validate_plan'), \
             patch.object(lab, 'save_context'), patch.object(lab, 'state_resources', return_value=[]), \
             patch.object(lab, 'cloud_config'), patch.object(lab, 'authenticate', side_effect=RuntimeError('API unavailable')), \
             patch.object(lab, 'cleanup_local_data', create=True) as cleanup:
            deployment.exists.return_value = True
            with self.assertRaises(RuntimeError):
                lab.destroy()
            cleanup.assert_not_called()

    def test_destroy_cleans_only_when_api_inventory_is_empty(self):
        for count in (0, 1):
            with self.subTest(remaining=count):
                context = {'project_id': 'a' * 32, 'region': 'ru-9', 'config': {'name_prefix': 'otus-dz05'}}
                with patch.object(lab, 'DEPLOYMENT') as deployment, \
                     patch.object(lab, 'load_deployment', return_value=context), \
                     patch.object(lab, 'tf', return_value='{}'), patch.object(lab, 'validate_plan'), \
                     patch.object(lab, 'save_context'), patch.object(lab, 'state_resources', return_value=[]), \
                     patch.object(lab, 'cloud_config'), patch.object(lab, 'authenticate', return_value=('token', [])), \
                     patch.object(lab, 'endpoints_for', return_value={}), \
                     patch.object(lab, 'await_inventory_empty', return_value={'servers': count}), \
                     patch.object(lab, 'write_json'), patch.object(lab, 'cleanup_local_data') as cleanup:
                    deployment.exists.return_value = True
                    if count:
                        with self.assertRaises(RuntimeError):
                            lab.destroy()
                        cleanup.assert_not_called()
                    else:
                        lab.destroy()
                        cleanup.assert_called_once_with()
