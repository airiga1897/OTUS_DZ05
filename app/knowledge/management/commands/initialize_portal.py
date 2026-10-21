"""One transaction for fixture loading and its permanent database marker."""
import hashlib
from pathlib import Path
from django.core.management import BaseCommand, call_command, CommandError
from django.db import transaction
from knowledge.models import Article, InitialDataImport


class Command(BaseCommand):
    def add_arguments(self, parser) -> None:
        parser.add_argument('--allow-initial-load', action='store_true')

    @transaction.atomic
    def handle(self, **options) -> None:
        fixture = Path(__file__).resolve().parents[2] / 'fixtures/initial_data.json'
        digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
        marker, created = InitialDataImport.objects.get_or_create(
            name='portal-initial-v1', defaults={'fixture_sha256': digest, 'origin': 'pending'})
        marker = InitialDataImport.objects.select_for_update().get(pk=marker.pk)
        if not created:
            if marker.origin == 'pending':
                raise CommandError('Incomplete database import marker; inspect before retry')
            self.stdout.write('DATA_INITIALIZED=False')
            return
        if options['allow_initial_load'] and not Article.objects.exists():
            call_command('loaddata', str(fixture), verbosity=0)
            marker.origin = 'fixture_loaded'
        else:
            # Also protects an intentionally emptied existing database from reseeding.
            marker.origin = 'existing_data_preserved'
        marker.save(update_fields=['origin'])
        self.stdout.write('IMPORT_MARKER_CREATED=True')
        self.stdout.write('DATA_INITIALIZED=' + str(marker.origin == 'fixture_loaded'))
