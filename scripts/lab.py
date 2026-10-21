"""DZ05 lifecycle with immutable placement for verify/destroy and safe pool fallback."""
import argparse
import datetime
import ipaddress
import json
import shutil
import subprocess
import sys
import time
from selectel_api import authenticate, request_json, NovaClient
from settings import (ROOT, LOCAL, CONFIG, DEPLOYMENT, INPUTS, STATE, load_config, load_deployment,
                      cloud_config, read_json, write_json, state_resources, key_path)
from tf import operator_env
from placement import candidates, endpoints_for, CandidateUnavailable
from preflight import collect, sufficient
from prepare import ensure_local, context_for, save_context, reject_legacy_inputs

TFDIR = ROOT / 'terraform-selectel'


class TerraformError(RuntimeError):
    def __init__(self, command, output):
        super().__init__('Terraform failed: ' + command)
        self.output = output


def tf(args, capture=False):
    args = list(args)
    if args[0] == 'plan':
        args += ['-var-file=' + str(INPUTS)]
    result = subprocess.run([str(ROOT / '.tools/terraform.exe'), '-chdir=' + str(TFDIR)] + args,
                            env=operator_env(), text=True, encoding='utf-8', errors='replace',
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    with (LOCAL / 'terraform.log').open('a', encoding='utf-8') as log:
        log.write(result.stdout)
    if result.returncode:
        print(result.stdout[-4000:], flush=True)
        raise TerraformError(args[0], result.stdout)
    if not capture:
        print(result.stdout[-1200:], flush=True)
    return result.stdout


def validate_plan(plan, destroy=False, context=None):
    context = context or load_deployment()
    config = context['config']
    prefix = config['name_prefix']
    names = {prefix, prefix + '-db'} | {prefix + '-' + n + suffix
            for n in config['nodes'] for suffix in ('', '-boot')}
    allowed = {'openstack_blockstorage_volume_v3', 'openstack_compute_instance_v2',
               'openstack_compute_servergroup_v2', 'openstack_networking_network_v2',
               'openstack_networking_subnet_v2', 'openstack_networking_router_v2',
               'openstack_networking_router_interface_v2', 'openstack_networking_secgroup_v2',
               'openstack_networking_secgroup_rule_v2', 'openstack_networking_port_v2',
               'openstack_networking_floatingip_v2', 'openstack_networking_floatingip_associate_v2'}
    changes = [r for r in plan.get('resource_changes', []) if r.get('mode') == 'managed']
    for resource in changes:
        change = resource['change']
        actions = [['delete'], ['no-op']] if destroy else [['create'], ['no-op']]
        if resource['type'] not in allowed or change['actions'] not in actions:
            raise ValueError('Unexpected action or resource in own plan: ' + resource['address'])
        values = change['before' if destroy else 'after'] or {}
        if values.get('name') and values['name'] not in names:
            raise ValueError('Foreign resource name: ' + resource['address'])
        if values.get('region') and values['region'] != context['region']:
            raise ValueError('Foreign pool in Terraform plan')
        if values.get('tenant_id') and values['tenant_id'] != context['project_id']:
            raise ValueError('Foreign project in Terraform plan')
        if resource['type'] == 'openstack_networking_subnet_v2' and values.get('cidr') != config['subnet_cidr']:
            raise ValueError('Foreign subnet')
    if not destroy and sum(r['type'] == 'openstack_compute_instance_v2' for r in changes) != len(config['nodes']):
        raise ValueError('Expected every configured DZ05 instance')


def check_existing(context, discovery):
    prefix = context['config']['name_prefix']
    subnet = ipaddress.ip_network(context['config']['subnet_cidr'])
    if any(ipaddress.ip_network(s['cidr']).overlaps(subnet)
           for s in discovery['subnets']['subnets'] if s.get('ip_version') == 4):
        raise CandidateUnavailable('Configured subnet overlaps an existing subnet')
    if any(s['name'].startswith(prefix) for s in discovery['servers']['servers']):
        raise ValueError('Existing DZ05 VMs outside own state; inspect before deployment')
    if any(n['name'].startswith(prefix) for n in discovery['networks']['networks']):
        raise ValueError('Existing DZ05 network outside own state; inspect before deployment')


def scheduler_unavailable(error, context):
    text = error.output.lower()
    if 'no valid host' in text or 'novalidhost' in text:
        return True
    api = NovaClient(cloud_config(context))
    found = False
    for resource in state_resources():
        if resource['type'] != 'openstack_compute_instance_v2':
            continue
        for instance in resource['instances']:
            node = instance.get('index_key')
            expected = context['config']['name_prefix'] + '-' + str(node)
            attributes = instance['attributes']
            if attributes.get('name') != expected or node not in context['config']['nodes']:
                raise ValueError('Foreign instance diagnostics refused')
            server = api.request('/servers/' + attributes['id'])['server']
            if server['name'] != expected:
                raise ValueError('Foreign instance returned by Nova')
            message = server.get('fault', {}).get('message', '').lower()
            found |= 'no valid host' in message or 'novalidhost' in message or 'not enough hosts' in message
    return found


def deploy(test_run_id=None):
    lock = ROOT / '.test-cycle.lock'
    if lock.exists() and lock.read_text(encoding='utf-8') != test_run_id:
        raise ValueError('A temporary test owns this clone; recover it before ordinary deploy')
    if state_resources():
        if test_run_id and load_deployment().get('test_run_id') != test_run_id:
            raise ValueError('Existing deployment is not owned by this temporary run')
        from growth import reconcile
        return reconcile()
    if DEPLOYMENT.exists() and load_deployment()['phase'] not in ('prepared', 'destroyed'):
        raise ValueError('Previous deployment needs cleanup; run destroy before selecting another pool')
    reject_legacy_inputs()
    config = load_config()
    ensure_local(config)
    attempts = []
    for placement in candidates(config):
        context = context_for(config, placement)
        if test_run_id:
            context['test_run_id'] = test_run_id
        label = placement['region'] + '/' + placement['availability_zone']
        try:
            discovery = collect(context)
            sufficient(context, discovery)
            check_existing(context, discovery)
        except CandidateUnavailable as error:
            print(label + ': ' + str(error) + '; no resources created, skipped.', flush=True)
            attempts.append({'pool': placement['region'], 'segment': placement['availability_zone'], 'result': 'preflight_unavailable'})
            write_json(LOCAL / 'pool-attempts.json', attempts)
            continue
        print('Selected deployment candidate ' + label, flush=True)
        context['phase'] = 'creating'
        save_context(context)
        plan = LOCAL / 'create.tfplan'
        try:
            from infrastructure import apply
            apply(context, tf, validate_plan, plan)
        except TerraformError as error:
            retry = scheduler_unavailable(error, context)
            # Clean before considering another pool, including partial apply failures.
            if not retry:
                raise
            if test_run_id:
                destroy(clean_local=False, expected_run_id=test_run_id)
            else:
                destroy(clean_local=False)
            attempts.append({'pool': placement['region'], 'segment': placement['availability_zone'],
                             'result': 'scheduler_unavailable', 'cleanup_success': True})
            write_json(LOCAL / 'pool-attempts.json', attempts)
            print(label + ': no suitable physical hosts; cleanup confirmed, trying next candidate.', flush=True)
            continue
        context['state_lineage'] = read_json(STATE)['lineage']
        context['phase'] = 'configuring'
        save_context(context)
        inventory = publish_inventory(context)
        discover_route(inventory)
        from remote import configure
        configure(inventory)
        context = load_deployment()  # route discovery may have added a confirmed /32
        context['phase'] = 'active'
        save_context(context)
        attempts.append({'pool': context['region'], 'segment': context['availability_zone'], 'result': 'active'})
        write_json(LOCAL / 'pool-attempts.json', attempts)
        return
    raise RuntimeError('No configured pool/segment could place the complete DZ05 stand; no failed attempt is left allocated')


def publish_inventory(context):
    inventory = json.loads(tf(['output', '-json', 'lab'], True))
    config = context['config']
    inventory.update(project_id=context['project_id'], region=context['region'],
                     name_prefix=config['name_prefix'], subnet_cidr=config['subnet_cidr'],
                     deployment_id=context['deployment_id'], ssh_key_path=str(key_path(config)))
    write_json(LOCAL / 'lab.json', inventory)
    return inventory


def discover_route(inventory):
    import hashlib
    import hmac
    import secrets
    import time
    import urllib.request
    context = load_deployment()
    key = context['terraform']['route_probe_key'].encode()
    nonce = secrets.token_hex(24)
    for attempt in range(30):
        try:
            with urllib.request.urlopen(inventory['site_url'] + '/?nonce=' + nonce, timeout=3) as response:
                result = json.load(response)
            expected = hmac.new(key, (nonce + '|' + result['ip']).encode(), hashlib.sha256).hexdigest()
            if result['nonce'] != nonce or not hmac.compare_digest(expected, result['signature']):
                raise ValueError('Untrusted route probe response')
            address = ipaddress.IPv4Address(result['ip'])
            if not address.is_global:
                raise ValueError('Invalid public route address')
            break
        except (OSError, json.JSONDecodeError):
            time.sleep(2)
    else:
        raise RuntimeError('Direct HTTP route probe unavailable')
    cidr = str(address) + '/32'
    changed = cidr not in context['terraform']['admin_cidrs']
    if changed:
        context['terraform']['admin_cidrs'].append(cidr)
        save_context(context)
        plan = LOCAL / 'route.tfplan'
        tf(['plan', '-input=false', '-no-color', '-out=' + str(plan)])
        validate_plan(json.loads(tf(['show', '-json', str(plan)], True)))
        tf(['apply', '-input=false', '-no-color', str(plan)])
    write_json(ROOT / 'docs/route-check.json', {'signed_response_verified': True,
               'additional_operator_rule_created': changed, 'ssh_access': 'confirmed operator IPv4 /32 entries only'})
    print('Direct route verified; operator SSH /32 rule ' + ('added' if changed else 'already present'), flush=True)


def saved_lab():
    context = load_deployment()
    if context['phase'] != 'active' or not state_resources():
        raise ValueError('Verification requires the active saved deployment')
    inventory = read_json(LOCAL / 'lab.json')
    if inventory.get('deployment_id') != context['deployment_id']:
        raise ValueError('Stale lab.json; deployment identity mismatch')
    ids = {i['index_key']: i['attributes']['id'] for r in state_resources()
           if r['type'] == 'openstack_compute_instance_v2' for i in r['instances']}
    if inventory['server_ids'] != ids:
        raise ValueError('VM identities differ from own Terraform state')
    return inventory


def inventory_snapshot(endpoints, token, prefix, own_fips):
    remaining = {}
    for name, service, path, key in [('servers', 'compute', '/servers/detail', 'servers'),
        ('volumes', 'volumev3', '/volumes/detail', 'volumes'), ('networks', 'network', '/v2.0/networks', 'networks'),
        ('subnets', 'network', '/v2.0/subnets', 'subnets'), ('routers', 'network', '/v2.0/routers', 'routers'),
        ('ports', 'network', '/v2.0/ports', 'ports'), ('security_groups', 'network', '/v2.0/security-groups', 'security_groups'),
        ('server_groups', 'compute', '/os-server-groups', 'server_groups')]:
        data, _ = request_json(endpoints[service] + path, token=token)
        remaining[name] = sum(str(r.get('name', '')).startswith(prefix) for r in data[key])
    fips, _ = request_json(endpoints['network'] + '/v2.0/floatingips', token=token)
    remaining['own_floating_ips'] = sum(f['id'] in own_fips for f in fips['floatingips'])
    return remaining


def await_inventory_empty(endpoints, token, prefix, own_fips):
    observations = []
    for attempt in range(12):
        remaining = inventory_snapshot(endpoints, token, prefix, own_fips)
        observations.append(remaining)
        write_json(LOCAL / 'cleanup-observations.json', observations)
        if not any(remaining.values()):
            return remaining
        if attempt < 11:
            time.sleep(2)
    return remaining


def cleanup_local_data() -> None:
    """Discard this project's runtime data after state and API cleanup succeeded."""
    if state_resources():
        raise ValueError('Cannot remove local data while managed state remains')
    from local_runtime import cleanup_runtime
    cleanup_runtime(ROOT)


def destroy(clean_local: bool = True, expected_run_id=None):
    if not DEPLOYMENT.exists():
        if state_resources():
            raise ValueError('State exists without saved placement; refusing to guess project/pool')
        print('No saved deployment and no managed state; nothing was allocated by this configuration.', flush=True)
        return
    context = load_deployment()
    if expected_run_id is not None and context.get('test_run_id') != expected_run_id:
        raise ValueError('Refusing to destroy a stand owned by another test run')
    plan = LOCAL / 'destroy.tfplan'
    tf(['plan', '-destroy', '-input=false', '-no-color', '-out=' + str(plan)])
    data = json.loads(tf(['show', '-json', str(plan)], True))
    validate_plan(data, True, context)
    own_fips = set(context.get('floating_ip_ids', [])) | {
        r['change']['before']['id'] for r in data.get('resource_changes', [])
        if r.get('mode') == 'managed' and r['type'] == 'openstack_networking_floatingip_v2' and r['change']['before']}
    context.update(phase='cleanup_pending', floating_ip_ids=sorted(own_fips))
    save_context(context)
    tf(['apply', '-input=false', '-no-color', str(plan)])
    if state_resources():
        raise RuntimeError('Managed resources remain in own state')
    token, catalog = authenticate(cloud_config(context))
    endpoints = endpoints_for(catalog, context['region'])
    prefix = context['config']['name_prefix']
    remaining = await_inventory_empty(endpoints, token, prefix, own_fips)
    write_json(ROOT / 'docs/cleanup-latest.json', {'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'region': context['region'], 'own_state_empty': True, 'remaining_by_prefix': remaining,
               'foreign_resources_modified': False})
    if any(remaining.values()):
        raise RuntimeError('Own resources remain in selected pool; do not switch pools or delete foreign resources')
    context['phase'] = 'destroyed'
    save_context(context)
    if clean_local:
        cleanup_local_data()
        lock = ROOT / '.test-cycle.lock'
        if expected_run_id is None and context.get('test_run_id') and lock.exists():
            if lock.read_text(encoding='utf-8') == context['test_run_id']:
                lock.unlink()  # Explicit recovery destroy releases its own abandoned test lock.
    print('DZ05 cleanup: own state and selected-pool API inventory empty.', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['deploy', 'verify', 'open', 'destroy', 'test-cycle'])
    args = parser.parse_args()
    if args.command == 'test-cycle':
        from test_cycle import run
        return run()
    LOCAL.mkdir(exist_ok=True)
    if args.command == 'open':
        from local_runtime import open_portal
        open_portal(ROOT, 5)
    elif args.command == 'deploy':
        tf(['init', '-input=false', '-no-color'])
        deploy()
    elif args.command == 'destroy':
        destroy()
    elif args.command == 'verify':
        from verify import verify
        verify(saved_lab())


if __name__ == '__main__':
    main()
