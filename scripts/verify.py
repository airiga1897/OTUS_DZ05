"""Live read/write and VM failure checks; only DZ05 UUIDs from own Terraform output."""
import datetime
import json
from pathlib import Path
import time
import threading
import re
import urllib.request
import ipaddress
import hashlib
from remote import connect,close,run,auth,ROOT,LOCAL
from selectel_api import NovaClient
from http_admin import verify_admin

MANAGE="sudo sh -c 'set -a; . /opt/otus-dz05/app.env; set +a; /opt/otus-dz05/venv/bin/python /opt/otus-dz05/app/manage.py "

def manage(client,command):return run(client,MANAGE+command+"'",quiet=True)


def verify_cycle_control(lab, run_id):
    if not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise ValueError('Invalid temporary run identity')
    with urllib.request.urlopen(lab['site_url'] + '/articles/cycle-control-' + run_id + '/', timeout=10) as response:
        page = response.read()
        if response.status != 200 or b'Committed before expansion' not in page:
            raise RuntimeError('Control article cannot be read through the portal')
    media = re.search(rb'src="(/media/[^\"]+)"', page)
    if not media:
        raise RuntimeError('Control article image missing')
    with urllib.request.urlopen(lab['site_url'] + media.group(1).decode(), timeout=10) as response:
        image = response.read()
        if response.status != 200 or not image:
            raise RuntimeError('Control media cannot be read through the portal')
    return {'article_read': True, 'media_sha256': hashlib.sha256(image).hexdigest()}


def verify_django_tests(lab, client):
    nodes = sorted(n for n in lab['server_ids'] if n != 'app')
    members = status(lab, nodes[0])
    primary_host = next(m[0] for m in members if m[1] == 'ONLINE' and m[2] == 'PRIMARY')
    primary = next(n for n in nodes if lab['private_ips'][n] == primary_host)
    db = connect(lab, primary)
    account = "'otus'@'" + str(ipaddress.IPv4Address(lab['private_ips']['app'])) + "'"
    try:
        run(db, 'sudo mysql', 'GRANT ALL PRIVILEGES ON test_otus.* TO ' + account + ';', quiet=True)
        manage(client, 'test knowledge --noinput --verbosity 1')
    finally:
        try:
            run(db, 'sudo mysql', 'REVOKE ALL PRIVILEGES ON test_otus.* FROM ' + account + ';', quiet=True)
        finally:
            close(db)


def verify_reconcile(lab, baseline):
    from remote import assert_online, capture_data
    members = assert_online(lab)
    if capture_data(lab) != baseline['data']:
        raise RuntimeError('Django rows or media changed during configuration; recovery files preserved')
    nodes = sorted(n for n in lab['server_ids'] if n != 'app')
    snapshots = {n: database_snapshot(lab, n) for n in nodes}
    if len({s['article_sha256'] for s in snapshots.values()}) != 1:
        raise RuntimeError('Expanded replicas disagree')
    client = connect(lab)
    try:
        crud = manage(client, 'verify_database').strip()
        if 'CRUD PASS' not in crud:
            raise RuntimeError('Application CRUD did not succeed')
    finally:
        close(client)
    return {'all_requested_members_online': True, 'existing_vm_ids_preserved': True,
            'django_rows_and_media_preserved': True, 'replica_data_matches': True,
            'member_count': len(members), 'http': http(lab), 'crud_pass': True}

def status(lab,node):
    client=connect(lab,node)
    try:
        result=run(client,"sudo mysql -NBe \"SELECT MEMBER_HOST,MEMBER_STATE,MEMBER_ROLE FROM performance_schema.replication_group_members ORDER BY MEMBER_HOST\"",quiet=True)
        return [line.split('\t') for line in result.strip().splitlines()]
    finally:close(client)

def http(lab):
    result={}
    for path in ['/','/articles/high-load/','/health/ready','/static/knowledge/site.css']:
        with urllib.request.urlopen(lab['site_url']+path,timeout=10) as response:
            body=response.read()
            if not body:raise RuntimeError('Empty HTTP response')
            result[path]=response.status
    return result

def database_snapshot(lab,node):
    client=connect(lab,node)
    try:
        sql="""SET SESSION group_replication_consistency='BEFORE';
SELECT VERSION();
SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='otus' AND engine <> 'InnoDB';
SELECT COUNT(*) FROM information_schema.tables t WHERE t.table_schema='otus' AND NOT EXISTS
(SELECT 1 FROM information_schema.table_constraints c WHERE c.table_schema=t.table_schema AND c.table_name=t.table_name AND c.constraint_type='PRIMARY KEY');
SET SESSION group_concat_max_len=1048576;
SELECT COUNT(*),SHA2(GROUP_CONCAT(CONCAT_WS(':',id,slug,title,lesson,summary,body,image,published,DATE_FORMAT(updated_at,'%Y-%m-%dT%H:%i:%s.%f')) ORDER BY id SEPARATOR '|'),256) FROM otus.knowledge_article;"""
        data=run(client,'sudo mysql -NB',sql,quiet=True).strip().splitlines()
        count,digest=data[3].split('\t')
        if data[1]!='0' or data[2]!='0':raise RuntimeError('Non-InnoDB or missing primary key')
        return {'version':data[0],'non_innodb_tables':int(data[1]),'tables_without_pk':int(data[2]),
                'article_count':int(count),'article_sha256':digest}
    finally:close(client)

def verify(lab):
    db_nodes = sorted(n for n in lab['server_ids'] if n != 'app')
    member_count = len(db_nodes)
    report={'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'success':False,'failures':[],
            'region':lab['region'],'admin_ssh_port':lab['admin_ssh_port']}
    client=connect(lab)
    try:
        verify_django_tests(lab, client)
        report['django_tests_passed'] = True
        report['migration']=json.loads(run(client,'sudo /opt/otus-dz05/venv/bin/python /home/otus/dz05/evidence.py prepare',quiet=True))
        report['admin_http']=verify_admin(client)
        report['http_before']=http(lab)
        report['crud_before']=manage(client,'verify_database').strip()
        initial=status(lab,db_nodes[0])
        if len(initial)!=member_count or any(r[1]!='ONLINE' for r in initial):raise RuntimeError('Cluster not fully ONLINE')
        report['initial_members']=initial
        api=NovaClient(auth())
        for role in ['PRIMARY','SECONDARY']:
            current=status(lab,db_nodes[0])
            host=next(r[0] for r in current if r[2]==role)
            node=next(n for n,ip in lab['private_ips'].items() if ip==host)
            uid=lab['server_ids'][node]
            server=api.request('/servers/'+uid)['server']
            if server['name']!=lab['name_prefix']+'-'+node:raise ValueError('Foreign VM: stop refused')
            scenario={'stopped_role':role,'node':node,'probes':[]}
            started=time.monotonic()
            stop_monitor=threading.Event()
            availability=[]
            def monitor():
                while not stop_monitor.is_set():
                    try:
                        with urllib.request.urlopen(lab['site_url']+'/health/ready',timeout=3) as response:
                            availability.append({'seconds':round(time.monotonic()-started,2),'status':response.status})
                    except Exception as error:
                        sample={'seconds':round(time.monotonic()-started,2),'error_type':type(error).__name__}
                        if hasattr(error,'code'):sample['status']=error.code
                        availability.append(sample)
                    stop_monitor.wait(1)
            worker=threading.Thread(target=monitor,daemon=True)
            worker.start()
            survivor=next(n for n in db_nodes if n!=node)
            try:
                api.request('/servers/'+uid+'/action',{'os-stop':None})
                # Wait for actual cloud power-off before judging cluster continuity.
                for _ in range(60):
                    if api.request('/servers/'+uid)['server']['status']=='SHUTOFF':break
                    time.sleep(2)
                else:raise RuntimeError('VM did not power off')
                for attempt in range(60):
                    try:
                        members=status(lab,survivor)
                        live=[r for r in members if r[1]=='ONLINE']
                        if len(live)!=member_count-1 or sum(r[2]=='PRIMARY' for r in live)!=1:raise RuntimeError('Waiting for majority')
                        crud=manage(client,'verify_database').strip()
                        durable=json.loads(run(client,'sudo /opt/otus-dz05/venv/bin/python /home/otus/dz05/evidence.py write '+role,quiet=True))
                        pages=http(lab)
                        scenario.update(seconds_to_success=round(time.monotonic()-started,2),
                                        surviving_members=members,crud=crud,http=pages,durable_write=durable)
                        break
                    except Exception as error:
                        scenario['probes'].append({'attempt':attempt,'error_type':type(error).__name__})
                        time.sleep(2)
                else:raise RuntimeError('No read/write recovery after node failure')
            finally:
                try:api.request('/servers/'+uid+'/action',{'os-start':None})
                finally:
                    stop_monitor.set()
                    worker.join(timeout=5)
                    scenario['readiness_samples']=availability
                for _ in range(120):
                    try:
                        members=status(lab,survivor)
                        if len(members)==member_count and all(r[1]=='ONLINE' for r in members):break
                    except Exception:pass
                    time.sleep(3)
                else:raise RuntimeError('Node failed to rejoin after restart')
                scenario['recovered_members']=members
            report['failures'].append(scenario)
        report['http_after']=http(lab)
        report['crud_after']=manage(client,'verify_database').strip()
        report['replica_snapshots']={node:database_snapshot(lab,node) for node in db_nodes}
        if len({r['article_sha256'] for r in report['replica_snapshots'].values()})!=1:
            raise RuntimeError('Replica article checksums differ')
        report['final_cleanup']=json.loads(run(client,'sudo /opt/otus-dz05/venv/bin/python /home/otus/dz05/evidence.py finish',quiet=True))
        report['success']=True
    finally:
        close(client)
        # UUIDs and addresses are replaced with stable role labels for submission.
        text=json.dumps(report,ensure_ascii=False,indent=2)
        for node,ip in lab['private_ips'].items():text=text.replace(ip,node)
        text=text.replace(lab['app_public_ip'],'APP_PUBLIC_IP')
        text=re.sub(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b','SERVER_UUID',text,flags=re.I)
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        (ROOT/'docs'/('verification-'+stamp+'.json')).write_text(text+'\n',encoding='utf-8')
    print('LIVE VERIFICATION PASSED',flush=True)
