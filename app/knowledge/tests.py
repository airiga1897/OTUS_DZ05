import tempfile
from pathlib import Path
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from .models import Article
from .models import InitialDataImport

class KnowledgeTests(TestCase):
    def setUp(self) -> None:
        Article.objects.create(title="ДЗ", slug="published", lesson=1, summary="Тест", body="<script>x</script>", published=True)
        Article.objects.create(title="Черновик", slug="draft", lesson=2, summary="Скрыто", body="Тест")

    def test_public_pages_hide_drafts_and_escape_content(self) -> None:
        response = self.client.get("/")
        self.assertContains(response, "ДЗ")
        self.assertNotContains(response, "Черновик")
        self.assertEqual(self.client.get("/articles/draft/").status_code, 404)
        self.assertContains(self.client.get("/articles/published/"), "&lt;script&gt;")

    def test_admin_requires_login(self) -> None:
        self.assertEqual(self.client.get("/admin/knowledge/article/add/").status_code, 302)

    def test_admin_can_upload_image(self) -> None:
        import io
        from PIL import Image
        user = get_user_model().objects.create_superuser("editor", "editor@example.invalid", "test-password")
        self.assertTrue(self.client.login(username=user.username, password="test-password"))
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4), "blue").save(buffer, format="PNG")
        with tempfile.TemporaryDirectory() as root:
            with override_settings(MEDIA_ROOT=root):
                response = self.client.post("/admin/knowledge/article/add/", {
                    "title": "Файл", "slug": "uploaded", "lesson": 4, "summary": "Картинка", "body": "Тест",
                    "published": "on", "image": SimpleUploadedFile("test.png", buffer.getvalue(), content_type="image/png"), "_save": "Сохранить"})
                self.assertEqual(response.status_code, 302)
                article = Article.objects.get(slug="uploaded")
                self.assertTrue(Path(article.image.path).exists())

    def test_readiness_requires_storage_and_live_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with override_settings(STATIC_ROOT=Path(root)/"static", REQUIRE_SHARED_MOUNT=False):
                self.assertEqual(self.client.get("/health/live").status_code, 200)
                self.assertEqual(self.client.get("/health/ready").status_code, 503)
                (Path(root)/".ready").write_text("otus-dz05")
                self.assertEqual(self.client.get("/health/ready").status_code, 200)
            with override_settings(STATIC_ROOT=Path(root)/"static", REQUIRE_SHARED_MOUNT=True):
                self.assertEqual(self.client.get("/health/ready").status_code, 503)


class InitialDataTests(TestCase):
    fixtures = ['initial_data.json']

    def test_temporary_cycle_creates_control_data_and_media_without_overwriting(self) -> None:
        from django.core.management.base import CommandError
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            Article.objects.filter(pk=1).update(title='User edit before expansion')
            call_command('create_cycle_control', 'a' * 32, verbosity=0)
            control = Article.objects.get(slug='cycle-control-' + 'a' * 32)
            self.assertEqual(control.body, 'Committed before expansion')
            with Image.open(control.image.path) as image:
                image.verify()
            with self.assertRaises(CommandError):
                call_command('create_cycle_control', 'a' * 32, verbosity=0)
            self.assertEqual(Article.objects.count(), 6)
            self.assertEqual(Article.objects.get(pk=1).title, 'User edit before expansion')

    def test_fixture_preserves_all_fields_and_identifiers(self) -> None:
        import json
        from django.core import serializers
        expected = json.loads((Path(__file__).parent / 'fixtures/initial_data.json').read_text(encoding='utf-8'))
        actual = json.loads(serializers.serialize('json', Article.objects.order_by('pk')))
        self.assertEqual(actual, expected)
        self.assertEqual(list(Article.objects.order_by('pk').values_list('pk', flat=True)), [1, 2, 3, 4, 5])
        self.assertTrue(Article.objects.get(lesson=5).published)
        article = Article.objects.create(title='Next', slug='next-id', lesson=6, summary='Next', body='Next')
        self.assertGreater(article.pk, 5)

    def test_migrations_own_contenttypes_permissions_and_relations(self) -> None:
        from django.contrib.auth.models import Group, Permission
        from django.contrib.contenttypes.models import ContentType
        contenttype = ContentType.objects.get_for_model(Article)
        permissions = Permission.objects.filter(content_type=contenttype)
        self.assertEqual(set(permissions.values_list('codename', flat=True)),
                         {'add_article', 'change_article', 'delete_article', 'view_article'})
        group = Group.objects.create(name='fixture-editors')
        group.permissions.set(permissions)
        user = get_user_model().objects.create_user('fixture-editor', password='synthetic-password')
        user.groups.add(group)
        self.assertTrue(user.has_perm('knowledge.change_article'))

        call_command('loaddata', 'initial_data.json', verbosity=0)
        self.assertEqual(Article.objects.count(), 5)
        self.assertEqual(ContentType.objects.filter(app_label='knowledge', model='article').count(), 1)
        self.assertEqual(Permission.objects.filter(content_type=contenttype).count(), 4)
        user = get_user_model().objects.get(pk=user.pk)
        self.assertTrue(user.has_perm('knowledge.change_article'))

class DatabaseImportMarkerTests(TestCase):
    def test_fresh_database_loads_once_and_reapplication_preserves_user_data(self) -> None:
        call_command('initialize_portal', allow_initial_load=True, verbosity=0)
        self.assertEqual(Article.objects.count(), 5)
        self.assertEqual(InitialDataImport.objects.get().origin, 'fixture_loaded')
        Article.objects.filter(pk=1).update(title='Edited by user')
        call_command('initialize_portal', allow_initial_load=True, verbosity=0)
        self.assertEqual(Article.objects.get(pk=1).title, 'Edited by user')
        self.assertEqual(InitialDataImport.objects.count(), 1)

    def test_missing_marker_adopts_existing_data_without_loading_fixture(self) -> None:
        article = Article.objects.create(pk=77, title='User article', slug='user-article', lesson=5,
                                         summary='Keep', body='Keep me', published=True)
        call_command('initialize_portal', allow_initial_load=True, verbosity=0)
        self.assertEqual(list(Article.objects.values_list('pk', flat=True)), [article.pk])
        self.assertEqual(InitialDataImport.objects.get().origin, 'existing_data_preserved')

    def test_empty_existing_database_is_not_reseeded(self) -> None:
        call_command('initialize_portal', verbosity=0)
        call_command('initialize_portal', allow_initial_load=True, verbosity=0)
        self.assertFalse(Article.objects.exists())
        self.assertEqual(InitialDataImport.objects.get().origin, 'existing_data_preserved')

    def test_import_failure_rolls_back_rows_and_marker_together(self) -> None:
        from unittest.mock import patch
        def interrupted_import(*args, **kwargs):
            Article.objects.create(pk=77, title='Incomplete', slug='incomplete', lesson=5, summary='x', body='x')
            raise RuntimeError('Interrupted fixture load')
        with patch('knowledge.management.commands.initialize_portal.call_command', side_effect=interrupted_import):
            with self.assertRaisesRegex(RuntimeError, 'Interrupted'):
                call_command('initialize_portal', allow_initial_load=True, verbosity=0)
        self.assertFalse(Article.objects.exists())
        self.assertFalse(InitialDataImport.objects.exists())
