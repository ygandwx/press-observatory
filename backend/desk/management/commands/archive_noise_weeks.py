from django.core.management.base import BaseCommand

from desk.noise_archive import archive_noise_weeks


class Command(BaseCommand):
    help = "汇总已结束自然周的运营噪音报告及负样本。重复运行只保留有变化的版本。"

    def handle(self, *args, **options):
        self.stdout.write(f"新增周度噪音报告版本 {archive_noise_weeks()} 份")
