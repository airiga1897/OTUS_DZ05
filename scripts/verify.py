"""Live read/write and VM failure checks; only DZ05 UUIDs from own Terraform output."""
import datetime
import json
from pathlib import Path
import time
import threading
import re
import urllib.request
from remote import connect,close,run,auth,ROOT,LOCAL
from selectel_api import NovaClient
from http_admin import verify_admin

MANAGE="sudo sh -c 'set -a; . /opt/otus-dz05/app.env; set +a; /opt/otus-dz05/venv/bin/python /opt/otus-dz05/app/manage.py "

def manage(client,command):return run(client,MANAGE+command+"'",quiet=True)

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
    report={'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'success':False,'failures':[]}
    client=connect(lab)
    try:
        report['migration']=json.loads(run(client,'sudo /opt/otus-dz05/venv/bin/python /home/otus/dz05/evidence.py prepare',quiet=True))
        report['admin_http']=verify_admin(client)
        report['http_before']=http(lab)
        report['crud_before']=manage(client,'verify_database').strip()
        initial=status(lab,'db1')
        if len(initial)!=3 or any(r[1]!='ONLINE' for r in initial):raise RuntimeError('Cluster not three ONLINE')
        report['initial_members']=initial
        api=NovaClient(auth())
        for role in ['PRIMARY','SECONDARY']:
            current=status(lab,'db1')
            host=next(r[0] for r in current if r[2]==role)
            node=next(n for n,ip in lab['private_ips'].items() if ip==host)
            uid=lab['server_ids'][node]
            server=api.request('/servers/'+uid)['server']
            if server['name']!='otus-dz05-'+node:raise ValueError('Foreign VM: stop refused')
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
            survivor=next(n for n in ['db1','db2','db3'] if n!=node)
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
                        if len(live)!=2 or sum(r[2]=='PRIMARY' for r in live)!=1:raise RuntimeError('Waiting for majority')
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
                    if len(members)==3 and all(r[1]=='ONLINE' for r in members):break
                except Exception:pass
                time.sleep(3)
            else:raise RuntimeError('Node failed to rejoin after restart')
            scenario['recovered_members']=members
            report['failures'].append(scenario)
        report['http_after']=http(lab)
        report['crud_after']=manage(client,'verify_database').strip()
        report['replica_snapshots']={node:database_snapshot(lab,node) for node in ['db1','db2','db3']}
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
