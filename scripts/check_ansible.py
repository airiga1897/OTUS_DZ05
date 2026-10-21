"""Static source checks on Windows; actual Ansible syntax-check runs on Linux."""
from pathlib import Path
import yaml
from jinja2 import Environment


def check(root: Path) -> None:
    environment = Environment()
    source = root/'ansible'
    plays = yaml.safe_load((source/'site.yml').read_text(encoding='utf-8'))
    for play in plays:
        for role in play['roles']:
            if not (source/'roles'/role/'tasks/main.yml').is_file():
                raise ValueError('Missing configuration role: ' + role)
    for path in source.rglob('*.yml'):
        if not isinstance(yaml.safe_load(path.read_text(encoding='utf-8')), list):
            raise ValueError('Invalid Ansible source list: ' + path.name)
    for path in source.rglob('*.j2'):
        environment.parse(path.read_text(encoding='utf-8'))
    print('Ansible source checks PASS; Linux syntax-check and live application are separate.')


if __name__ == '__main__':
    check(Path(__file__).resolve().parents[1])
