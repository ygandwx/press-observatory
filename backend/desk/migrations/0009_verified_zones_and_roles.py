from urllib.parse import urlsplit

from django.db import migrations

ROLE_NAMES = (("查看人员", "社领导"), ("维护人员", "运营编辑"))


def rename(apps, pairs):
    group = apps.get_model("auth", "Group")
    for old, new in pairs:
        if (
            group.objects.filter(name=old).exists()
            and group.objects.filter(name=new).exists()
        ):
            raise RuntimeError("角色名称冲突，已保留原账号，请先核对分组。")
        group.objects.filter(name=old).update(name=new)


def forward(apps, schema_editor):
    rename(apps, ROLE_NAMES)
    source = apps.get_model("desk", "Source")
    change = apps.get_model("desk", "Change")
    reviewed = {
        row.get("source"): row.get("url")
        for row in change.objects.filter(action="登记核验栏目").values_list(
            "detail", flat=True
        )
    }
    for s in source.objects.all():
        host = (urlsplit(s.url).hostname or "").lower()
        if host == "ccgp.gov.cn" or host.endswith(".ccgp.gov.cn"):
            source.objects.filter(pk=s.pk).update(
                stop_reason="政府采购栏目按编辑口径排除"
            )
        elif reviewed.get(s.pk) == s.url or (s.trusted and s.media_id):
            source.objects.filter(pk=s.pk).update(
                zone="媒体官网" if s.media_id else "行业垂类"
            )


def backward(apps, schema_editor):
    rename(apps, ((new, old) for old, new in ROLE_NAMES))


class Migration(migrations.Migration):
    dependencies = [("desk", "0008_editorial_workflow")]
    operations = [migrations.RunPython(forward, backward)]
