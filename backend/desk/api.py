from functools import wraps
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.db import connection, IntegrityError, transaction
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, NotFound, PermissionDenied
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
from .accounts import business_role, save_account
from .models import Change


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
            role = business_role(req.user)
            if not role or (
                req.method not in ("GET", "HEAD", "OPTIONS") and role != "维护人员"
            ):
                raise PermissionDenied("此操作需要维护人员权限。")
            require(req.user, "read")
            return fn(req, *args, **kwargs)

        return checked

    return decorate


@ensure_csrf_cookie
@api_view(["GET"])
@permission_classes([AllowAny])
def session(req):
    role = business_role(req.user)
    return Response(
        {
            "username": req.user.get_username(),
            "role": role,
            "rights": list(ROLES.get(role, ())),
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


@endpoint(["GET", "POST"])
def accounts(req):
    if business_role(req.user) != "维护人员":
        raise PermissionDenied("此操作需要维护人员权限。")
    User = get_user_model()
    if req.method == "POST":
        username = req.data.get("username")
        role = req.data.get("role")
        action = req.data.get("action")
        if (
            not isinstance(username, str)
            or not username.strip()
            or action not in ("create", "role")
        ):
            raise ValidationError("请输入账号并选择操作。")
        if action == "role" and not User.objects.filter(username=username).exists():
            raise NotFound()
        if action == "create" and not req.data.get("password"):
            raise ValidationError("请输入初始密码。")
        with transaction.atomic():
            user = save_account(
                username,
                role,
                req.data.get("password") if action == "create" else None,
                create_only=action == "create",
            )
            Change.objects.create(
                actor=req.user.username,
                action="账号维护",
                detail={"username": user.username, "role": role},
            )
    return Response(
        {
            "roles": list(ROLES),
            "items": [
                {
                    "username": u.username,
                    "role": business_role(u),
                    "active": u.is_active,
                }
                for u in User.objects.filter(
                    groups__name__in=ROLES, is_staff=False, is_superuser=False
                )
                .distinct()
                .order_by("username")
            ],
        }
    )
