"""A temporary run cannot adopt or destroy an existing deployment."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import lab
import test_cycle
import remote
import verify


class TestCycleOwnership(unittest.TestCase):
    def test_nonempty_state_and_pending_operation_are_rejected_without_changes(self):
        for pending in ('state', 'baseline', 'lock'):
            with self.subTest(pending=pending), tempfile.TemporaryDirectory(dir=ROOT / '.local') as directory:
                root = Path(directory)
                local = root / '.local'
                local.mkdir()
                if pending == 'state':
                    lab.write_json(root/'terraform-selectel/terraform.tfstate', {'outputs': {'lab': {'value': {}}}})
                elif pending == 'baseline':
                    (local/'reconcile-baseline.json').write_text('{}')
                else:
                    (root/'.test-cycle.lock').write_text('another-run')
                with patch.multiple(lab, ROOT=root, LOCAL=local, DEPLOYMENT=local/'deployment.json',
                                    state_resources=lambda: [], load_config=Mock(), tf=Mock(), destroy=Mock()):
                    with self.assertRaises(ValueError):
                        test_cycle.run()
                    lab.load_config.assert_not_called()
                    lab.tf.assert_not_called()
                    lab.destroy.assert_not_called()

    def test_cleanup_failure_keeps_lock_and_recovery_context(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.local') as directory:
            root = Path(directory)
            local = root / '.local'
            local.mkdir()
            context_path = local/'deployment.json'
            config = json.loads((ROOT/'config/lab.example.json').read_text())
            def deploy(test_run_id=None):
                lab.write_json(context_path, {'test_run_id': test_run_id})
                raise RuntimeError('setup interrupted')
            with patch.multiple(lab, ROOT=root, LOCAL=local, CONFIG=local/'lab-config.json',
                                DEPLOYMENT=context_path, load_config=lambda: copy.deepcopy(config),
                                state_resources=lambda: [], tf=Mock(), deploy=deploy), \
                 patch.object(lab, 'destroy', side_effect=RuntimeError('cloud audit incomplete')):
                with self.assertRaisesRegex(RuntimeError, 'audit incomplete'):
                    test_cycle.run()
            self.assertTrue(context_path.exists())
            self.assertTrue((root/'.test-cycle.lock').exists())
            report = lab.read_json(root/'docs/cycle-latest.json')
            self.assertFalse(report['success'] or report['cleanup_success'])

    def test_full_temporary_workflow_checks_data_and_removes_its_own_stand(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.local') as directory:
            root = Path(directory)
            local = root / '.local'
            local.mkdir()
            config_path = local / 'lab-config.json'
            context_path = local / 'deployment.json'
            config = json.loads((ROOT/'config/lab.example.json').read_text())
            lab.write_json(config_path, config)
            calls = []
            inventory = {'server_ids': {n: 'existing-'+n for n in config['nodes']}}
            def deploy(test_run_id=None):
                calls.append(test_run_id)
                lab.write_json(context_path, {'test_run_id': test_run_id})
                desired = lab.read_json(config_path)
                for n in desired['nodes']:
                    inventory['server_ids'].setdefault(n, 'new-'+n)
                lab.write_json(root/'docs/expansion-latest.json', {'success': True, 'member_count': len(desired['nodes'])-1})
            def destroy(expected_run_id=None):
                self.assertEqual(expected_run_id, calls[0])
                context_path.unlink()
            with patch.multiple(lab, ROOT=root, LOCAL=local, CONFIG=config_path, DEPLOYMENT=context_path,
                                load_config=lambda: copy.deepcopy(config), state_resources=lambda: [],
                                tf=Mock(), deploy=deploy, destroy=destroy, saved_lab=lambda: copy.deepcopy(inventory)), \
                 patch.object(remote, 'connect', return_value=Mock()), patch.object(remote, 'close'), \
                 patch.object(remote, 'capture_data', return_value={'data_and_media': 'unchanged'}), \
                 patch.object(verify, 'verify') as failure_checks, patch.object(verify, 'manage') as control, \
                 patch.object(verify, 'verify_cycle_control', return_value={'article_read': True, 'media_sha256': 'same'}) as http_control:
                test_cycle.run()
            self.assertEqual(len(calls), 4)
            self.assertEqual(len(set(calls)), 1)
            failure_checks.assert_called_once()
            self.assertEqual(http_control.call_count, 4)
            self.assertTrue(control.call_args.args[1].startswith('create_cycle_control '))
            self.assertEqual(lab.read_json(config_path), config)
            report = lab.read_json(root/'docs/cycle-latest.json')
            self.assertTrue(report['success'] and report['cleanup_success'])
            self.assertEqual(len(report['steps']), 6)
            self.assertFalse((root/'.test-cycle.lock').exists())

    def test_existing_stand_is_rejected_before_init_or_deploy_or_destroy(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.local') as directory:
            root = Path(directory)
            local = root / '.local'
            local.mkdir()
            (local / 'deployment.json').write_text('{}')
            with patch.multiple(lab, ROOT=root, LOCAL=local, DEPLOYMENT=local/'deployment.json',
                                state_resources=lambda: [], load_config=Mock(), tf=Mock(), deploy=Mock(), destroy=Mock()):
                with self.assertRaises(ValueError):
                    test_cycle.run()
                lab.load_config.assert_not_called()
                lab.tf.assert_not_called()
                lab.deploy.assert_not_called()
                lab.destroy.assert_not_called()

    def test_failure_destroys_only_context_with_matching_run_id(self):
        for own in (True, False):
            with self.subTest(own=own), tempfile.TemporaryDirectory(dir=ROOT / '.local') as directory:
                root = Path(directory)
                local = root / '.local'
                local.mkdir()
                context_path = local / 'deployment.json'
                config = json.loads((ROOT/'config/lab.example.json').read_text())
                def deploy(test_run_id=None):
                    lab.write_json(context_path, {'test_run_id': test_run_id if own else 'another-run'})
                    raise RuntimeError('first configuration failed')
                with patch.multiple(lab, ROOT=root, LOCAL=local, CONFIG=local/'lab-config.json',
                                    DEPLOYMENT=context_path, state_resources=lambda: [], load_config=lambda: copy.deepcopy(config),
                                    tf=Mock(), deploy=deploy), patch.object(lab, 'destroy') as destroy:
                    with self.assertRaises((ValueError, RuntimeError)):
                        test_cycle.run()
                    if own:
                        self.assertEqual(destroy.call_count, 1)
                        self.assertIn('expected_run_id', destroy.call_args.kwargs)
                    else:
                        destroy.assert_not_called()
