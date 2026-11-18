from getpass import getpass
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from rest_framework.exceptions import ValidationError
from desk.catalog import ROLES
from desk.accounts import save_account


class Command(BaseCommand):
    help = "Create or update a named platform account with one business role"

    def add_arguments(self, p):
        p.add_argument("username")
        p.add_argument("role", choices=list(ROLES))
        p.add_argument(
            "--password", action="store_true", help="Prompt for a new password"
        )

    def handle(self, username, role, password, **options):
        update = (
            password or not get_user_model().objects.filter(username=username).exists()
        )
        try:
            save_account(username, role, getpass("密码：") if update else None)
        except ValidationError as exc:
            raise CommandError(str(exc.detail)) from exc
        self.stdout.write("账号角色已保存。")
