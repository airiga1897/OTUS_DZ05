"""Additive deployment contracts with no cloud or live filesystem mutation."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import growth


class GrowthSafety(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((Path(__file__).resolve().parents[1] / 'config/lab.example.json').read_text())
        self.desired = copy.deepcopy(self.config)
        self.desired['nodes']['db4'] = {'host': 24, 'role': 'db'}

    def test_only_added_nodes_are_selected(self):
        self.assertEqual(growth.added_nodes(self.config, self.desired), ['db4'])
        self.assertEqual(growth.added_nodes(self.config, self.config), [])

    def test_existing_nodes_and_placement_cannot_change(self):
        for mutate in ('remove', 'address', 'pool', 'disk'):
            desired = copy.deepcopy(self.desired)
            if mutate == 'remove':
                del desired['nodes']['db2']
            elif mutate == 'address':
                desired['nodes']['db2']['host'] = 32
            elif mutate == 'pool':
                desired['pools'] = ['ru-7']
            else:
                desired['boot_disk_gb'] += 1
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                growth.added_nodes(self.config, desired)

    def test_plan_only_creates_new_nodes_and_preserves_existing_identities(self):
        resources = []
        for kind, label in [('compute_instance', 'node'), ('networking_port', 'node'), ('blockstorage_volume', 'boot')]:
            resources.append(dict(mode='managed', type='openstack_' + kind + '_v2' if kind != 'blockstorage_volume' else 'openstack_blockstorage_volume_v3',
                                  address=('openstack_' + kind + ('_v3' if kind == 'blockstorage_volume' else '_v2') + '.' + label + '["db4"]'),
                                  change=dict(actions=['create'], before=None, after={})))
        growth.validate_additions({'resource_changes': resources}, ['db4'])
        resources[0]['change']['actions'] = ['delete', 'create']
        with self.assertRaises(ValueError):
            growth.validate_additions({'resource_changes': resources}, ['db4'])
