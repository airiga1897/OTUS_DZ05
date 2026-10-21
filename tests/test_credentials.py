"""Operator input is session-only; local development loading is explicit."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import settings
import tf


class Credentials(unittest.TestCase):
    def setUp(self):
        self.env = dict(OS_USER_DOMAIN_NAME='test-account', OS_USERNAME='test-user',
                        OS_PASSWORD='test-password', OS_PROJECT_ID='a' * 32)

    def test_environment_works_without_credential_file(self):
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(settings, 'read_json', wraps=settings.read_json) as reads:
            auth = settings.cloud_config({'project_id': 'a' * 32, 'region': 'ru-9'})
        self.assertTrue(all(call.args[0] == settings.ROOT / 'config/selectel-auth.json'
                            for call in reads.call_args_list))
        self.assertEqual(auth['password'], 'test-password')
        self.assertEqual(auth['region'], 'ru-9')

    def test_mapping_comes_from_shared_schema(self):
        schema = {'auth_url': 'https://cloud.api.selcloud.ru/identity/v3',
                  'fields': {key: {'env': 'TEST_' + key.upper()} for key in
                             ('domain_name', 'project_id', 'username', 'password')}}
        env = {field['env']: ('a' * 32 if key == 'project_id' else 'synthetic')
               for key, field in schema['fields'].items()}
        with patch.dict(os.environ, env, clear=True), patch.object(settings, 'auth_settings', return_value=schema):
            self.assertEqual(settings.credentials_from_env()['username'], 'synthetic')

    def test_file_selector_is_ignored_by_python(self):
        with patch.dict(os.environ, {'OTUS_SELECTEL_AUTH': 'not-a-credential-source'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'OS_PASSWORD'):
                settings.credentials_from_env()

    def test_terraform_mapping_uses_schema_and_saved_pool(self):
        schema = settings.auth_settings()
        schema['fields']['username']['env'] = 'TEST_OPERATOR_NAME'
        auth = dict(domain_name='synthetic-account', project_id='a' * 32,
                    username='synthetic-user', password='synthetic-password',
                    auth_url=schema['auth_url'], region='ru-9')
        with patch.dict(os.environ, {'OS_REGION_NAME': 'ru-7'}, clear=True), \
             patch.object(tf, 'cloud_config', return_value=auth), \
             patch.object(tf, 'auth_settings', return_value=schema), \
             patch.object(tf, 'DEPLOYMENT') as deployment:
            deployment.exists.return_value = False
            env = tf.operator_env()
        self.assertEqual(env['TEST_OPERATOR_NAME'], 'synthetic-user')
        self.assertEqual(env['OS_REGION_NAME'], 'ru-9')

    def test_missing_input_does_not_fall_back_to_file(self):
        for key in self.env:
            with self.subTest(key=key), patch.dict(os.environ, {k: v for k, v in self.env.items() if k != key}, clear=True):
                with self.assertRaisesRegex(ValueError, key):
                    settings.cloud_config({'project_id': 'a' * 32, 'region': 'ru-9'})

    def test_session_cannot_redirect_saved_project(self):
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaisesRegex(ValueError, 'project'):
                settings.cloud_config({'project_id': 'b' * 32, 'region': 'ru-9'})
