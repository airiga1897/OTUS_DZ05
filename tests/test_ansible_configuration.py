"""Ansible is the server configuration stage, without cloud calls in tests."""
import json
from pathlib import Path
import sys
import unittest
import tempfile
import shutil
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import ansible_configuration


class AnsibleConfiguration(unittest.TestCase):
    def test_configuration_runs_ansible_without_cloud_credentials_and_removes_temporary_key(self):
        import remote
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted), tempfile.TemporaryDirectory(dir=ROOT/'.local') as directory:
                root = Path(directory)
                local = root/'.local'
                local.mkdir()
                shutil.copytree(ROOT/'ansible', root/'ansible')
                (root/'app').mkdir()
                (root/'app/manage.py').write_text('# synthetic')
                (root/'scripts').mkdir()
                for name in ('cluster.py', 'evidence.py', 'bootstrap-ansible.sh'):
                    shutil.copyfile(ROOT/'scripts'/name, root/'scripts'/name)
                for name in ('key', 'known_hosts'):
                    (local/name).write_text('synthetic')
                (local/'app-secrets.json').write_text(json.dumps(dict(cluster_password='a'*64, db_password='b'*64, django_secret_key='c'*96)))
                inventory = dict(private_ips={'app': '10.55.0.10', 'db1': '10.55.0.21', 'db2': '10.55.0.22', 'db3': '10.55.0.23'},
                                 server_ids={n: n+'-id' for n in ('app', 'db1', 'db2', 'db3')},
                                 app_public_ip='192.0.2.1', subnet_cidr='10.55.0.0/24', name_prefix='otus-dz05', ssh_key_path=str(local/'key'))
                commands = []
                def run(client, command, *args, **kwargs):
                    commands.append(command)
                    if interrupted and 'site.yml' in command and '--syntax-check' not in command:
                        raise RuntimeError('playbook interrupted')
                    return ''
                with patch.multiple(remote, ROOT=root, LOCAL=local, trusted_keys=Mock(),
                                    connect=Mock(return_value=Mock()), put=Mock(), run=run, close=Mock()):
                    if interrupted:
                        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                            ansible_configuration.configure(inventory, configured_nodes=list(inventory['server_ids']))
                    else:
                        ansible_configuration.configure(inventory, configured_nodes=list(inventory['server_ids']))
                self.assertTrue(any('ansible-playbook' in c and '--syntax-check' in c for c in commands))
                self.assertTrue(any('ansible-playbook' in c and '--syntax-check' not in c for c in commands))
                self.assertTrue(commands[-1].startswith('rm -rf -- /home/otus/.ansible-dz05/run-'))
                self.assertFalse(any('node.py' in c or '/app.py' in c for c in commands))

    def test_yaml_templates_and_handler_references_parse_locally(self):
        import yaml
        from jinja2 import Environment
        environment = Environment()
        for path in (ROOT/'ansible').rglob('*.yml'):
            self.assertIsInstance(yaml.safe_load(path.read_text()), list)
        for path in (ROOT/'ansible').rglob('*.j2'):
            environment.parse(path.read_text())
    def test_inventory_uses_linux_app_controller_and_stable_members(self):
        lab = {'private_ips': {'app': '10.55.0.10', 'db1': '10.55.0.21', 'db2': '10.55.0.22',
                               'db3': '10.55.0.23', 'db4': '10.55.0.24'},
               'server_ids': {n: n+'-id' for n in ['app', 'db1', 'db2', 'db3', 'db4']}}
        inventory = ansible_configuration.inventory(lab, ['app', 'db1', 'db2', 'db3'])
        groups = inventory['all']['children']
        self.assertEqual(groups['application']['hosts']['app']['ansible_connection'], 'local')
        self.assertEqual(set(groups['database']['hosts']), {'db1', 'db2', 'db3', 'db4'})
        self.assertFalse(groups['database']['hosts']['db1']['dz05_new'])
        self.assertTrue(groups['database']['hosts']['db4']['dz05_new'])
        self.assertNotIn('password', json.dumps(inventory))

    def test_playbook_contains_real_configuration_roles(self):
        import yaml
        plays = yaml.safe_load((ROOT/'ansible/site.yml').read_text())
        roles = {r if isinstance(r, str) else r['role'] for play in plays for r in play.get('roles', [])}
        self.assertEqual(roles, {'common', 'mysql_member', 'mysql_cluster', 'mysql_router', 'django_portal'})
        for role in roles:
            self.assertTrue((ROOT/'ansible/roles'/role/'tasks/main.yml').is_file())
