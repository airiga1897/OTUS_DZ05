"""Safety checks for configuration ownership and pool fallback; no cloud calls."""
import copy
import os
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
(ROOT / '.local').mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / 'scripts'))
import settings
import lab
import placement
import remote


class ConfigurationSafety(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'config/lab.example.json').read_text())
        self.context = {'project_id': 'a' * 32, 'region': 'ru-9',
                        'availability_zone': 'ru-9a', 'config': self.config,
                        'terraform': {}, 'phase': 'active', 'deployment_id': 'first'}

    def plan(self, action='create'):
        return {'resource_changes': [
            {'address': 'node.' + node, 'mode': 'managed', 'type': 'openstack_compute_instance_v2',
             'change': {'actions': [action], 'before': {'name': 'otus-dz05-' + node},
                        'after': {'name': 'otus-dz05-' + node}}} for node in self.config['nodes']]}

    def test_foreign_resource_name_refused_on_destroy(self):
        plan = self.plan('delete')
        plan['resource_changes'][0]['change']['before']['name'] = 'otus-dz06-app'
        with self.assertRaises(ValueError):
            lab.validate_plan(plan, True, self.context)

    def test_foreign_region_or_project_refused(self):
        for field, value in [('region', 'ru-7'), ('tenant_id', 'b' * 32)]:
            plan = self.plan('delete')
            plan['resource_changes'][0]['change']['before'][field] = value
            with self.assertRaises(ValueError):
                lab.validate_plan(plan, True, self.context)

    def test_resource_replacement_refused(self):
        plan = self.plan()
        plan['resource_changes'][0]['change']['actions'] = ['delete', 'create']
        with self.assertRaises(ValueError):
            lab.validate_plan(plan, context=self.context)

    def test_incomplete_cluster_refused(self):
        plan = self.plan()
        plan['resource_changes'].pop()
        with self.assertRaises(ValueError):
            lab.validate_plan(plan, context=self.context)

    def test_config_edit_does_not_redirect_existing_deployment(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.local') as directory:
            local = Path(directory)
            settings.write_json(local / 'operator.json', {
                'auth_url': 'https://cloud.api.selcloud.ru/identity/v3', 'domain_name': 'account',
                'username': 'operator', 'password': 'test-password', 'project_id': 'b' * 32, 'region': 'ru-7'})
            settings.write_json(local / 'deployment.json', self.context)
            settings.write_json(local / 'terraform.tfstate', {'resources': []})
            changed_config = copy.deepcopy(self.config)
            changed_config.update(pools=['ru-7'])
            settings.write_json(local / 'config.json', changed_config)
            with patch.multiple(settings, LOCAL=local, CONFIG=local / 'config.json',
                                DEPLOYMENT=local / 'deployment.json', STATE=local / 'terraform.tfstate'):
                with patch.dict(os.environ, dict(OS_PROJECT_ID='a' * 32, OS_USERNAME='operator', OS_PASSWORD='test-password', OS_USER_DOMAIN_NAME='account'), clear=True):
                    auth = settings.cloud_config()
                self.assertEqual(auth['region'], 'ru-9')
                self.assertEqual(auth['project_id'], 'a' * 32)

    def test_state_without_context_refused(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.local') as directory:
            local = Path(directory)
            settings.write_json(local / 'state', {'resources': [{'mode': 'managed', 'instances': [{}]}]})
            with patch.multiple(settings, STATE=local / 'state', DEPLOYMENT=local / 'missing'):
                with self.assertRaises(ValueError):
                    settings.load_deployment()

    def test_stale_lab_inventory_refused(self):
        with patch.object(lab, 'load_deployment', return_value=self.context), \
             patch.object(lab, 'state_resources', return_value=[{}]), \
             patch.object(lab, 'read_json', return_value={'deployment_id': 'other'}):
            with self.assertRaises(ValueError):
                lab.saved_lab()

    def test_auto_pool_selection_excludes_foreign_countries(self):
        catalog = [{'type': service, 'endpoints': [
            {'interface': 'public', 'region': region, 'url': 'https://example.test'}
            for region in ('ru-9', 'ru-7', 'kz-1', 'uz-1')]} for service in placement.SERVICES]
        self.assertEqual(placement.ordered_pools(self.config, catalog), ['ru-9', 'ru-7'])

    def test_cleanup_failure_blocks_next_pool(self):
        self.run_fallback(cleanup_fails=True)

    def test_api_cleanup_waits_for_asynchronous_deletion(self):
        with patch.object(lab, 'inventory_snapshot', side_effect=[{'ports': 1}, {'ports': 0}]) as snapshot, \
             patch.object(lab, 'write_json'), patch.object(lab.time, 'sleep'):
            self.assertEqual(lab.await_inventory_empty({}, 'token', 'otus-dz05', set()), {'ports': 0})
            self.assertEqual(snapshot.call_count, 2)

    def test_api_cleanup_has_bounded_wait(self):
        with patch.object(lab, 'inventory_snapshot', return_value={'ports': 1}) as snapshot, \
             patch.object(lab, 'write_json'), patch.object(lab.time, 'sleep'):
            self.assertEqual(lab.await_inventory_empty({}, 'token', 'otus-dz05', set()), {'ports': 1})
            self.assertEqual(snapshot.call_count, 12)

    def test_scheduler_failure_cleans_before_next_pool(self):
        self.run_fallback(cleanup_fails=False)

    def run_fallback(self, cleanup_fails):
        with tempfile.TemporaryDirectory(dir=ROOT / '.local') as directory:
            local = Path(directory)
            (local / 'state').write_text('{"lineage":"test"}')
            selected = {}
            events = []
            attempts = [{'region': 'ru-9', 'availability_zone': 'ru-9a'},
                        {'region': 'ru-7', 'availability_zone': 'ru-7a'}]
            def context_for(config, choice):
                return copy.deepcopy(self.context) | choice
            def save(context):
                selected.clear()
                selected.update(copy.deepcopy(context))
            def tf(args, capture=False):
                if args[0] == 'plan':
                    events.append('plan:' + selected['region'])
                if args[:2] == ['show', '-json']:
                    return json.dumps(self.plan())
                if args[0] == 'apply' and selected['region'] == 'ru-9':
                    raise lab.TerraformError('apply', 'NoValidHost')
                if args[0] == 'output':
                    return '{}'
                return ''
            def destroy(clean_local=True):
                events.append('cleanup:' + selected['region'])
                self.assertFalse(clean_local)
                if cleanup_fails:
                    raise RuntimeError('Cleanup incomplete')
            with patch.multiple(lab, ROOT=local, LOCAL=local, STATE=local / 'state', DEPLOYMENT=local / 'missing',
                                state_resources=lambda: [], load_config=lambda: self.config,
                                reject_legacy_inputs=lambda: None, ensure_local=lambda c: None,
                                candidates=lambda c: iter(attempts), context_for=context_for,
                                collect=lambda c: {}, sufficient=lambda c, r: None,
                                check_existing=lambda c, r: None, save_context=save,
                                load_deployment=lambda: copy.deepcopy(selected), tf=tf,
                                scheduler_unavailable=lambda e, c: True, destroy=destroy,
                                key_path=lambda c: local / 'key', discover_route=lambda i: None), \
                 patch.object(remote, 'configure'), \
                 patch.object(lab.subprocess, 'run', side_effect=AssertionError('deploy must not reconstruct a database')):
                if cleanup_fails:
                    with self.assertRaises(RuntimeError):
                        lab.deploy()
                    self.assertEqual(events, ['plan:ru-9', 'cleanup:ru-9'])
                else:
                    lab.deploy()
                    self.assertEqual(events, ['plan:ru-9', 'cleanup:ru-9', 'plan:ru-7'])
                    self.assertEqual(selected['phase'], 'active')


if __name__ == '__main__':
    unittest.main()
