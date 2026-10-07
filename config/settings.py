
import environ
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()

environ.Env.read_env(BASE_DIR / ".env", overwrite=True)

# Publish metadata only after verifying the matching, signed APK on the server.
# Empty defaults intentionally return 503 rather than advertise an unverified build.
CALLER_APP_UPDATE = {
    'latest_version': env('CALLER_APP_LATEST_VERSION', default=''),
    'version_code': env.int('CALLER_APP_VERSION_CODE', default=0),
    'minimum_version_code': env.int('CALLER_APP_MINIMUM_VERSION_CODE', default=1),
    'mandatory': env.bool('CALLER_APP_UPDATE_MANDATORY', default=False),
    'download_url': env('CALLER_APP_DOWNLOAD_URL', default='https://vaaniapp.co.in/dist/vaani.apk'),
    'release_notes': env.json('CALLER_APP_RELEASE_NOTES', default=[]),
}
# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env("SECRET_KEY")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env.bool("DEBUG", default=False)

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[
    "localhost",
    "127.0.0.1",
    "testserver",
    "192.168.31.191"
])

CORS_ALLOWED_ORIGINS = env.list("WEBSITE_CORS_ALLOWED_ORIGINS", default=[
    "https://authenticattest.com",
    "https://www.authenticattest.com",
])

if not DEBUG:
    # Nginx terminates TLS and proxies plain HTTP to Gunicorn; without this,
    # Django thinks every request is insecure and rejects/loops on HTTPS checks.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

AUTH_USER_MODEL = "accounts.User"
# Application definition

INSTALLED_APPS = [
    'daphne',  # must precede staticfiles so its ASGI-aware runserver takes over

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    "channels",

    "apps.accounts",
    "apps.notifications",
    "apps.leads",
    "apps.calls",
    "apps.followups",
    "apps.activity",
    "apps.web",
    "apps.chat",
    "apps.performance",

]

ASGI_APPLICATION = 'config.asgi.application'

# In-memory channel layer works fine for local dev (single runserver process). Set
# REDIS_URL in production so multiple Daphne/worker processes share the same pub-sub.
REDIS_URL = env('REDIS_URL', default='')
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {'hosts': [REDIS_URL]},
    } if REDIS_URL else {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.api.authentication.VerifiedSessionAuthentication",
    ],
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.web.context_processors.notifications',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME"),
        "USER": env("DB_USER"),
        "PASSWORD": env("DB_PASSWORD"),
        "HOST": env("DB_HOST"),
        "PORT": env("DB_PORT"),
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Asia/Kolkata'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = Path(env('MEDIA_ROOT', default=str(BASE_DIR / 'media')))
try:
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
LOGIN_URL = 'web:login'
LOGIN_REDIRECT_URL = 'web:dashboard'
LOGOUT_REDIRECT_URL = 'web:login'


# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

MAILERS = {
    'default': {
        'BACKEND': 'django.core.mail.backends.console.EmailBackend',
    },
}

# Attendance JSON carries a bounded base64 camera image.
DATA_UPLOAD_MAX_MEMORY_SIZE = 5_000_000

FACE_MODEL_DIR = Path(env('FACE_MODEL_DIR', default=str(BASE_DIR / 'face_models')))
FACE_MATCH_THRESHOLD = env.float('FACE_MATCH_THRESHOLD', default=0.363)

# Local dev default assumes caller-app is checked out as a sibling directory and built via
# scripts/build-android.ps1. Override with a real path once a signed release APK is hosted
# (see the deferred VPS deployment plan — this needs to move off local disk at deploy time).
CALLER_APK_PATH = Path(env('CALLER_APK_PATH', default=str(
    BASE_DIR.parent / 'caller-app' / 'android' / 'app' / 'build' / 'outputs' / 'apk' / 'debug' / 'app-debug.apk')))

# Discover isolated app tests and converted root tests, not manual database smoke scripts.
TEST_RUNNER = "config.test_runner.AppTestRunner"

# Independent website intake limits; existing authenticated API limits are unchanged.
PUBLIC_LEAD_BURST_RATE = env('PUBLIC_LEAD_BURST_RATE', default='5/min')
PUBLIC_LEAD_DAILY_RATE = env('PUBLIC_LEAD_DAILY_RATE', default='50/day')

# Website-claim outcomes only; batch/manual lead ownership is unchanged.
WEBSITE_LEAD_BUSY_RETRY_SECONDS = env.int('WEBSITE_LEAD_BUSY_RETRY_SECONDS', default=900)
WEBSITE_LEAD_NO_ANSWER_RETRY_SECONDS = env.int('WEBSITE_LEAD_NO_ANSWER_RETRY_SECONDS', default=3600)

# Website lead source authentication
WEBSITE_LEAD_REQUIRE_API_KEY = True
DEFAULT_WEBSITE_SOURCE_CODE = env('DEFAULT_WEBSITE_SOURCE_CODE', default='official_website')
DEFAULT_WEBSITE_SOURCE_NAME = env('DEFAULT_WEBSITE_SOURCE_NAME', default='Official Website')
DEFAULT_WEBSITE_API_KEY = env('DEFAULT_WEBSITE_API_KEY', default='')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} [{name}] {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'apps.leads': {
            'handlers': ['console'],
            'level': env('LEADS_LOG_LEVEL', default='INFO'),
            'propagate': False,
        },
    },
}

# Explicit website origins; never use CORS as authentication.
from corsheaders.defaults import default_headers
CORS_ALLOW_HEADERS = (*default_headers, "x-api-key")

# Expo push credentials stay on the server.
WEBSITE_LEAD_PUSH_ENABLED = env.bool("WEBSITE_LEAD_PUSH_ENABLED", default=True)
EXPO_PUSH_ACCESS_TOKEN = env("EXPO_PUSH_ACCESS_TOKEN", default="")
