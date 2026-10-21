"""Trusted Nova-console SSH transport for configuration and separate verification."""
import base64
import json
from pathlib import Path
import re
import time
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


def assert_online(lab):
    from verify import status
    nodes = sorted(n for n in lab['server_ids'] if n != 'app')
    members = status(lab, nodes[0])
    if len(members) != len(nodes) or any(m[1] != 'ONLINE' for m in members):
        raise RuntimeError('Every existing database member must be ONLINE before reconfiguration')
    if {m[0] for m in members} != {lab['private_ips'][n] for n in nodes}:
        raise RuntimeError('Cluster membership differs from saved inventory')
    return members


def capture_data(lab):
    client = connect(lab)
    try:
        put(client, ROOT / 'scripts/data_snapshot.py', '/home/otus/dz05/data_snapshot.py')
        return json.loads(run(client, 'sudo /opt/otus-dz05/venv/bin/python /home/otus/dz05/data_snapshot.py', quiet=True))
    finally:
        close(client)

def configure(lab, configured_nodes=None):
    from ansible_configuration import configure as configure_ansible
    configure_ansible(lab, configured_nodes)
