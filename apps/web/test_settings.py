"""Run web tests against an isolated in-memory database."""
from config.settings import *  # noqa: F403

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
SECURE_SSL_REDIRECT = False

TEST_RUNNER = 'config.test_runner.AppTestRunner'
