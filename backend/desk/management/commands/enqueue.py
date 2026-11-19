from django.core.management.base import BaseCommand

from desk.collection import schedule
from desk.reports import schedule_weekly
from desk.noise_archive import archive_noise_weeks


class Command(BaseCommand):
    help = "Enqueue collection, prepare the last complete weekly draft and archive editorial quality reports"

    def handle(self, *args, **kwargs):
        self.stdout.write(str(schedule()))
        weekly = schedule_weekly()
        if weekly:
            self.stdout.write(f"已准备周报草稿 {weekly.day}，编号 {weekly.pk}")
        self.stdout.write(f"新增周度噪音报告版本 {archive_noise_weeks()} 份")
