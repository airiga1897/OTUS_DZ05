from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('knowledge', '0001_initial')]
    operations = [migrations.CreateModel(
        name='InitialDataImport',
        fields=[('name', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('fixture_sha256', models.CharField(max_length=64)),
                ('origin', models.CharField(max_length=32)),
                ('applied_at', models.DateTimeField(auto_now_add=True))],
        options={'default_permissions': ()},
    )]
