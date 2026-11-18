from functools import wraps
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth import authenticate, login, logout
from django.db import connection, IntegrityError
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, NotFound
from rest_framework.views import exception_handler as drf_handler
from .catalog import (
    MARKET_SECTIONS,
    ROLES,
    EVENTS,
    CATEGORIES,
    CHANNELS,
    FOCUS,
    LEVELS,
    IMPORTANCE,
)
from .domain import require
from .external import ExternalError


def exception_handler(exc, context):
    if isinstance(exc, ObjectDoesNotExist):
        exc = NotFound("记录不存在。")
    elif isinstance(exc, (ExternalError, IntegrityError)):
        exc = ValidationError(
            str(exc) if isinstance(exc, ExternalError) else "数据已存在或发生冲突。"
        )
    return drf_handler(exc, context)


def endpoint(methods):
    def decorate(fn):
        @api_view(methods)
        @wraps(fn)
        def checked(req, *args, **kwargs):
            require(req.user, "read")
            return fn(req, *args, **kwargs)

        return checked

    return decorate


@ensure_csrf_cookie
@api_view(["GET"])
@permission_classes([AllowAny])
def session(req):
    return Response(
        {
            "username": req.user.get_username(),
            "rights": [c for c in ROLES.values() if req.user.has_perm("desk." + c)],
            "csrf": get_token(req),
            "catalog": {
                "events": EVENTS,
                "market_sections": MARKET_SECTIONS,
                "categories": CATEGORIES,
                "channels": CHANNELS,
                "focus": FOCUS,
                "levels": LEVELS,
                "importance": IMPORTANCE,
            },
        }
    )


@api_view(["POST"])
@permission_classes([AllowAny])
@csrf_protect
def sign_in(req):
    user = authenticate(
        username=req.data.get("username", ""), password=req.data.get("password", "")
    )
    if not user:
        return Response({"detail": "账号或密码有误。"}, status=400)
    login(req, user)
    return Response({"ok": True})


@api_view(["POST"])
def sign_out(req):
    logout(req)
    return Response({"ok": True})


@api_view(["GET"])
@permission_classes([AllowAny])
def ready(req):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return Response({"ready": True})
    except Exception:
        return Response({"ready": False}, status=503)
