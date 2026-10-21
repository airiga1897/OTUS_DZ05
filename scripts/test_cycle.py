"""Destructive temporary acceptance run, with explicit deployment ownership."""
import copy
import datetime
import ipaddress
import uuid


def assert_empty(lab) -> None:
    if lab.state_resources() or lab.DEPLOYMENT.exists() or (lab.LOCAL / 'lab.json').exists():
        raise ValueError('test-cycle requires no existing stand, state or unfinished operation')
    if (lab.LOCAL / 'reconcile-baseline.json').exists() or (lab.ROOT / '.test-cycle.lock').exists():
        raise ValueError('An unfinished operation requires recovery before test-cycle')
    for relative in ('terraform-selectel/terraform.tfstate', 'terraform/terraform.tfstate'):
        path = lab.ROOT / relative
        if path.exists():
            state = lab.read_json(path)
            if state.get('resources') or state.get('outputs'):
                raise ValueError('Nonempty state must be inspected before test-cycle')


def run() -> None:
    import lab
    import remote
    from verify import verify, manage, verify_cycle_control

    assert_empty(lab)  # Before init, config writes, keys, or any cloud operation.
    config = lab.load_config()
    desired = copy.deepcopy(config)
    node = next((f'db{i}' for i in range(1, 10) if f'db{i}' not in desired['nodes']), None)
    network = ipaddress.IPv4Network(config['subnet_cidr'])
    used = {v['host'] for v in config['nodes'].values()}
    host = next((h for h in range(2, network.num_addresses - 1) if h not in used), None)
    if node is None or host is None:
        raise ValueError('The temporary test needs room for one additional member')
    desired['nodes'][node] = {'host': host, 'role': 'db'}
    run_id = uuid.uuid4().hex
    lock = lab.ROOT / '.test-cycle.lock'
    with lock.open('x', encoding='utf-8') as file:
        file.write(run_id)
    result = {'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'success': False, 'cleanup_success': False, 'steps': []}
    try:
        lab.tf(['init', '-input=false', '-no-color'])
        lab.deploy(test_run_id=run_id)
        result['steps'].append('initial_deployment')
        verify(lab.saved_lab())
        inventory = lab.saved_lab()
        existing_ids = copy.deepcopy(inventory['server_ids'])
        client = remote.connect(inventory)
        try:
            manage(client, 'create_cycle_control ' + run_id)
        finally:
            remote.close(client)
        control = remote.capture_data(inventory)
        control_http = verify_cycle_control(inventory, run_id)
        result['steps'].append('control_article_and_media')
        lab.deploy(test_run_id=run_id)
        if remote.capture_data(lab.saved_lab()) != control:
            raise RuntimeError('Control data changed during repeated deployment')
        if verify_cycle_control(lab.saved_lab(), run_id) != control_http:
            raise RuntimeError('Control media changed over HTTP after repeated deployment')
        result['steps'].append('repeated_deployment')
        lab.write_json(lab.CONFIG, desired)
        lab.deploy(test_run_id=run_id)
        expanded = lab.saved_lab()
        if any(expanded['server_ids'].get(n) != uid for n, uid in existing_ids.items()):
            raise RuntimeError('An original VM was recreated')
        if node not in expanded['server_ids'] or remote.capture_data(expanded) != control:
            raise RuntimeError('Expansion did not preserve control rows and media')
        if verify_cycle_control(expanded, run_id) != control_http:
            raise RuntimeError('Control media changed over HTTP after expansion')
        expansion = lab.read_json(lab.ROOT / 'docs/expansion-latest.json')
        if not expansion['success'] or expansion['member_count'] != len(desired['nodes']) - 1:
            raise RuntimeError('Expansion acceptance report is incomplete')
        result['expansion'] = expansion
        result['steps'].append('added_member_and_preserved_ids_rows_media')
        expanded_ids = copy.deepcopy(expanded['server_ids'])
        lab.deploy(test_run_id=run_id)
        if lab.saved_lab()['server_ids'] != expanded_ids or remote.capture_data(lab.saved_lab()) != control:
            raise RuntimeError('Repeated expanded deploy changed IDs or data')
        if verify_cycle_control(lab.saved_lab(), run_id) != control_http:
            raise RuntimeError('Control media changed over HTTP after expanded reapplication')
        result['control_http'] = control_http
        result['steps'].append('repeated_expanded_deployment')
        result['success'] = True
    except Exception as error:
        result['error_type'] = type(error).__name__
        raise
    finally:
        try:
            if lab.DEPLOYMENT.exists():
                context = lab.read_json(lab.DEPLOYMENT)
                if context.get('test_run_id') != run_id:
                    raise ValueError('Refusing cleanup: context belongs to another run')
                lab.destroy(expected_run_id=run_id)
            elif lab.state_resources():
                raise ValueError('State without owned context: recovery data preserved')
            result['cleanup_success'] = True
            lab.write_json(lab.CONFIG, config)
            result['steps'].append('owned_state_and_api_cleanup')
        except Exception:
            result['success'] = False
            result['cleanup_success'] = False
            print('Test cleanup failed: preserve state, context, keys and diagnostics; run destroy explicitly.', flush=True)
            raise
        finally:
            result['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            lab.write_json(lab.ROOT / 'docs/cycle-latest.json', result)
            if result['cleanup_success'] and lock.exists() and lock.read_text(encoding='utf-8') == run_id:
                lock.unlink()
