"""Create user-like data only on the explicitly owned temporary test stand."""
import io
import re
from PIL import Image
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from knowledge.models import Article


class Command(BaseCommand):
    def add_arguments(self, parser) -> None:
        parser.add_argument('run_id')

    def handle(self, run_id: str, **options) -> None:
        if not re.fullmatch(r'[0-9a-f]{32}', run_id):
            raise CommandError('Invalid temporary run identity')
        slug = 'cycle-control-' + run_id
        if Article.objects.filter(slug=slug).exists():
            raise CommandError('Control article already exists; refusing to overwrite')
        article = Article.objects.create(title='Контроль расширения', slug=slug, lesson=5,
                                         summary='User data preservation', body='Committed before expansion', published=True)
        image = io.BytesIO()
        Image.new('RGB', (8, 8), 'green').save(image, format='PNG')
        article.image.save('cycle-control.png', ContentFile(image.getvalue()), save=True)
        self.stdout.write('Control article and media committed.')
