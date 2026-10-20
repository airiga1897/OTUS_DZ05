"""Run as root on freshly created DZ05 Ubuntu nodes; no cloud credentials needed."""
import json
import os
from pathlib import Path
import subprocess
import sys
import ipaddress

cfg=json.loads(Path('/root/dz05.json').read_text())
node=sys.argv[1]
assert node in cfg['ips']
if node=='app':
    subprocess.run(['systemctl','disable','--now','otus-dz05-route.service'],check=True)
os.environ['DEBIAN_FRONTEND']='noninteractive'
def cmd(args,**kwargs):subprocess.run(args,check=True,**kwargs)
cmd(['apt-get','update','-qq'])
packages=(['mysql-server','mysql-shell'] if node!='app' else
          ['mysql-router','mysql-shell','nginx','python3-venv','python3-dev','build-essential',
           'pkg-config','default-libmysqlclient-dev','libpcre3-dev'])
cmd(['apt-get','install','-y','-qq']+packages)
if node!='app':
    path=Path('/etc/mysql/mysql.conf.d/zz-dz05.cnf')
    content=f'''[mysqld]
server_id={int(node[-1])}
bind-address=0.0.0.0
skip_name_resolve=ON
report_host={cfg['ips'][node]}
log_bin=mysql-bin
binlog_format=ROW
binlog_transaction_dependency_tracking=WRITESET
gtid_mode=ON
enforce_gtid_consistency=ON
transaction_write_set_extraction=XXHASH64
innodb_buffer_pool_size=512M
max_connections=100
'''
    changed=not path.exists() or path.read_text()!=content
    path.write_text(content)
    if changed:cmd(['systemctl','restart','mysql'])
    password=cfg['cluster_password']
    assert all(c in '0123456789abcdef' for c in password)
    network=ipaddress.IPv4Network(cfg['subnet_cidr'],strict=True).with_netmask
    sql=f"CREATE USER IF NOT EXISTS 'icadmin'@'{network}' IDENTIFIED BY '{password}'; GRANT ALL PRIVILEGES ON *.* TO 'icadmin'@'{network}' WITH GRANT OPTION;"
    cmd(['mysql'],input=sql,text=True,stdout=subprocess.DEVNULL)
    cmd(['mysql','-NBe','SELECT VERSION(),@@server_id,@@gtid_mode,@@bind_address'])
    assert subprocess.check_output(['mysql','-NBe','SELECT @@bind_address'],text=True).strip()=='0.0.0.0'
    import socket
    with socket.create_connection((cfg['ips'][node],3306),timeout=3):pass
else:
    if subprocess.run(['id','-u','mysqlrouter'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
        cmd(['useradd','--system','--user-group','--home-dir','/nonexistent','--shell','/usr/sbin/nologin','mysqlrouter'])
print('NODE READY',node)
