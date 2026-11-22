import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'yemekhane-secret-key-2026-insecure-dev')

DEBUG = True

ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'api',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'api.sessions.RedisSessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
]

ROOT_URLCONF = 'config.urls'

WSGI_APPLICATION = 'config.wsgi.application'

# Veritabanı (Okuma/Yazma Ayrımı)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('DB_ADI', 'yemekhane'),
        'USER': os.environ.get('DB_KULLANICI', 'yemekhane_app'),
        'PASSWORD': os.environ.get('DB_SIFRE', 'Uyg_2026'),
        'HOST': os.environ.get('DB_YAZMA_HOST', 'mysql-primary'),
        'PORT': os.environ.get('DB_PORT', '3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET NAMES 'utf8mb4' COLLATE 'utf8mb4_tr_0900_ai_ci'",
        },
    },
    'replica': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('DB_ADI', 'yemekhane'),
        'USER': os.environ.get('DB_KULLANICI', 'yemekhane_app'),
        'PASSWORD': os.environ.get('DB_SIFRE', 'Uyg_2026'),
        'HOST': os.environ.get('DB_OKUMA_HOST', 'mysql-replica'),
        'PORT': os.environ.get('DB_PORT', '3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET NAMES 'utf8mb4' COLLATE 'utf8mb4_tr_0900_ai_ci'",
        },
    }
}

# Redis Ayarları
REDIS_HOST = os.environ.get('REDIS_HOST', 'redis')
REDIS_PORT = int(os.environ.get('REDIS_PORT', '6379'))

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'loggers': {
        'api.cache': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'api.sessions': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}

LANGUAGE_CODE = 'tr-tr'
TIME_ZONE = 'Europe/Istanbul'
USE_I18N = True
USE_TZ = False
