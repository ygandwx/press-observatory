from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from desk.models import Rule, Media, Source
from desk.catalog import ROLES


class Command(BaseCommand):
    help = "Initialize role groups and keyword defaults; optionally add verified public source metadata"

    def add_arguments(self, parser):
        parser.add_argument("--public-sources", action="store_true")

    def handle(self, *args, **options):
        for label, code in ROLES.items():
            group, _ = Group.objects.get_or_create(name=label)
            group.permissions.add(
                *Permission.objects.filter(
                    content_type__app_label="desk", codename__in=["read", code]
                )
            )
        words = {
            "主体词": "媒体 报业 报社 日报 晚报 传媒 通讯社 期刊",
            "动作词": "改革 变革 融合 上线 创刊 转型 整合 停刊 改版 人事 重组",
            "排除词": "医院诊疗 科技公司IPO 物流配送",
        }
        for kind, values in words.items():
            for word in values.split():
                Rule.objects.get_or_create(kind=kind, word=word)
        if options["public_sources"]:
            for name, web in [
                ("人民日报", "https://www.people.com.cn/"),
                ("光明日报", "https://www.gmw.cn/"),
            ]:
                m, _ = Media.objects.get_or_create(
                    name=name,
                    defaults={
                        "category": "中央级主流媒体",
                        "subcategory": "中央机关党报党刊",
                        "level": "中央级",
                        "focus": "核心必看",
                        "region": "北京",
                        "channels": {"官网": web},
                        "reference": web,
                    },
                )
                Source.objects.get_or_create(
                    name=name + "·官网",
                    defaults={"url": web, "media": m, "trusted": True},
                )
            Source.objects.get_or_create(
                name="光明网·报网动态",
                defaults={
                    "url": "https://about.gmw.cn/node_8926.htm",
                    "media": Media.objects.get(name="光明日报"),
                    "trusted": True,
                },
            )
        self.stdout.write("角色与规则已就绪。")
