"""Configure the DZ05 Router and Django/uWSGI/Nginx host."""
import json
import os
from pathlib import Path
import subprocess

cfg=json.loads(Path('/root/dz05.json').read_text())
base=Path('/home/otus/dz05')
root=Path('/opt/otus-dz05')
root.mkdir(exist_ok=True)
def cmd(args,**kwargs):return subprocess.run(args,check=True,**kwargs)
cmd(['cp','-a',str(base/'app'),str(root)])
if not (root/'venv').exists():cmd(['python3','-m','venv',str(root/'venv')])
python=str(root/'venv/bin/python')
cmd([python,'-m','pip','install','--disable-pip-version-check','-r',str(root/'app/requirements.txt'),'uwsgi==2.0.31'])
router=Path('/etc/otus-router')
if not (router/'mysqlrouter.conf').exists():
    cmd(['mysqlrouter','--bootstrap','icadmin@'+cfg['ips']['db1']+':3306',
         '--directory',str(router),'--user','mysqlrouter','--conf-bind-address','127.0.0.1',
         '--report-host',cfg['ips']['app'],'--disable-rest'],
        input=cfg['cluster_password']+'\n',text=True)
Path('/etc/systemd/system/otus-router.service').write_text('''[Unit]
After=network-online.target
Wants=network-online.target
[Service]
User=mysqlrouter
Group=mysqlrouter
ExecStart=/usr/bin/mysqlrouter -c /etc/otus-router/mysqlrouter.conf
Restart=on-failure
RestartSec=2
[Install]
WantedBy=multi-user.target
''')
cmd(['systemctl','daemon-reload'])
cmd(['systemctl','enable','--now','otus-router'])
env={'DJANGO_SECRET_KEY':cfg['django_secret_key'],'DB_PASSWORD':cfg['db_password'],
     'DB_HOST':'127.0.0.1','DB_PORT':'6446','DJANGO_ALLOWED_HOSTS':cfg['public_ip']+',localhost,127.0.0.1',
     'NODE_NAME':cfg['name_prefix']+'-app','SHARED_ROOT':'/srv/otus','REQUIRE_SHARED_MOUNT':'0'}
p=root/'app.env'
p.write_text(''.join(k+'='+v+'\n' for k,v in env.items()))
p.chmod(0o600)
env=dict(os.environ,**env)
for name in ['static','media']:Path('/srv/otus/'+name).mkdir(parents=True,exist_ok=True)
Path('/srv/otus/.ready').write_text('otus-dz05')
manage=[python,str(root/'app/manage.py')]
cmd(manage+['migrate','--noinput'],env=env)
if not (root/'imported').exists():
    cmd(manage+['loaddata',str(base/'portal.json')],env=env)
    (root/'imported').write_text('Reconstructed from DZ04 source; not historical PostgreSQL data.\n')
cmd(manage+['collectstatic','--noinput'],env=env)
cmd(['chown','-R','www-data:www-data','/srv/otus'])
(root/'uwsgi.ini').write_text('''[uwsgi]
chdir=/opt/otus-dz05/app
module=config.wsgi:application
master=true
processes=2
socket=127.0.0.1:8000
vacuum=true
die-on-term=true
need-app=true
''')
Path('/etc/systemd/system/otus-uwsgi.service').write_text('''[Unit]
After=network-online.target otus-router.service
Wants=otus-router.service
[Service]
User=www-data
Group=www-data
EnvironmentFile=/opt/otus-dz05/app.env
ExecStart=/opt/otus-dz05/venv/bin/uwsgi --ini /opt/otus-dz05/uwsgi.ini
Restart=on-failure
RestartSec=2
[Install]
WantedBy=multi-user.target
''')
Path('/etc/nginx/sites-enabled/default').write_text('''server {
 listen 80 default_server;
 server_name _;
 client_max_body_size 6m;
 location /static/ { alias /srv/otus/static/; }
 location /media/ { alias /srv/otus/media/; }
 location / { include uwsgi_params; uwsgi_pass 127.0.0.1:8000; }
}
''')
cmd(['nginx','-t'])
cmd(['systemctl','daemon-reload'])
cmd(['systemctl','enable','otus-uwsgi'])
cmd(['systemctl','restart','otus-uwsgi','nginx'])
cmd(manage+['verify_database'],env=env)
cmd(manage+['test','knowledge','--noinput','--verbosity','1'],env=env)
cmd(['mysqlsh','--py','--execute',"import json,ipaddress; c=json.load(open('/root/dz05.json')); shell.connect({'scheme':'mysql','host':c['ips']['db1'],'user':'icadmin','password':c['cluster_password']}); session.run_sql(\"REVOKE ALL PRIVILEGES ON test_otus.* FROM 'otus'@'\"+str(ipaddress.IPv4Address(c['ips']['app']))+\"'\")"])
cmd(['mysqlrouter','--version'])
print('APPLICATION READY')
