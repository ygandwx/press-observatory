import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = os.getenv("DEBUG") == "1"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
CSRF_TRUSTED_ORIGINS = list(
    filter(None, os.getenv("CSRF_TRUSTED_ORIGINS", "").split(","))
)
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "desk",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "observatory"),
        "USER": os.getenv("POSTGRES_USER", "observatory"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
        "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}
if os.getenv("DEV_SQLITE") == "1":
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.getenv("SQLITE_PATH", str(BASE / "dev.sqlite3")),
    }
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication"
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
TIME_ZONE = "Asia/Shanghai"
USE_TZ = True
LANGUAGE_CODE = "zh-hans"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
WEB_DIST = Path(os.environ.get("WEB_DIST", BASE.parent / "frontend" / "dist"))
WHITENOISE_ROOT = WEB_DIST
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
SECURE_CONTENT_TYPE_NOSNIFF = True
DATA_UPLOAD_MAX_MEMORY_SIZE = 1_000_000
REST_FRAMEWORK["EXCEPTION_HANDLER"] = "desk.api.exception_handler"

STATIC_URL = "/static/"
