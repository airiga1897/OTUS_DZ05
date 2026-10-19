"""DZ05 lifecycle: deploy, verify, destroy, or a test cycle with mandatory finally cleanup."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import ipaddress
from selectel_api import authenticate,request_json

from tf import operator_env

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local'
TFDIR = ROOT / 'terraform-selectel'

def tf(args, capture=False):
    binary = ROOT / '.tools/terraform.exe'
    if not binary.exists():
        binary = ROOT.parent / 'OTUS_DZ04/.tools/terraform.exe'
    cmd = [str(binary), '-chdir=' + str(TFDIR)] + args
    result = subprocess.run(cmd, env=operator_env(), text=True, encoding='utf-8',
                            errors='replace', stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    with (LOCAL / 'terraform.log').open('a', encoding='utf-8') as log:
        log.write(result.stdout)
    if result.returncode:
        print(result.stdout[-5000:])
        raise RuntimeError('Terraform failed: ' + args[0])
    if not capture:
        print(result.stdout[-1200:], flush=True)
    return result.stdout

def validate_plan(plan, destroy=False):
    changes = [r for r in plan.get('resource_changes', []) if r.get('mode') == 'managed']
    allowed = {'openstack_blockstorage_volume_v3', 'openstack_compute_instance_v2',
               'openstack_compute_servergroup_v2', 'openstack_networking_network_v2',
               'openstack_networking_subnet_v2', 'openstack_networking_router_v2',
               'openstack_networking_router_interface_v2', 'openstack_networking_secgroup_v2',
               'openstack_networking_secgroup_rule_v2', 'openstack_networking_port_v2',
               'openstack_networking_floatingip_v2', 'openstack_networking_floatingip_associate_v2'}
    for r in changes:
        if r['type'] not in allowed or r['change']['actions'] not in ([['delete'], ['no-op']] if destroy else [['create'], ['no-op']]):
            raise ValueError('Unexpected action or resource in own state: ' + r['address'])
        values = r['change']['before' if destroy else 'after'] or {}
        if values.get('name') and not values['name'].startswith('otus-dz05'):
            raise ValueError('Foreign resource name: ' + r['address'])
        if r['type'] == 'openstack_networking_subnet_v2' and values.get('cidr') != '10.55.0.0/24':
            raise ValueError('Foreign subnet')
    if not destroy and sum(r['type'] == 'openstack_compute_instance_v2' for r in changes) != 4:
        raise ValueError('Expected exactly four DZ05 instances')

def deploy():
    state_path=TFDIR/'terraform.tfstate'
    if state_path.exists():
        state=json.loads(state_path.read_text(encoding='utf-8-sig'))
        if any(r.get('mode')=='managed' and r.get('instances') for r in state.get('resources',[])):
            raise ValueError('Existing DZ05 resources: use verify or destroy before a fresh deploy; clone bootstrap is for new nodes only')
    if not (LOCAL/'reconstructed-portal.json').exists():
        subprocess.run([sys.executable,str(ROOT/'scripts/reconstruct.py')],check=True)
    subprocess.run([sys.executable, str(ROOT/'scripts/preflight.py'), '--auth-json', str(LOCAL/'operator.json')], check=True)
    report = json.loads((ROOT/'docs/preflight.json').read_text())
    if report['errors']:
        raise ValueError('Quota checks incomplete')
    discovery=json.loads((LOCAL/'discovery.json').read_text())
    if any(ipaddress.ip_network(s['cidr']).overlaps(ipaddress.ip_network('10.55.0.0/24'))
           for s in discovery['subnets']['subnets']
           if s.get('ip_version')==4 and not s.get('name','').startswith('otus-dz05')):
        raise ValueError('DZ05 subnet overlaps a foreign subnet')
    q = report['quotas']
    def capacity(item, need, used='in_use'):
        return item['limit'] < 0 or item['limit'] - item.get(used, 0) - item.get('reserved', 0) >= need
    az = q['compute']['quota_set']['az_quotas']['ru-9a']
    # Conservative reservation for both DZ05 and DZ06, even if DZ06 is already present.
    if not all(capacity(az[k], n) for k,n in {'cores':15,'ram':28672,'instances':8}.items()):
        raise ValueError('Insufficient combined compute quota')
    if not capacity(q['volumes']['quota_set']['gigabytes_basicssd.ru-9a'],160):
        raise ValueError('Insufficient combined SSD quota')
    if not all(capacity(q['network']['quota'][k],n,'used') for k,n in {'floatingip':2,'network':2,'subnet':2,'router':2,'port':12,'security_group':2}.items()):
        raise ValueError('Insufficient combined network quota')
    plan = LOCAL/'create.tfplan'
    tf(['plan','-input=false','-no-color','-out='+str(plan)])
    validate_plan(json.loads(tf(['show','-json',str(plan)],True)))
    tf(['apply','-input=false','-no-color',str(plan)])
    lab = json.loads(tf(['output','-json','lab'],True))
    (LOCAL/'lab.json').write_text(json.dumps(lab,indent=2),encoding='utf-8')
    discover_route(lab)
    from remote import configure
    configure(lab)

def discover_route(lab):
    import hashlib,hmac,secrets,time,urllib.request
    path=TFDIR/'terraform.tfvars.json'
    variables=json.loads(path.read_text())
    key=variables['route_probe_key'].encode()
    nonce=secrets.token_hex(24)
    for attempt in range(30):
        try:
            with urllib.request.urlopen(lab['site_url']+'/?nonce='+nonce,timeout=3) as response:
                result=json.load(response)
            expected=hmac.new(key,(nonce+'|'+result['ip']).encode(),hashlib.sha256).hexdigest()
            if result['nonce']!=nonce or not hmac.compare_digest(expected,result['signature']):
                raise ValueError('Untrusted route probe response')
            address=ipaddress.IPv4Address(result['ip'])
            if not address.is_global:raise ValueError('Invalid public route address')
            break
        except (OSError,json.JSONDecodeError):time.sleep(2)
    else:raise RuntimeError('Direct HTTP route probe unavailable')
    cidr=str(address)+'/32'
    changed=cidr not in variables['admin_cidrs']
    if changed:
        variables['admin_cidrs'].append(cidr)
        path.write_text(json.dumps(variables,indent=2)+'\n',encoding='utf-8')
        plan=LOCAL/'route.tfplan'
        tf(['plan','-input=false','-no-color','-out='+str(plan)])
        validate_plan(json.loads(tf(['show','-json',str(plan)],True)))
        tf(['apply','-input=false','-no-color',str(plan)])
    (ROOT/'docs/route-check.json').write_text(json.dumps({'signed_response_verified':True,
           'additional_operator_rule_created':changed,'ssh_access':'confirmed operator IPv4 /32 entries only'},indent=2)+'\n')
    print('Direct route verified; operator SSH /32 rule '+('added' if changed else 'already present'),flush=True)

def destroy():
    plan = LOCAL/'destroy.tfplan'
    tf(['plan','-destroy','-input=false','-no-color','-out='+str(plan)])
    data=json.loads(tf(['show','-json',str(plan)],True))
    validate_plan(data,True)
    own_fips={r['change']['before']['id'] for r in data.get('resource_changes',[])
              if r.get('mode')=='managed' and r['type']=='openstack_networking_floatingip_v2' and r['change']['before']}
    tf(['apply','-input=false','-no-color',str(plan)])
    state = json.loads(tf(['show','-json'],True) or '{}')
    if any(r.get('mode') == 'managed' for r in state.get('values',{}).get('root_module',{}).get('resources',[])):
        raise RuntimeError('Managed resources remain in DZ05 state')
    print('DZ05 cleanup: own Terraform state has no managed resources.',flush=True)
    config=json.loads((LOCAL/'operator.json').read_text(encoding='utf-8-sig'))
    token,catalog=authenticate(config)
    endpoints={s['type']:e['url'].rstrip('/') for s in catalog for e in s['endpoints']
               if e.get('interface')=='public' and e.get('region')=='ru-9' and e['url'].startswith('https://')}
    remaining={}
    for name,service,path,key in [('servers','compute','/servers/detail','servers'),
                                  ('volumes','volumev3','/volumes/detail','volumes'),
                                  ('networks','network','/v2.0/networks','networks'),
                                  ('subnets','network','/v2.0/subnets','subnets'),
                                  ('routers','network','/v2.0/routers','routers'),
                                  ('ports','network','/v2.0/ports','ports'),
                                  ('security_groups','network','/v2.0/security-groups','security_groups'),
                                  ('server_groups','compute','/os-server-groups','server_groups')]:
        data,_=request_json(endpoints[service]+path,token=token)
        remaining[name]=sum(str(r.get('name','')).startswith('otus-dz05') for r in data[key])
    fips,_=request_json(endpoints['network']+'/v2.0/floatingips',token=token)
    remaining['own_floating_ips']=sum(f['id'] in own_fips for f in fips['floatingips'])
    report={'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'own_state_empty':True,'remaining_by_prefix':remaining,
            'foreign_resources_modified':False}
    (ROOT/'docs/cleanup-latest.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    if any(remaining.values()):raise RuntimeError('DZ05 resources remain in cloud inventory; inspect, do not delete foreign resources')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['deploy','verify','destroy','test-cycle'])
    args=parser.parse_args()
    LOCAL.mkdir(exist_ok=True)
    if args.command=='deploy': deploy()
    elif args.command=='destroy': destroy()
    elif args.command=='verify':
        from verify import verify
        verify(json.loads((LOCAL/'lab.json').read_text()))
    else:
        result={'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'success':False}
        try:
            deploy()
            from verify import verify
            verify(json.loads((LOCAL/'lab.json').read_text()))
            result['success']=True
        except Exception as error:
            result['error_type']=type(error).__name__
            print('Test cycle failed: '+str(error)+'; mandatory DZ05 cleanup follows.',flush=True)
            raise
        finally:
            try:
                destroy()
                result['cleanup_success']=True
            except Exception:
                result['cleanup_success']=False
                raise
            finally:
                result['finished_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
                stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                (ROOT/'docs'/('cycle-'+stamp+'.json')).write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':
    main()
