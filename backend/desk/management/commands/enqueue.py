from django.core.management.base import BaseCommand

from desk.collection import schedule
from desk.reports import schedule_weekly


class Command(BaseCommand):
    help = (
        "Enqueue the Beijing collection slot and prepare the last complete weekly draft"
    )

    def handle(self, *args, **kwargs):
        self.stdout.write(str(schedule()))
        weekly = schedule_weekly()
        if weekly:
            self.stdout.write(f"已准备周报草稿 {weekly.day}，编号 {weekly.pk}")
