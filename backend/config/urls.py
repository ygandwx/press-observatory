from django.urls import path, re_path
from django.conf import settings
from django.http import FileResponse, HttpResponse
from desk import api, events_api as e, management_api as m, reports_api as r


def frontend(request):
    index = settings.WEB_DIST / "index.html"
    if not index.is_file():
        return HttpResponse(
            "前端尚未构建，请在 frontend 目录执行 npm ci 和 npm run build。", status=503
        )
    return FileResponse(index.open("rb"), content_type="text/html; charset=utf-8")


routes = [
    ("ready", api.ready),
    ("session", api.session),
    ("login", api.sign_in),
    ("logout", api.sign_out),
    ("dashboard", e.dashboard),
    ("events", e.events),
    ("events/review", e.bulk),
    ("events/search", e.semantic),
    ("events/<int:pk>", e.event_detail),
    ("media", m.archives),
    ("media/<int:pk>", m.archives),
    ("monitor", m.monitoring),
    ("monitor/<str:action>", m.operations),
    ("reports", r.reports),
    ("reports/<int:pk>", r.reports),
    ("shared/<slug:token>", r.public),
]
urlpatterns = [path("api/v1/" + route, view) for route, view in routes] + [
    re_path(r"^(?!api/|assets/).*$", frontend)
]
