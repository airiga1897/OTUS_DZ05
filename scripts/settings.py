"""Single editable configuration; credentials and deployment results are separate."""
import ipaddress
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local'
CONFIG = LOCAL / 'lab-config.json'
DEPLOYMENT = LOCAL / 'deployment.json'
INPUTS = LOCAL / 'terraform-inputs.json'
STATE = ROOT / 'terraform-selectel/terraform.tfstate'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.new')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def load_config():
    config = read_json(CONFIG)
    if not re.fullmatch(r'[0-9a-f]{32}', config['project_id']):
        raise ValueError('Set project_id in .local/lab-config.json')
    if not config['pools'] or any(p != 'auto' and not re.fullmatch(r'ru-\d+', p) for p in config['pools']):
        raise ValueError('Use Russian Selectel pools or auto in lab-config.json')
    if not re.fullmatch(r'otus-dz05(?:-[a-z0-9-]+)?', config['name_prefix']):
        raise ValueError('The prefix must belong to DZ05')
    subnet = ipaddress.IPv4Network(config['subnet_cidr'], strict=True)
    if not subnet.is_private:
        raise ValueError('Private IPv4 subnet required')
    if set(config['nodes']) != {'app', 'db1', 'db2', 'db3'}:
        raise ValueError('Expected app and three database nodes')
    hosts = [int(n['host']) for n in config['nodes'].values()]
    if len(set(hosts)) != 4 or any(h <= 0 or h >= subnet.num_addresses - 1 for h in hosts):
        raise ValueError('Invalid or duplicate private host offsets')
    if any(n['role'] != ('app' if name == 'app' else 'db') for name, n in config['nodes'].items()):
        raise ValueError('Unexpected node roles')
    if config['admin_ssh_port'] not in (22, 443):
        raise ValueError('Unsupported external SSH port')
    for cidr in config['admin_cidrs']:
        network = ipaddress.IPv4Network(cidr, strict=True)
        if network.prefixlen != 32 or not network.network_address.is_global:
            raise ValueError('Operator rules must be public IPv4 /32')
    if config['segment'] and not re.fullmatch(r'ru-\d+[a-z]', config['segment']):
        raise ValueError('Invalid pool segment')
    return config


def state_resources():
    if not STATE.exists():
        return []
    return [r for r in read_json(STATE).get('resources', [])
            if r.get('mode') == 'managed' and r.get('instances')]


def load_deployment():
    if not DEPLOYMENT.exists():
        raise ValueError('No saved deployment context; do not guess the pool for existing state')
    context = read_json(DEPLOYMENT)
    if context['project_id'] != context['config']['project_id']:
        raise ValueError('Inconsistent deployment project')
    if context.get('state_lineage') and STATE.exists():
        if read_json(STATE).get('lineage') != context['state_lineage']:
            raise ValueError('Terraform state belongs to another deployment context')
    for resource in state_resources():
        for item in resource['instances']:
            attrs = item.get('attributes', {})
            if attrs.get('tenant_id') and attrs['tenant_id'] != context['project_id']:
                raise ValueError('Foreign project found in Terraform state')
            if attrs.get('region') and attrs['region'] != context['region']:
                raise ValueError('Foreign pool found in Terraform state')
    return context


def cloud_config(context=None):
    # Old credential files remain readable, but their project/region are not authoritative.
    credentials = read_json(LOCAL / 'operator.json')
    if context is None:
        if state_resources():
            context = load_deployment()
        elif DEPLOYMENT.exists():
            context = load_deployment()
        else:
            config = load_config()
            explicit = next((p for p in config['pools'] if p != 'auto'), None)
            context = {'project_id': config['project_id'], 'region': explicit}
    return {k: credentials[k] for k in ('auth_url', 'domain_name', 'username', 'password')} | {
        'project_id': context['project_id'], 'region': context.get('region')}


def render_inputs(context):
    """Always regenerate the private Terraform inputs from the saved deployment."""
    write_json(INPUTS, context['terraform'])


def key_path(config):
    path = (ROOT / config['ssh_key_path']).resolve()
    if not path.is_relative_to(LOCAL.resolve()):
        raise ValueError('The SSH key must stay in this project .local directory')
    return path
