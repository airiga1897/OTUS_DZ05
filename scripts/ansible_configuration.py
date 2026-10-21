"""Run Ansible on the stand's Ubuntu app VM; Windows only provides SSH transport."""
import json
import secrets
import tarfile
import time
import paramiko


def inventory(lab: dict, configured_nodes=None, run_dir='/home/otus/.ansible-dz05/run') -> dict:
    configured = set(configured_nodes or [])
    hosts = {n: {'ansible_host': ip, 'dz05_new': n not in configured,
                 'ansible_python_interpreter': '/usr/bin/python3'} for n, ip in lab['private_ips'].items()}
    hosts['app'].update(ansible_connection='local')
    return {'all': {'vars': {
        'ansible_user': 'otus', 'ansible_become': True,
        'ansible_ssh_private_key_file': run_dir + '/id_ed25519',
        'ansible_ssh_common_args': '-o StrictHostKeyChecking=yes -o UserKnownHostsFile=' + run_dir + '/known_hosts',
    }, 'children': {'application': {'hosts': {'app': hosts.pop('app')}}, 'database': {'hosts': hosts}}}}


def configure(lab: dict, configured_nodes=None) -> None:
    import remote
    root, local = remote.ROOT, remote.LOCAL
    remote.trusted_keys(lab)
    secretpath = local / 'app-secrets.json'
    if configured_nodes is not None and not secretpath.exists():
        raise ValueError('Existing stand secrets missing; refusing regeneration')
    if not secretpath.exists():
        secretpath.write_text(json.dumps({'cluster_password': secrets.token_hex(32),
                                         'db_password': secrets.token_hex(32), 'django_secret_key': secrets.token_hex(48)}), encoding='utf-8')
    db_nodes = sorted(n for n in lab['server_ids'] if n != 'app')
    new_nodes = sorted(set(lab['server_ids']) - set(configured_nodes or []))
    seed = next((n for n in sorted(configured_nodes or []) if n != 'app'), db_nodes[0])
    config = dict(json.loads(secretpath.read_text()), ips=lab['private_ips'], public_ip=lab['app_public_ip'],
                  subnet_cidr=lab['subnet_cidr'], name_prefix=lab['name_prefix'], db_nodes=db_nodes,
                  configure_nodes=[n for n in new_nodes if n != 'app'], bootstrap=configured_nodes is None, seed_node=seed)
    archive = local / 'ansible-source.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        paths = list((root/'ansible').rglob('*')) + list((root/'app').rglob('*'))
        paths += [root/'scripts'/name for name in ('cluster.py', 'evidence.py')]
        for path in paths:
            if path.is_file() and '__pycache__' not in path.parts:
                bundle.add(path, arcname=path.relative_to(root).as_posix())
    for attempt in range(12):
        try:
            client = remote.connect(lab)
            break
        except (OSError, paramiko.SSHException):
            if attempt == 11:
                raise
            time.sleep(3)
    run_dir = '/home/otus/.ansible-dz05/run-' + secrets.token_hex(8)
    try:
        remote.run(client, 'mkdir -p ' + run_dir + '; chmod 700 ' + run_dir)
        remote.put(client, root/'scripts/bootstrap-ansible.sh', run_dir+'/bootstrap.sh')
        remote.run(client, 'bash ' + run_dir+'/bootstrap.sh')
        remote.put(client, archive, run_dir+'/source.tar.gz')
        remote.run(client, 'tar -xzf ' + run_dir+'/source.tar.gz -C '+run_dir)
        remote.put(client, lab['ssh_key_path'], run_dir+'/id_ed25519')
        remote.put(client, local/'known_hosts', run_dir+'/known_hosts')
        remote.run(client, "umask 077; cat > " + run_dir+'/inventory.json', json.dumps(inventory(lab, configured_nodes, run_dir)), quiet=True)
        remote.run(client, "umask 077; cat > " + run_dir+'/vars.json', json.dumps({'dz05': config, 'dz05_run_dir': run_dir}), quiet=True)
        remote.run(client, 'chmod 600 '+run_dir+'/id_ed25519 '+run_dir+'/known_hosts')
        command = ('cd '+run_dir+'/ansible && ANSIBLE_CONFIG='+run_dir+'/ansible/ansible.cfg '
                   '/home/otus/.ansible-dz05/venv/bin/ansible-playbook -i '+run_dir+'/inventory.json '
                   '--extra-vars @'+run_dir+'/vars.json site.yml')
        remote.run(client, command + ' --syntax-check')
        remote.run(client, command)
        from settings import write_json
        write_json(root/'docs/ansible-latest.json', {'success': True, 'controller': 'app Ubuntu 24.04 / Python 3.12',
                   'ansible_core': '2.20.10', 'bootstrap': configured_nodes is None,
                   'new_nodes': new_nodes, 'roles': ['common', 'mysql_member', 'mysql_cluster', 'mysql_router', 'django_portal']})
    finally:
        try:
            # Generated fixed prefix + random hex; no user-supplied deletion paths.
            remote.run(client, 'rm -rf -- ' + run_dir, quiet=True)
        finally:
            remote.close(client)
