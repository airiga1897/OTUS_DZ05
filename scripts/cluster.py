"""Execute with mysqlsh --py; Shell AdminAPI globals are supplied by mysqlsh."""
import json
import ipaddress

with open('/root/dz05.json') as source:
    cfg=json.load(source)

def address(node):
    return {'scheme':'mysql','host':cfg['ips'][node],'port':3306,
            'user':'icadmin','password':cfg['cluster_password']}

for node in ['db1','db2','db3']:
    dba.configure_instance(address(node),{'restart':True,'interactive':False})
shell.connect(address('db1'))
try:
    cluster=dba.get_cluster('otusDZ05')
except Exception:
    cluster=dba.create_cluster('otusDZ05',{'gtidSetIsComplete':True,'memberSslMode':'REQUIRED',
        'expelTimeout':5,'autoRejoinTries':3,'consistency':'BEFORE_ON_PRIMARY_FAILOVER'})
topology=cluster.status()['defaultReplicaSet']['topology']
for node in ['db2','db3']:
    if cfg['ips'][node]+':3306' not in topology:
        cluster.add_instance(address(node),{'recoveryMethod':'clone'})
shell.connect(address('db1'))
sql=shell.get_session()
sql.run_sql('CREATE DATABASE IF NOT EXISTS otus CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs')
password=cfg['db_password']
assert len(password)==64 and all(c in '0123456789abcdef' for c in password)
app_host=str(ipaddress.IPv4Address(cfg['ips']['app']))
sql.run_sql("CREATE USER IF NOT EXISTS 'otus'@'"+app_host+"' IDENTIFIED BY '"+password+"'")
sql.run_sql("GRANT ALL PRIVILEGES ON otus.* TO 'otus'@'"+app_host+"'")
sql.run_sql("GRANT ALL PRIVILEGES ON test_otus.* TO 'otus'@'"+app_host+"'")
print('PORTAL SCHEMA AND ACCOUNT READY')
tree=cluster.status({'extended':1})
summary={'clusterName':str(tree['clusterName']),'status':str(tree['defaultReplicaSet']['status']),'topology':{}}
for node in ['db1','db2','db3']:
    member=tree['defaultReplicaSet']['topology'][cfg['ips'][node]+':3306']
    summary['topology'][node]={'status':str(member['status']),'role':str(member['memberRole'])}
print(json.dumps(summary))
