from getpass import getpass
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from desk.catalog import ROLES


class Command(BaseCommand):
    help = "Create or update a named account and its desk roles"

    def add_arguments(self, p):
        p.add_argument("username")
        p.add_argument("roles", nargs="+", choices=list(ROLES))
        p.add_argument(
            "--password", action="store_true", help="Prompt for a new password"
        )

    def handle(self, username, roles, password, **options):
        groups = list(Group.objects.filter(name__in=roles))
        if len(groups) != len(set(roles)):
            raise CommandError("请先执行 bootstrap。")
        user, new = get_user_model().objects.get_or_create(username=username)
        if new or password:
            secret = getpass("密码：")
            if len(secret) < 12:
                if new:
                    user.delete()
                raise CommandError("密码至少 12 个字符。")
            user.set_password(secret)
            user.save()
        user.groups.set(groups)
        self.stdout.write("账号角色已保存。")
