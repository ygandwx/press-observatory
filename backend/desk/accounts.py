from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework.exceptions import ValidationError
from .catalog import ROLES


def business_role(user):
    if not user.is_authenticated or not user.is_active:
        return ""
    if user.is_superuser:
        return "运营编辑"
    groups = set(user.groups.values_list("name", flat=True))
    return next((role for role in ("运营编辑", "社领导") if role in groups), "")


@transaction.atomic
def save_account(username, role, password=None, *, create_only=False):
    if not isinstance(role, str) or role not in ROLES:
        raise ValidationError("请选择社领导或运营编辑。")
    groups = {
        g.name: g
        for g in Group.objects.select_for_update().filter(name__in=ROLES).order_by("id")
    }
    if len(groups) != 2:
        raise ValidationError("请先初始化平台角色。")
    User = get_user_model()
    user = User.objects.select_for_update().filter(username=username).first()
    if user and (
        create_only or user.is_staff or user.is_superuser or not business_role(user)
    ):
        raise ValidationError("账号已存在或不属于平台业务账号。")
    if not user:
        if not password:
            raise ValidationError("新账号需要初始密码。")
        user = User(username=username)
    elif business_role(user) == "运营编辑" and role != "运营编辑":
        if (
            not User.objects.filter(is_active=True, groups=groups["运营编辑"])
            .exclude(pk=user.pk)
            .exists()
        ):
            raise ValidationError("至少保留一个可登录的运营编辑账号。")
    if password is not None:
        if not isinstance(password, str) or len(password) < 12:
            raise ValidationError("密码至少 12 个字符。")
    try:
        if password is not None:
            validate_password(password, user)
            user.set_password(password)
        user.full_clean()
    except DjangoValidationError as exc:
        raise ValidationError(exc.messages) from exc
    user.save()
    user.groups.remove(*groups.values())
    user.groups.add(groups[role])
    return user
