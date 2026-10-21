"""Repeat deploy, add db4, fail configuration, and resume on a fully isolated stand."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import growth
import lab
import remote
import verify


class GrowthWorkflow(unittest.TestCase):
    def test_add_resume_and_repeat_preserve_existing_stand(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.local') as directory:
            root = Path(directory)
            local = root / '.local'
            local.mkdir()
            (root / 'docs').mkdir()
            for name in ('key', 'known_hosts', 'app-secrets.json'):
                (local / name).write_text('synthetic')
            previous = json.loads((ROOT / 'config/lab.example.json').read_text())
            desired = copy.deepcopy(previous)
            desired['nodes']['db4'] = dict(host=24, role='db')
            context = dict(project_id='a'*32, region='ru-9', phase='active', config=previous,
                           terraform={'nodes': previous['nodes']}, deployment_id='same-stand')
            inventory = dict(server_ids={n: 'original-'+n for n in previous['nodes']},
                             private_ips={n: '10.55.0.'+str(v['host']) for n, v in previous['nodes'].items()},
                             app_public_ip='192.0.2.1')
            original = copy.deepcopy(inventory)
            applied = False
            data = {'article-77': 'edited user content', 'media': 'uploaded image hash'}
            def plan():
                changes = []
                for n in desired['nodes']:
                    for kind, label in [('openstack_compute_instance_v2', 'node'),
                                        ('openstack_networking_port_v2', 'node'),
                                        ('openstack_blockstorage_volume_v3', 'boot')]:
                        value = dict(name='otus-dz05-'+n+('-boot' if label == 'boot' else ''), id=n+label)
                        create = n == 'db4' and not applied
                        changes.append(dict(address=f'{kind}.{label}["{n}"]', mode='managed', type=kind,
                                            change=dict(actions=['create' if create else 'no-op'],
                                                        before=None if create else value, after=value)))
                return {'resource_changes': changes}
            def runner(args, capture=False):
                nonlocal applied
                if args[0] == 'show':
                    return json.dumps(plan())
                if args[0] == 'apply':
                    applied = True
                    inventory['server_ids']['db4'] = 'new-db4'
                    inventory['private_ips']['db4'] = '10.55.0.24'
                return ''
            def save(value):
                context.clear()
                context.update(copy.deepcopy(value))
            def check_inventory(current, baseline):
                self.assertEqual(baseline['data'], data)
                for n, uid in original['server_ids'].items():
                    self.assertEqual(current['server_ids'][n], uid)
                return dict(member_count=4, django_rows_and_media_preserved=True)
            with patch.multiple(lab, ROOT=root, LOCAL=local, load_deployment=lambda: copy.deepcopy(context),
                                load_config=lambda: copy.deepcopy(desired), key_path=lambda c: local/'key',
                                cloud_config=lambda c: {}, saved_lab=lambda: copy.deepcopy(inventory),
                                save_context=save, tf=runner, collect=lambda c: {'servers': {'servers': []}}, sufficient=lambda *a, **kw: None,
                                state_resources=lambda: [],
                                publish_inventory=lambda c: copy.deepcopy(inventory)), \
                 patch.object(remote, 'assert_online'), patch.object(remote, 'capture_data', return_value=data), \
                 patch.object(remote, 'configure', side_effect=[RuntimeError('configuration interrupted'), None, None]) as configure, \
                 patch.object(verify, 'verify_reconcile', side_effect=check_inventory), \
                 patch.object(lab, 'destroy') as destroy:
                with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                    growth.reconcile()
                self.assertEqual(context['phase'], 'updating')
                self.assertTrue((local/'reconcile-baseline.json').exists())
                growth.reconcile()  # Retry uses no-op Terraform resources from partial success.
                self.assertEqual(context['phase'], 'active')
                self.assertFalse((local/'reconcile-baseline.json').exists())
                growth.reconcile()  # Repeat on the unchanged expanded stand.
                self.assertEqual(configure.call_args.kwargs['configured_nodes'], sorted(desired['nodes']))
                destroy.assert_not_called()
            report = json.loads((root/'docs/expansion-latest.json').read_text())
            self.assertTrue(report['success'])
            self.assertEqual(report['added_nodes'], [])  # Last run was a pure reapplication.
