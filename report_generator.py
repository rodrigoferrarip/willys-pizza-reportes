import io
import os
import csv
import json
import urllib.parse
from datetime import datetime

import requests
import google.generativeai as genai

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

NEGRO = colors.HexColor("#000000")
AMARILLO = colors.HexColor("#FFC700")

genai.configure(api_key=os.environ["GEMINI_API_KEY"])


def _to_float(value):
    if value is None:
        return None
    value = value.strip().replace(",", "")
    if value == "":
        return None
    return float(value)


def parse_csv(file_stream):
    text = file_stream.read()
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    rows = []
    for raw in reader:
        if not raw.get("created_at_gmt3") or not raw.get("total_amount"):
            continue
        fecha = datetime.strptime(raw["created_at_gmt3"].strip(), "%B %d, %Y, %I:%M %p")
        rows.append({
            "fecha": fecha,
            "mes_label": fecha.strftime("%b %Y"),
            "order_count": int(float(raw["order_count"])),
            "total_amount": _to_float(raw["total_amount"]),
            "amount_growth_percent": _to_float(raw.get("amount_growth_percent")),
            "order_count_growth_percent": _to_float(raw.get("order_count_growth_percent")),
        })

    rows.sort(key=lambda r: r["fecha"])
    return rows


def compute_stats(rows):
    best = max(rows, key=lambda r: r["total_amount"])
    worst = min(rows, key=lambda r: r["total_amount"])
    total_monto = sum(r["total_amount"] for r in rows)
    total_pedidos = sum(r["order_count"] for r in rows)
    n = len(rows)

    return {
        "periodo_inicio": rows[0]["mes_label"],
        "periodo_fin": rows[-1]["mes_label"],
        "total_pedidos": total_pedidos,
        "total_monto": total_monto,
        "promedio_monto": total_monto / n,
        "promedio_pedidos": total_pedidos / n,
        "mejor_mes": best["mes_label"],
        "mejor_mes_monto": best["total_amount"],
        "peor_mes": worst["mes_label"],
        "peor_mes_monto": worst["total_amount"],
        "ultimo_mes": rows[-1]["mes_label"],
        "ultimo_mes_monto": rows[-1]["total_amount"],
        "ultimo_crecimiento": rows[-1]["amount_growth_percent"],
    }


def call_gemini(rows, stats):
    model = genai.GenerativeModel("gemini-1.5-flash")

    lineas = ["mes | pedidos | monto_total | crecimiento_monto% | crecimiento_pedidos%"]
    for r in rows:
        lineas.append(
            f"{r['mes_label']} | {r['order_count']} | {r['total_amount']:.2f} | "
            f"{r['amount_growth_percent'] if r['amount_growth_percent'] is not None else 'N/A'} | "
            f"{r['order_count_growth_percent'] if r['order_count_growth_percent'] is not None else 'N/A'}"
        )
    tabla = "\n".join(lineas)

    prompt = f"""Eres un analista financiero experto en negocios gastronomicos. A continuacion te paso los datos de ventas mensuales de "Willy's Pizza", una pizzeria, extraidos de su plataforma de pedidos online.

Datos:
{tabla}

Estadisticas ya calculadas para que las uses como referencia:
{json.dumps(stats, ensure_ascii=False, indent=2)}

Con estos datos, redacta un reporte ejecutivo en espanol, claro y directo, con EXACTAMENTE estas secciones (usa estos titulos tal cual, precedidos por ##):

## Resumen del periodo
## Analisis de tendencia
## Mejor y peor mes
## Promedio mensual
## Recomendacion

Tono profesional pero cercano, sin tecnicismos innecesarios. No uses tablas markdown, solo texto corrido bajo cada titulo. No repitas los titulos de seccion dentro del texto."""

    response = model.generate_content(prompt)
    return response.text


def fetch_banner_image():
    prompt = (
        "Minimalist flat design illustration of a pizza, black and yellow color "
        "palette, clean background, modern branding style for a pizzeria called "
        "Willy's Pizza, no text, horizontal banner format"
    )
    url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
    url += "?width=1024&height=400&nologo=true&seed=42"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return io.BytesIO(resp.content)


def fetch_chart_image(rows):
    last6 = rows[-6:]
    labels = [r["mes_label"] for r in last6]
    data = [round(r["total_amount"], 2) for r in last6]
    chart_config = {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": "Ventas (UYU)",
                "data": data,
                "backgroundColor": "#FFC700",
                "borderColor": "#000000",
                "borderWidth": 2,
            }],
        },
        "options": {
            "plugins": {"legend": {"labels": {"color": "#000000"}}},
            "scales": {
                "x": {"ticks": {"color": "#000000"}},
                "y": {"ticks": {"color": "#000000"}},
            },
        },
    }
    url = "https://quickchart.io/chart"
    resp = requests.post(url, json={"chart": chart_config, "width": 600, "height": 350, "backgroundColor": "white"}, timeout=60)
    resp.raise_for_status()
    return io.BytesIO(resp.content)


def parse_sections(report_text):
    sections = {}
    current = None
    buf = []
    for line in report_text.splitlines():
        if line.strip().startswith("##"):
            if current:
                sections[current] = "\n".join(buf).strip()
            current = line.strip().lstrip("#").strip()
            buf = []
        else:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()
    return sections


def build_pdf(rows, stats, report_text, banner_img, chart_img):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle("TitleY", parent=styles["Title"], textColor=AMARILLO, backColor=NEGRO,
                                  alignment=1, spaceAfter=0, leading=28, fontSize=22, leftIndent=0)
    h2_style = ParagraphStyle("H2", parent=styles["Heading2"], textColor=NEGRO,
                               spaceBefore=14, spaceAfter=6, fontSize=14)
    body_style = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=10.5, leading=15)

    elements = []
    elements.append(Paragraph("WILLY'S PIZZA &mdash; Reporte Semanal de Ventas", title_style))
    elements.append(Spacer(1, 12))

    banner_img.seek(0)
    elements.append(Image(banner_img, width=17 * cm, height=6.6 * cm))
    elements.append(Spacer(1, 14))

    sections = parse_sections(report_text)
    for titulo, contenido in sections.items():
        elements.append(Paragraph(titulo, h2_style))
        for parrafo in contenido.split("\n\n"):
            parrafo = parrafo.strip().replace("\n", " ")
            if parrafo:
                elements.append(Paragraph(parrafo, body_style))
                elements.append(Spacer(1, 4))

    elements.append(Spacer(1, 10))
    elements.append(Paragraph("Grafica de ventas (ultimos 6 meses)", h2_style))
    chart_img.seek(0)
    elements.append(Image(chart_img, width=16 * cm, height=9.3 * cm))

    elements.append(PageBreak())
    elements.append(Paragraph("Tabla resumen mensual", h2_style))

    table_data = [["Mes", "Pedidos", "Monto (UYU)", "% Crec. Monto", "% Crec. Pedidos"]]
    for r in rows:
        crec_monto = f"{r['amount_growth_percent']:.1f}%" if r["amount_growth_percent"] is not None else "-"
        crec_pedidos = f"{r['order_count_growth_percent']:.1f}%" if r["order_count_growth_percent"] is not None else "-"
        table_data.append([
            r["mes_label"],
            str(r["order_count"]),
            f"{r['total_amount']:,.2f}",
            crec_monto,
            crec_pedidos,
        ])

    tbl = Table(table_data, repeatRows=1, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NEGRO),
        ("TEXTCOLOR", (0, 0), (-1, 0), AMARILLO),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
    ]))
    elements.append(tbl)

    doc.build(elements)
    buf.seek(0)
    return buf


def generate_report_pdf(file_stream):
    rows = parse_csv(file_stream)
    stats = compute_stats(rows)
    report_text = call_gemini(rows, stats)
    banner_img = fetch_banner_image()
    chart_img = fetch_chart_image(rows)
    return build_pdf(rows, stats, report_text, banner_img, chart_img)
