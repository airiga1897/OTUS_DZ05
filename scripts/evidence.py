"""Root-only live evidence through Django ORM; keeps durable rows across failures."""
import hashlib
import json
import os
from pathlib import Path
import sys

cfg=json.loads(Path('/root/dz05.json').read_text())
os.environ.update(DJANGO_SECRET_KEY=cfg['django_secret_key'],DB_PASSWORD=cfg['db_password'],
                  DJANGO_SETTINGS_MODULE='config.settings')
sys.path.insert(0,'/opt/otus-dz05/app')
import django
django.setup()
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group,Permission
from django.contrib.contenttypes.models import ContentType
from django.core import serializers
from django.db import connection
from knowledge.models import Article

mode=sys.argv[1]
if mode=='prepare':
    source=list(Article.objects.order_by('pk').values('pk','slug','title','body'))
    fixture=json.loads(Path('/opt/otus-dz05/app/knowledge/fixtures/initial_data.json').read_text(encoding='utf-8'))
    if Article.objects.filter(slug__in=['dz05-durable','dz05-media-proof']).exists() or get_user_model().objects.filter(username='dz05-check').exists() or Group.objects.filter(name='dz05-check-editors').exists():
        raise RuntimeError('Reserved verification identities already exist; inspect unfinished verification before retrying')
    full=json.loads(serializers.serialize('json',Article.objects.order_by('pk'),use_natural_foreign_keys=True))
    source_max_pk=max([r['pk'] for r in source],default=0)
    expected=sorted([r for r in fixture if r['model']=='knowledge.article'],key=lambda r:r['pk'])
    fixture_matches=full==expected
    baseline={'article_count':len(source),'article_sha256':hashlib.sha256(json.dumps(full,sort_keys=True).encode()).hexdigest()}
    Path('/root/dz05-verification-baseline.json').write_text(json.dumps(baseline))
    a=Article.objects.create(slug='dz05-durable',title='До отказа',lesson=5,summary='HA evidence',body='committed-before-failure')
    assert a.pk>source_max_pk
    u,created=get_user_model().objects.get_or_create(username='dz05-check')
    u.is_staff=u.is_active=True
    u.is_superuser=False
    u.set_password(cfg['db_password']);u.save()
    ct=ContentType.objects.get(app_label='knowledge',model='article')
    permissions=Permission.objects.filter(content_type=ct)
    assert permissions.count()==4
    group,_=Group.objects.get_or_create(name='dz05-check-editors')
    group.permissions.set(permissions)
    u.groups.add(group)
    assert all(u.has_perm('knowledge.'+p) for p in ['add_article','change_article','delete_article','view_article'])
    print(json.dumps({'source_rows':len(source),'source_pks':[r['pk'] for r in source],
                      'source_sha256':hashlib.sha256(json.dumps(source,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                      'new_pk':a.pk,'auto_increment_verified':a.pk>source_max_pk,'fixture_content_matches':fixture_matches,
                      'all_article_fields_match':fixture_matches,
                      'article_permissions':4,'contenttype_group_user_relations_verified':True},ensure_ascii=False))
elif mode=='write':
    a=Article.objects.get(slug='dz05-durable')
    phase=sys.argv[2]
    previous='committed-before-failure' if phase=='PRIMARY' else 'committed-after-PRIMARY'
    updated='committed-after-'+phase
    assert a.body in (previous,updated),'Previously committed content was lost'
    a.body=updated;a.save()
    connection.close()
    assert Article.objects.get(pk=a.pk).body==a.body
    assert get_user_model().objects.get(username='dz05-check').has_perm('knowledge.change_article')
    print(json.dumps({'persisted_pk':a.pk,'body':a.body}))
elif mode=='media':
    a=Article.objects.get(slug='dz05-media-proof')
    assert a.image and Path(a.image.path).is_file()
    print(json.dumps({'pk':a.pk,'media_url':a.image.url,
                      'sha256':hashlib.sha256(Path(a.image.path).read_bytes()).hexdigest()}))
elif mode=='finish':
    assert Article.objects.get(slug='dz05-durable').body=='committed-after-SECONDARY'
    Article.objects.filter(slug__in=['dz05-durable','dz05-media-proof']).delete()
    get_user_model().objects.filter(username='dz05-check').delete()
    Group.objects.filter(name='dz05-check-editors').delete()
    baseline=json.loads(Path('/root/dz05-verification-baseline.json').read_text())
    full=json.loads(serializers.serialize('json',Article.objects.order_by('pk'),use_natural_foreign_keys=True))
    assert hashlib.sha256(json.dumps(full,sort_keys=True).encode()).hexdigest()==baseline['article_sha256'],'Pre-existing article data changed during failure checks'
    print(json.dumps({'source_articles_remaining':Article.objects.count(),'temporary_admin_removed':True,
                      'preexisting_article_data_preserved':True}))
