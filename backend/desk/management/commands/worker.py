import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from desk.collection import work_once


class Command(BaseCommand):
    help = "Run the collection worker"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            worked = work_once()
            if options["once"]:
                return
            if not worked:
                time.sleep(3)
