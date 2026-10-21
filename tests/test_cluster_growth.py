"""Exercise the actual MySQL Shell procedure against an in-memory AdminAPI double."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cluster import reconcile_cluster


class ClusterGrowth(unittest.TestCase):
    def test_add_fourth_member_then_repeat_preserves_data(self):
        ips = {'app': '10.55.0.10', **{f'db{i}': f'10.55.0.{20+i}' for i in range(1, 5)}}
        topology = {ips[f'db{i}'] + ':3306': {'status': 'ONLINE', 'memberRole': 'PRIMARY' if i == 2 else 'SECONDARY'} for i in range(1, 4)}
        rows = {f'db{i}': {1: 'initial', 77: 'user-edited-article'} for i in range(1, 4)}
        before = copy.deepcopy(rows)
        cluster = Mock()
        cluster.status.side_effect = lambda: {'defaultReplicaSet': {'topology': copy.deepcopy(topology)}}
        def join(address, options):
            self.assertEqual(options, {'recoveryMethod': 'clone'})
            topology[address['host'] + ':3306'] = {'status': 'ONLINE', 'memberRole': 'SECONDARY'}
            rows['db4'] = copy.deepcopy(rows['db2'])
        cluster.add_instance.side_effect = join
        dba = Mock()
        dba.get_cluster.return_value = cluster
        shell = Mock()
        shell.get_session.return_value.run_sql.return_value.fetch_one.return_value = [0]
        cfg = dict(ips=ips, db_nodes=['db1', 'db2', 'db3', 'db4'], seed_node='db1',
                   bootstrap=False, configure_nodes=['db4'], cluster_password='a'*64, db_password='b'*64)
        reconcile_cluster(cfg, dba, shell)
        self.assertEqual({k: rows[k] for k in before}, before)
        self.assertEqual(rows['db4'], before['db2'])
        dba.configure_instance.assert_called_once()
        self.assertEqual(dba.configure_instance.call_args.args[0]['host'], ips['db4'])
        cfg['configure_nodes'] = []
        reconcile_cluster(cfg, dba, shell)
        cluster.add_instance.assert_called_once()
        dba.configure_instance.assert_called_once()
        dba.create_cluster.assert_not_called()

    def test_nonempty_clone_target_is_rejected(self):
        cfg = dict(ips={'db1': '10.55.0.21', 'db4': '10.55.0.24'}, db_nodes=['db1', 'db4'],
                   seed_node='db1', bootstrap=False, configure_nodes=['db4'], cluster_password='a'*64)
        dba, shell = Mock(), Mock()
        dba.get_cluster.return_value.status.return_value = {'defaultReplicaSet': {'topology': {
            '10.55.0.21:3306': {'status': 'ONLINE', 'memberRole': 'PRIMARY'}}}}
        shell.get_session.return_value.run_sql.return_value.fetch_one.return_value = [1]
        with self.assertRaisesRegex(RuntimeError, 'contains data'):
            reconcile_cluster(cfg, dba, shell)
        dba.get_cluster.return_value.add_instance.assert_not_called()

    def test_discovery_error_does_not_bootstrap_existing_cluster(self):
        dba, shell = Mock(), Mock()
        dba.get_cluster.side_effect = RuntimeError('metadata unavailable')
        with self.assertRaises(RuntimeError):
            reconcile_cluster(dict(ips={'db1': '10.55.0.21'}, db_nodes=['db1'], seed_node='db1',
                                  bootstrap=False, cluster_password='a'*64), dba, shell)
        dba.create_cluster.assert_not_called()

