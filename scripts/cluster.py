"""MySQL Shell stage: bootstrap once or join empty instances to a healthy cluster."""
import json
import ipaddress


def reconcile_cluster(cfg, dba, shell):
    nodes = cfg['db_nodes']
    seed = cfg['seed_node']
    def address(node):
        return {'scheme': 'mysql', 'host': cfg['ips'][node], 'port': 3306,
                'user': 'icadmin', 'password': cfg['cluster_password']}
    if cfg['bootstrap']:
        dba.configure_instance(address(seed), {'restart': True, 'interactive': False})
    shell.connect(address(seed))
    try:
        cluster = dba.get_cluster('otusDZ05')
    except Exception:
        if not cfg['bootstrap']:
            raise  # Never rebootstrap an existing stand after a discovery error.
        cluster = dba.create_cluster('otusDZ05', {'gtidSetIsComplete': True, 'memberSslMode': 'REQUIRED',
            'expelTimeout': 5, 'autoRejoinTries': 3, 'consistency': 'BEFORE_ON_PRIMARY_FAILOVER'})
    topology = cluster.status()['defaultReplicaSet']['topology']
    for node in nodes:
        endpoint = cfg['ips'][node] + ':3306'
        if endpoint in topology:
            if topology[endpoint]['status'] != 'ONLINE':
                raise RuntimeError('Existing member is not ONLINE; repair it before expansion')
            continue
        if node not in cfg['configure_nodes']:
            raise RuntimeError('Existing member absent from cluster; automatic cloning is forbidden')
        shell.connect(address(node))
        tables = shell.get_session().run_sql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema NOT IN ('mysql','sys','performance_schema','information_schema','mysql_innodb_cluster_metadata')").fetch_one()[0]
        if int(tables):
            raise RuntimeError('Clone destination contains data; refusing to overwrite')
        dba.configure_instance(address(node), {'restart': True, 'interactive': False})
        shell.connect(address(seed))
        cluster.add_instance(address(node), {'recoveryMethod': 'clone'})
        topology = cluster.status()['defaultReplicaSet']['topology']
    topology = cluster.status()['defaultReplicaSet']['topology']
    if any(cfg['ips'][n] + ':3306' not in topology or topology[cfg['ips'][n] + ':3306']['status'] != 'ONLINE' for n in nodes):
        raise RuntimeError('Not all requested members are ONLINE')
    primary = next(n for n in nodes if topology[cfg['ips'][n] + ':3306']['memberRole'] == 'PRIMARY')
    shell.connect(address(primary))
    sql = shell.get_session()
    sql.run_sql('CREATE DATABASE IF NOT EXISTS otus CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs')
    password = cfg['db_password']
    assert len(password) == 64 and all(c in '0123456789abcdef' for c in password)
    app_host = str(ipaddress.IPv4Address(cfg['ips']['app']))
    sql.run_sql("CREATE USER IF NOT EXISTS 'otus'@'" + app_host + "' IDENTIFIED BY '" + password + "'")
    sql.run_sql("GRANT ALL PRIVILEGES ON otus.* TO 'otus'@'" + app_host + "'")
    # Temporary test-database rights are handled only by the verification stage.
    print('PORTAL SCHEMA AND ACCOUNT READY')
    print(json.dumps({'clusterName': 'otusDZ05', 'topology': {
        n: {'status': topology[cfg['ips'][n] + ':3306']['status'],
            'role': topology[cfg['ips'][n] + ':3306']['memberRole']} for n in nodes}}))


if __name__ == '__main__':
    with open('/root/dz05.json') as source:
        reconcile_cluster(json.load(source), dba, shell)
