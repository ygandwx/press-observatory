"""Export the same filtered event rows used by the list to CSV or Excel XML."""

import csv
import re
from xml.sax.saxutils import escape

from django.http import HttpResponse

from .labels import text_labels

COLUMNS = [
    "事件编号",
    "标题",
    "分类",
    "重要度",
    "趋势标签",
    "发生日",
    "机构",
    "摘要",
    "原文",
]
ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def values(row):
    return [
        row["id"],
        row["title"],
        row["category"],
        row.get("importance", ""),
        "、".join(text_labels(row.get("tags"))),
        row["occurred"] or "",
        "、".join(m["name"] for m in row["media"]),
        row["summary"],
        " ".join(a["url"] for a in row["articles"]),
    ]


def export_events(rows, kind):
    if kind == "excel":
        response = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="events.xml"'
        response.write(
            '<?xml version="1.0" encoding="UTF-8"?><?mso-application progid="Excel.Sheet"?>'
        )
        response.write(
            '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="事件"><Table>'
        )

        def write_row(cells):
            response.write("<Row>")
            for value in cells:
                response.write(
                    '<Cell><Data ss:Type="String">'
                    + escape(ILLEGAL_XML.sub("", str(value)))
                    + "</Data></Cell>"
                )
            response.write("</Row>")

        write_row(COLUMNS)
        for row in rows:
            write_row(values(row))
        response.write("</Table></Worksheet></Workbook>")
        return response
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="events.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow(
            [
                ("'" + v if v.lstrip().startswith(("=", "+", "-", "@")) else v)
                if isinstance(v, str)
                else v
                for v in values(row)
            ]
        )
    return response
