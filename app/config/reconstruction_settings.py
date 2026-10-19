"""Local-only reconstruction of the four lessons; never a production backend."""
import os
from .settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3",
                         "NAME": os.environ['RECONSTRUCTION_DB']}}
