"""Reconcile a saved stand additively, without deleting or rebuilding members."""
import copy
import datetime
import json


def resource_identities(resources: list) -> dict:
    result = {}
    for resource in resources:
        for item in resource.get('instances', []):
            suffix = '[' + json.dumps(item['index_key']) + ']' if 'index_key' in item else ''
            address = resource.get('module', '')
            address = (address + '.' if address else '') + resource['type'] + '.' + resource['name'] + suffix
            result[address] = item['attributes']['id']
    return result


def added_nodes(previous: dict, desired: dict) -> list[str]:
    if {k: v for k, v in previous.items() if k != 'nodes'} != {k: v for k, v in desired.items() if k != 'nodes'}:
        raise ValueError('Existing placement and parameters are immutable during expansion')
    if any(desired['nodes'].get(name) != node for name, node in previous['nodes'].items()):
        raise ValueError('Removing or changing an existing node is forbidden')
    return sorted(set(desired['nodes']) - set(previous['nodes']))


def validate_additions(plan: dict, additions: list[str]) -> None:
    expected = {f'{kind}.{label}["{node}"]' for node in additions for kind, label in (
        ('openstack_compute_instance_v2', 'node'), ('openstack_networking_port_v2', 'node'),
        ('openstack_blockstorage_volume_v3', 'boot'))}
    created = set()
    for item in plan.get('resource_changes', []):
        if item.get('mode') != 'managed':
            continue
        change = item['change']
        if change['actions'] == ['create'] and item['address'] in expected:
            created.add(item['address'])
        elif change['actions'] == ['no-op']:
            if item['address'] in expected:
                created.add(item['address'])
            if (change.get('before') or {}).get('id') != (change.get('after') or {}).get('id'):
                raise ValueError('Existing resource identity changed')
        else:
            raise ValueError('Expansion allows only new node resources and no-op existing resources')
    if created != expected:
        raise ValueError('Incomplete new node plan')


def reconcile() -> None:
    import lab
    import remote
    from infrastructure import apply
    from verify import verify_reconcile

    context = lab.load_deployment()
    if context['phase'] not in ('active', 'updating'):
        raise ValueError('Resume initial deployment or destroy before changing membership')
    # Missing recovery files are never silently replaced with newly generated secrets.
    for path in (lab.key_path(context['config']), lab.LOCAL / 'app-secrets.json', lab.LOCAL / 'known_hosts'):
        if not path.is_file():
            raise ValueError('Recovery key, secrets or trusted hosts missing; preserve the existing stand')
    lab.cloud_config(context)  # Enforce session project binding before any mutation.
    desired = lab.load_config()
    added_nodes(context['config'], desired)
    baseline_path = lab.LOCAL / 'reconcile-baseline.json'
    if context['phase'] == 'active':
        inventory = lab.saved_lab()
        remote.assert_online(inventory)
        baseline = {'inventory': inventory, 'data': remote.capture_data(inventory),
                    'configured_nodes': sorted(inventory['server_ids']),
                    'resource_ids': resource_identities(lab.state_resources())}
        lab.write_json(baseline_path, baseline)
    else:
        baseline = lab.read_json(baseline_path)
        # A failed apply/configuration resumes the same desired topology only.
        if desired != context['config']:
            raise ValueError('Finish the pending expansion before editing configuration again')
    baseline = lab.read_json(baseline_path)
    configured = baseline['configured_nodes']
    new_nodes = sorted(set(desired['nodes']) - set(configured))
    if context['phase'] == 'active':
        context = copy.deepcopy(context)
        context['config'] = copy.deepcopy(desired)
        context['terraform']['nodes'] = copy.deepcopy(desired['nodes'])
        context['phase'] = 'updating'
        lab.save_context(context)
    try:
        if new_nodes:
            delta = copy.deepcopy(context)
            delta['config']['nodes'] = {n: desired['nodes'][n] for n in new_nodes}
            discovery = lab.collect(context)
            own_ids = {i['attributes']['id'] for r in lab.state_resources()
                       if r['type'] == 'openstack_compute_instance_v2' for i in r['instances']}
            names = {desired['name_prefix'] + '-' + n for n in new_nodes}
            if any(s['name'] in names and s['id'] not in own_ids for s in discovery['servers']['servers']):
                raise ValueError('Added VM name already exists outside this state')
            lab.sufficient(delta, discovery, incremental=True)
        # On a retry some of the new resources already exist and must be no-op.
        def validate(plan, context):
            lab.validate_plan(plan, context=context)
            validate_additions(plan, new_nodes)
            changes = {r['address']: r['change'] for r in plan.get('resource_changes', []) if r.get('mode') == 'managed'}
            for address, uid in baseline['resource_ids'].items():
                change = changes.get(address, {})
                if change.get('actions') != ['no-op'] or (change.get('before') or {}).get('id') != uid:
                    raise ValueError('Existing resource identity or configuration differs from baseline')
        apply(context, lab.tf, validate, lab.LOCAL / 'expand.tfplan')
        after_ids = resource_identities(lab.state_resources())
        if any(after_ids.get(address) != uid for address, uid in baseline['resource_ids'].items()):
            raise ValueError('Existing resource identity changed after apply')
        inventory = lab.publish_inventory(context)
        for name, uid in baseline['inventory']['server_ids'].items():
            if inventory['server_ids'].get(name) != uid or inventory['private_ips'].get(name) != baseline['inventory']['private_ips'][name]:
                raise ValueError('An existing VM or address changed')
        if inventory['app_public_ip'] != baseline['inventory']['app_public_ip']:
            raise ValueError('Application public address changed')
        remote.configure(inventory, configured_nodes=configured)
        report = verify_reconcile(inventory, baseline)
        report['existing_resource_ids_preserved'] = True
        context['phase'] = 'active'
        lab.save_context(context)
        lab.write_json(lab.ROOT / 'docs/expansion-latest.json', {
            'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'success': True, 'added_nodes': new_nodes, **report})
        baseline_path.unlink()
    except Exception:
        # Never destroy an existing stand after failed expansion.
        raise
