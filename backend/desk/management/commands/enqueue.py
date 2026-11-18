from django.core.management.base import BaseCommand
from desk.collection import schedule


class Command(BaseCommand):
    help = "Enqueue the current Beijing 08:00 or 18:00 slot idempotently"

    def handle(self, *args, **kwargs):
        self.stdout.write(str(schedule()))
