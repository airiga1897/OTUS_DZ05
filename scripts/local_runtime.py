"""Local lifecycle operations; never authenticate or contact a cloud service."""
import ipaddress
import json
from pathlib import Path
import shutil
import webbrowser


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def linked(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def checked_tree(path, root):
    if linked(path) or not path.resolve().is_relative_to(root):
        raise ValueError('Runtime path must stay inside this clone without links')
    if path.is_dir():
        for child in path.iterdir():
            checked_tree(child, root)


def cleanup_runtime(root):
    """Call only after successful state AND cloud API deletion audits."""
    root = Path(root).resolve()
    local = root / '.local'
    state = root / 'terraform-selectel/terraform.tfstate'
    alternate = root / 'terraform/terraform.tfstate'
    checked_tree(state, root)
    checked_tree(alternate, root)
    if alternate.exists() and any(r.get('mode') == 'managed' and r.get('instances')
                                 for r in read(alternate).get('resources', [])):
        raise ValueError('Another deployment still needs local recovery files')
    if state.exists() and any(r.get('mode') == 'managed' and r.get('instances')
                              for r in read(state).get('resources', [])):
        raise ValueError('Managed state remains; preserve recovery files')
    paths = [p for p in local.iterdir() if p.name not in {'operator.json', 'lab-config.json'}] if local.exists() else []
    paths += list((root / 'terraform-selectel').glob('terraform.tfstate*'))
    paths += list((root / 'terraform-selectel').glob('*.tfplan'))
    paths += [p for p in (root / 'terraform-selectel').glob('terraform.tfvars*')
              if p.name != 'terraform.tfvars.example.json' and p.suffix == '.json']
    # Validate every target before removing any recovery file.
    checked_tree(local, root)
    for path in paths:
        checked_tree(path, root)
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def validate_state(root, config, prefix):
    """Fail closed on foreign project, pool, provider, or resource namespace."""
    state = Path(root) / 'terraform-selectel/terraform.tfstate'
    checked_tree(state, Path(root).resolve())
    if not state.exists():
        return
    for resource in read(state).get('resources', []):
        if resource.get('mode') != 'managed' or not resource.get('instances'):
            continue
        if not resource.get('type', '').startswith('openstack_'):
            raise ValueError('Foreign provider in state')
        for item in resource['instances']:
            attrs = item.get('attributes', {})
            name = attrs.get('name')
            if name and name != prefix and not name.startswith(prefix + '-'):
                raise ValueError('Foreign resource in state')
            if attrs.get('tenant_id') and attrs['tenant_id'] != config['project_id']:
                raise ValueError('Foreign project in state')
            if attrs.get('region') and attrs['region'] != config['region']:
                raise ValueError('Foreign pool in state')


def open_portal(root, homework, opener=None):
    """Validate saved identities against local state; no cloud credentials needed."""
    root = Path(root)
    local = root / '.local'
    try:
        checked_tree(root / 'terraform-selectel/terraform.tfstate', root.resolve())
        checked_tree(local / {5: 'deployment.json', 6: 'context.json', 7: 'selectel-context.json'}[homework], root.resolve())
        checked_tree(local / 'lab.json', root.resolve())
        state = read(root / 'terraform-selectel/terraform.tfstate')
        resources = [r for r in state.get('resources', []) if r.get('mode') == 'managed' and r.get('instances')]
        if not resources:
            raise ValueError('No active managed state')
        context = read(local / {5: 'deployment.json', 6: 'context.json', 7: 'selectel-context.json'}[homework])
        if homework == 5 and context.get('phase') != 'active':
            raise ValueError('Deployment is not active')
        if context.get('state_lineage') and context['state_lineage'] != state.get('lineage'):
            raise ValueError('Stale deployment lineage')
        output = state['outputs']['lab']['value']
        saved = read(local / 'lab.json') if homework != 6 else output
        if homework == 5 and saved.get('deployment_id') != context.get('deployment_id'):
            raise ValueError('Stale deployment identity')
        ids = {}
        for resource in resources:
            for item in resource['instances']:
                attrs = item.get('attributes', {})
                if attrs.get('tenant_id') and attrs['tenant_id'] != context['project_id']:
                    raise ValueError('Foreign project in state')
                if attrs.get('region') and attrs['region'] != context['region']:
                    raise ValueError('Foreign region in state')
                name = attrs.get('name')
                prefix = context.get('config', {}).get('name_prefix', f'otus-dz0{homework}')
                if name and name != prefix and not name.startswith(prefix + '-'):
                    raise ValueError('Foreign resource in state')
                if resource['type'] == 'openstack_compute_instance_v2':
                    ids[item['index_key']] = attrs['id']
        expected = {name: node['id'] for name, node in saved.items()} if homework == 7 else saved['server_ids']
        output_ids = {name: node['id'] for name, node in output.items()} if homework == 7 else output['server_ids']
        if not ids or ids != expected or ids != output_ids:
            raise ValueError('Stale VM identities')
        address = saved['es3']['public_ip'] if homework == 7 else saved['app_public_ip']
        current_address = output['es3']['public_ip'] if homework == 7 else output['app_public_ip']
        if address != current_address:
            raise ValueError('Stale portal address')
        ipaddress.IPv4Address(address)
        url = 'http://' + address
        region = context['region']
        zone = context.get('availability_zone')
        if not zone:
            variables = read(root / 'terraform-selectel/terraform.tfvars.json')
            zone = variables['availability_zone']
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError('Нет действующего локального контекста портала: проверьте context, state и завершение deploy; сохраните данные для восстановления.') from error
    print(f'Placement: {region}/{zone}\nPortal: {url}', flush=True)
    if not (opener or webbrowser.open)(url):
        print('Браузер не открылся автоматически; используйте URL выше.', flush=True)
    return url
