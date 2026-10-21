"""Exercise committed CRUD through Django's configured Router connection."""
import uuid
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from knowledge.models import Article

class Command(BaseCommand):
    help = 'Проверить запись, повторное чтение и удаление через MySQL Router.'

    def handle(self, **options):
        slug = 'probe-' + uuid.uuid4().hex
        try:
            a = Article.objects.create(slug=slug, title='Проверка ДЗ05', lesson=5,
                                       summary='CRUD probe', body='Первичная запись', published=False)
            pk = a.pk
            connection.close()
            if Article.objects.get(pk=pk).body != 'Первичная запись':
                raise CommandError('Read after committed write failed')
            Article.objects.filter(pk=pk).update(body='Обновлено')
            connection.close()
            if Article.objects.get(pk=pk).body != 'Обновлено':
                raise CommandError('Read after update failed')
            with connection.cursor() as cursor:
                cursor.execute('SELECT @@server_uuid, @@read_only')
                server, read_only = cursor.fetchone()
            if read_only:
                raise CommandError('Router connected to read-only node')
            self.stdout.write(f'CRUD PASS pk={pk} server_uuid={server}')
        finally:
            Article.objects.filter(slug=slug).delete()
