"""Root-only read-only snapshot of Django rows and media, without exposing values."""
import hashlib
import json
import os
from pathlib import Path
import sys


def main() -> None:
    cfg = json.loads(Path('/root/dz05.json').read_text())
    os.environ.update(DJANGO_SECRET_KEY=cfg['django_secret_key'], DB_PASSWORD=cfg['db_password'],
                      DJANGO_SETTINGS_MODULE='config.settings')
    sys.path.insert(0, '/opt/otus-dz05/app')
    import django
    django.setup()
    from django.apps import apps
    from django.core import serializers
    from django.db import transaction
    result = {}
    with transaction.atomic():
        for model in apps.get_models():
            if model._meta.label_lower == 'knowledge.initialdataimport':
                continue  # Operational import metadata can be added without changing user data.
            rows = model.objects.order_by('pk')
            if model._meta.label_lower == 'contenttypes.contenttype':
                rows = rows.exclude(app_label='knowledge', model='initialdataimport')
            if model._meta.label_lower == 'auth.permission':
                rows = rows.exclude(content_type__app_label='knowledge', content_type__model='initialdataimport')
            text = serializers.serialize('json', rows)
            result[model._meta.label_lower] = hashlib.sha256(text.encode()).hexdigest()
    media = Path('/srv/otus/media')
    files = {str(p.relative_to(media)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(media.rglob('*')) if p.is_file()}
    result['media'] = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
