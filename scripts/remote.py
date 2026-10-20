"""Trusted Nova-console SSH transport and script-based configuration."""
import base64
import concurrent.futures
import json
from pathlib import Path
import re
import secrets
import sys
import time
import tarfile
import paramiko
from selectel_api import NovaClient
from settings import cloud_config, load_deployment

ROOT=Path(__file__).resolve().parents[1]
LOCAL=ROOT/'.local'

def auth():
    return cloud_config(load_deployment())

def trusted_keys(lab):
    api=NovaClient(auth())
    lines=[]
    for node,uid in lab['server_ids'].items():
        for attempt in range(60):
            data=api.request('/servers/'+uid+'/action',{'os-getConsoleOutput':{'length':1000}})['output']
            section=re.search(r'BEGIN SSH HOST KEY KEYS.*?END SSH HOST KEY KEYS',data,re.S)
            match=re.search(r'ssh-ed25519\s+([A-Za-z0-9+/=]+)',section.group()) if section else None
            if match: break
            time.sleep(5)
        if not match: raise RuntimeError('No trusted Nova-console SSH key for '+node)
        paramiko.Ed25519Key(data=base64.b64decode(match[1]))
        hosts=[lab['private_ips'][node]]
        if node=='app':
            port=lab.get('admin_ssh_port',22)
            hosts.append(lab['app_public_ip'] if port==22 else '['+lab['app_public_ip']+']:'+str(port))
        lines.append(','.join(hosts)+' ssh-ed25519 '+match[1]+'\n')
    (LOCAL/'known_hosts').write_text(''.join(lines),encoding='utf-8')

def connect(lab,node='app'):
    client=paramiko.SSHClient()
    client.load_host_keys(str(LOCAL/'known_hosts'))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    jump=None
    sock=None
    if node!='app':
        jump=connect(lab)
        sock=jump.get_transport().open_channel('direct-tcpip',(lab['private_ips'][node],22),('127.0.0.1',0))
    try:
        client.connect(lab['app_public_ip'] if node=='app' else lab['private_ips'][node],
                       port=lab.get('admin_ssh_port',22) if node=='app' else 22,
                       username='otus',key_filename=lab['ssh_key_path'],sock=sock,
                       look_for_keys=False,allow_agent=False,timeout=8,banner_timeout=20)
        client._dz05_jump=jump
        return client
    except Exception:
        client.close()
        if jump: jump.close()
        raise

def close(client):
    client.close()
    if getattr(client,'_dz05_jump',None):client._dz05_jump.close()

def redact(text):
    path=LOCAL/'app-secrets.json'
    if path.exists():
        for value in json.loads(path.read_text()).values():
            if isinstance(value,str) and len(value)>16: text=text.replace(value,'[REDACTED]')
    return text

def run(client,command,input_data=None,quiet=False):
    channel=client.get_transport().open_session()
    channel.set_combine_stderr(True)
    channel.exec_command(command)
    if input_data is not None:
        channel.sendall(input_data.encode() if isinstance(input_data,str) else input_data)
        channel.shutdown_write()
    chunks=[]
    while not channel.exit_status_ready() or channel.recv_ready():
        if channel.recv_ready():
            data=channel.recv(65536).decode('utf-8',errors='replace')
            chunks.append(data)
            if not quiet: print(redact(data),end='',flush=True)
        else: time.sleep(.2)
    code=channel.recv_exit_status()
    output=redact(''.join(chunks))
    with (LOCAL/'remote.log').open('a',encoding='utf-8') as f: f.write(output)
    if code:
        if quiet:print(output[-3000:])
        raise RuntimeError('Remote command failed, exit='+str(code))
    return output

def put(client,source,destination):
    with client.open_sftp() as sftp:
        sftp.put(str(source),destination)

def configure(lab):
    trusted_keys(lab)
    for attempt in range(12):
        try:
            ready=connect(lab)
            close(ready)
            break
        except (OSError,paramiko.SSHException) as error:
            print('Waiting for application SSH, attempt '+str(attempt+1)+': '+type(error).__name__,flush=True)
            time.sleep(3)
    else:raise RuntimeError('Application VM SSH did not become ready')
    secretpath=LOCAL/'app-secrets.json'
    if not secretpath.exists():
        secretpath.write_text(json.dumps({'cluster_password':secrets.token_hex(32),
                                        'db_password':secrets.token_hex(32),
                                        'django_secret_key':secrets.token_hex(48)}),encoding='utf-8')
    config=dict(json.loads(secretpath.read_text()),ips=lab['private_ips'],public_ip=lab['app_public_ip'],
                subnet_cidr=lab['subnet_cidr'],name_prefix=lab['name_prefix'])
    def node_setup(node):
        client=connect(lab,node)
        try:
            command="""cloud-init status --wait >/tmp/dz05-cloud-init-status.txt
rc=$?
if [ "$rc" != 0 ] && [ "$rc" != 2 ]; then cat /tmp/dz05-cloud-init-status.txt; exit "$rc"; fi
sudo python3 - "$rc" <<'PY'
import json,sys
d=json.load(open('/run/cloud-init/result.json'))
e=d.get('v1',{}).get('errors',[])
print(json.dumps({'status':'done','fatal_errors':e,'warning_exit_code':int(sys.argv[1])}))
sys.exit(bool(e))
PY
"""
            cloud_status=json.loads(run(client,command,quiet=True).strip().splitlines()[-1])
            cloud_status['node']=node
            run(client,'mkdir -p /home/otus/dz05')
            put(client,ROOT/'scripts/node.py','/home/otus/dz05/node.py')
            run(client,'sudo sh -c \'umask 077; cat > /root/dz05.json\'',json.dumps(config),quiet=True)
            run(client,'sudo python3 /home/otus/dz05/node.py '+node)
            return cloud_status
        finally:close(client)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(node_setup,n) for n in lab['server_ids']]
        cloud_statuses=[f.result() for f in futures]
    (ROOT/'docs/cloud-init.json').write_text(json.dumps(cloud_statuses,indent=2)+'\n',encoding='utf-8')
    client=connect(lab,'db1')
    try:
        put(client,ROOT/'scripts/cluster.py','/home/otus/dz05/cluster.py')
        run(client,'sudo mysqlsh --no-wizard --py --file /home/otus/dz05/cluster.py')
    finally:close(client)
    client=connect(lab)
    try:
        # Only project source files are uploaded; no operator credentials or Terraform state.
        archive=LOCAL/'app-source.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            for path in (ROOT/'app').rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts:
                    tar.add(path,arcname='app/'+path.relative_to(ROOT/'app').as_posix())
        put(client,archive,'/home/otus/dz05/app-source.tar.gz')
        run(client,'tar -xzf /home/otus/dz05/app-source.tar.gz -C /home/otus/dz05',quiet=True)
        put(client,ROOT/'scripts/app.py','/home/otus/dz05/app.py')
        put(client,ROOT/'scripts/evidence.py','/home/otus/dz05/evidence.py')
        put(client,LOCAL/'reconstructed-portal.json','/home/otus/dz05/portal.json')
        run(client,'sudo python3 /home/otus/dz05/app.py')
    finally:close(client)
