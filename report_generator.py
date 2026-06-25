import io
import os
import csv
import json
from datetime import datetime, timedelta

import requests
from openai import OpenAI

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

NEGRO = colors.HexColor("#000000")
AMARILLO = colors.HexColor("#FFC700")

groq_client = OpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
)


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
            "dia_label": fecha.strftime("%d %b %Y"),
            "order_count": int(float(raw["order_count"])),
            "total_amount": _to_float(raw["total_amount"]),
            "amount_growth_percent": _to_float(raw.get("amount_growth_percent")),
            "order_count_growth_percent": _to_float(raw.get("order_count_growth_percent")),
        })

    rows.sort(key=lambda r: r["fecha"])
    return rows


def filter_period(rows, start, end):
    return [r for r in rows if start <= r["fecha"] <= end]


def default_comparison_period(start, end):
    duracion = end - start
    comp_end = start - timedelta(days=1)
    comp_start = comp_end - duracion
    return comp_start, comp_end


DIAS_SEMANA = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]


def compute_period_stats(rows):
    if not rows:
        return None
    best = max(rows, key=lambda r: r["total_amount"])
    worst = min(rows, key=lambda r: r["total_amount"])
    total_monto = sum(r["total_amount"] for r in rows)
    total_pedidos = sum(r["order_count"] for r in rows)
    n = len(rows)

    monto_por_dia_semana = {}
    pedidos_por_dia_semana = {}
    for r in rows:
        dia_semana = DIAS_SEMANA[r["fecha"].weekday()]
        monto_por_dia_semana[dia_semana] = monto_por_dia_semana.get(dia_semana, 0) + r["total_amount"]
        pedidos_por_dia_semana[dia_semana] = pedidos_por_dia_semana.get(dia_semana, 0) + r["order_count"]
    dia_semana_mas_fuerte = max(monto_por_dia_semana, key=monto_por_dia_semana.get)

    return {
        "desde": rows[0]["dia_label"],
        "hasta": rows[-1]["dia_label"],
        "dias_con_ventas": n,
        "total_pedidos": total_pedidos,
        "total_monto": round(total_monto, 2),
        "ticket_promedio": round(total_monto / total_pedidos, 2) if total_pedidos else 0,
        "promedio_diario_monto": round(total_monto / n, 2),
        "promedio_diario_pedidos": round(total_pedidos / n, 2),
        "mejor_dia": best["dia_label"],
        "mejor_dia_monto": best["total_amount"],
        "peor_dia": worst["dia_label"],
        "peor_dia_monto": worst["total_amount"],
        "dia_semana_mas_fuerte": dia_semana_mas_fuerte,
        "monto_por_dia_semana": {k: round(v, 2) for k, v in monto_por_dia_semana.items()},
    }


def compute_comparison(stats_a, stats_b):
    if not stats_b or stats_b["total_monto"] == 0:
        return {"crecimiento_monto": None, "crecimiento_pedidos": None}
    crecimiento_monto = (stats_a["total_monto"] - stats_b["total_monto"]) / stats_b["total_monto"] * 100
    crecimiento_pedidos = (stats_a["total_pedidos"] - stats_b["total_pedidos"]) / stats_b["total_pedidos"] * 100
    return {
        "crecimiento_monto": round(crecimiento_monto, 1),
        "crecimiento_pedidos": round(crecimiento_pedidos, 1),
    }


def agente_analista_groq(rows_a, stats_a, label_a, rows_b, stats_b, label_b, comparacion):
    """Agente 1 (Groq / Llama 3.3): analiza los datos crudos y devuelve insights estructurados en JSON."""
    tabla_a = "\n".join(
        f"{r['dia_label']} | pedidos={r['order_count']} | monto={r['total_amount']:.2f}" for r in rows_a
    )

    prompt = f"""Eres un analista de datos para Willy's Pizza, una pizzeria que vende online. Te paso ventas DIARIAS de dos periodos.

PERIODO A ({label_a}):
{tabla_a}
Estadisticas A: {json.dumps(stats_a, ensure_ascii=False)}

PERIODO B / comparacion ({label_b}):
Estadisticas B: {json.dumps(stats_b, ensure_ascii=False)}

Comparacion A vs B: {json.dumps(comparacion, ensure_ascii=False)}

Devolve UNICAMENTE un JSON valido (sin texto adicional, sin markdown) con esta forma exacta:
{{
  "resumen": "string con el resumen del periodo A: total pedidos, total monto, ticket promedio",
  "comparacion": "string explicando si A creció o cayó vs B, con los porcentajes",
  "mejor_peor_dia": "string identificando mejor y peor dia de A con sus montos",
  "dia_semana_destacado": "string sobre que dia de la semana concentra mas ventas y por que podria ser relevante",
  "anomalias": "string con cualquier dato atipico relevante (picos, caidas fuertes)",
  "recomendacion": "string con una recomendacion concreta y accionable, maximo 3 lineas"
}}"""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def _resumen_breve_fallback(insights):
    partes = [
        insights.get("resumen", ""),
        insights.get("comparacion", ""),
        insights.get("recomendacion", ""),
    ]
    return " ".join(p.strip() for p in partes if p.strip())


def agente_redactor_hf(insights, label_a, label_b):
    """Agente 2 (Hugging Face Inference API): toma los insights del analista y redacta un resumen ejecutivo breve en espanol."""
    prompt = f"""Eres un redactor ejecutivo. A partir de estos insights ya analizados sobre las ventas de "Willy's Pizza" (periodo analizado: {label_a}, comparado contra: {label_b}), escribi un RESUMEN EJECUTIVO BREVE en espanol de 4 a 6 oraciones, en un solo parrafo, sin titulos, sin markdown, sin listas. Debe mencionar el desempeno del periodo, la comparacion contra el periodo anterior, y cerrar con la recomendacion principal.

Insights:
{json.dumps(insights, ensure_ascii=False, indent=2)}"""

    try:
        hf_client = OpenAI(
            api_key=os.environ["HF_API_TOKEN"],
            base_url="https://router.huggingface.co/v1",
        )
        response = hf_client.chat.completions.create(
            model="meta-llama/Llama-3.1-8B-Instruct",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        texto = (response.choices[0].message.content or "").strip()
        return texto if texto else _resumen_breve_fallback(insights)
    except Exception:
        return _resumen_breve_fallback(insights)


def fetch_trend_chart(rows_a, label_a):
    labels = [r["dia_label"] for r in rows_a]
    data = [round(r["total_amount"], 2) for r in rows_a]
    chart_config = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": f"Ventas diarias - {label_a}",
                "data": data,
                "backgroundColor": "rgba(255,199,0,0.3)",
                "borderColor": "#000000",
                "borderWidth": 2,
                "fill": True,
                "pointBackgroundColor": "#FFC700",
                "pointBorderColor": "#000000",
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
    return _quickchart(chart_config)


def fetch_comparison_chart(stats_a, label_a, stats_b, label_b):
    chart_config = {
        "type": "bar",
        "data": {
            "labels": [label_a, label_b],
            "datasets": [
                {
                    "label": "Monto total (UYU)",
                    "data": [stats_a["total_monto"], stats_b["total_monto"]],
                    "backgroundColor": "#FFC700",
                    "borderColor": "#000000",
                    "borderWidth": 2,
                    "yAxisID": "y",
                },
                {
                    "label": "Pedidos totales",
                    "data": [stats_a["total_pedidos"], stats_b["total_pedidos"]],
                    "backgroundColor": "#000000",
                    "borderColor": "#FFC700",
                    "borderWidth": 2,
                    "yAxisID": "y1",
                },
            ],
        },
        "options": {
            "plugins": {"legend": {"labels": {"color": "#000000"}}},
            "scales": {
                "x": {"ticks": {"color": "#000000"}},
                "y": {"type": "linear", "position": "left", "ticks": {"color": "#000000"}},
                "y1": {"type": "linear", "position": "right", "ticks": {"color": "#000000"}, "grid": {"drawOnChartArea": False}},
            },
        },
    }
    return _quickchart(chart_config)


def fetch_ticket_chart(stats_a, label_a, stats_b, label_b):
    chart_config = {
        "type": "bar",
        "data": {
            "labels": [label_a, label_b],
            "datasets": [{
                "label": "Ticket promedio (UYU)",
                "data": [stats_a["ticket_promedio"], stats_b["ticket_promedio"]],
                "backgroundColor": ["#FFC700", "#000000"],
                "borderColor": "#000000",
                "borderWidth": 2,
            }],
        },
        "options": {
            "plugins": {"legend": {"display": False}},
            "scales": {
                "x": {"ticks": {"color": "#000000"}},
                "y": {"ticks": {"color": "#000000"}},
            },
        },
    }
    return _quickchart(chart_config)


def fetch_weekday_chart(stats_a, label_a):
    orden = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
    data = [stats_a["monto_por_dia_semana"].get(d, 0) for d in orden]
    chart_config = {
        "type": "bar",
        "data": {
            "labels": orden,
            "datasets": [{
                "label": f"Monto por dia de la semana - {label_a}",
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
    return _quickchart(chart_config)


def _quickchart(chart_config):
    url = "https://quickchart.io/chart"
    resp = requests.post(
        url,
        json={
            "chart": chart_config,
            "width": 700,
            "height": 380,
            "backgroundColor": "white",
            "version": "3",
        },
        timeout=60,
    )
    if not resp.ok:
        raise RuntimeError(f"QuickChart error ({resp.status_code}): {resp.text[:300]}")
    return io.BytesIO(resp.content)


def build_pdf(rows_a, stats_a, label_a, stats_b, label_b, resumen_breve,
              trend_img, ticket_img, comparison_img, weekday_img):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle("TitleY", parent=styles["Title"], textColor=AMARILLO, backColor=NEGRO,
                                  alignment=1, spaceAfter=0, leading=28, fontSize=20, leftIndent=0)
    subtitle_style = ParagraphStyle("Subtitle", parent=styles["Normal"], textColor=colors.HexColor("#555555"),
                                     alignment=1, spaceBefore=6, spaceAfter=14, fontSize=10)
    h2_style = ParagraphStyle("H2", parent=styles["Heading2"], textColor=NEGRO,
                               spaceBefore=14, spaceAfter=6, fontSize=14)
    body_style = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=10.5, leading=15)
    metric_label_style = ParagraphStyle("MetricLabel", parent=styles["Normal"], fontSize=9,
                                         textColor=colors.HexColor("#555555"), alignment=1)
    metric_value_style = ParagraphStyle("MetricValue", parent=styles["Normal"], fontSize=15,
                                         textColor=NEGRO, alignment=1, spaceBefore=2, fontName="Helvetica-Bold")

    elements = []
    elements.append(Paragraph("WILLY'S PIZZA &mdash; Reporte de Ventas", title_style))
    elements.append(Paragraph(f"Periodo analizado: {label_a} &nbsp;|&nbsp; Comparado contra: {label_b}", subtitle_style))

    elements.append(Paragraph("Resumen ejecutivo", h2_style))
    elements.append(Paragraph(resumen_breve, body_style))
    elements.append(Spacer(1, 10))

    metric_data = [[
        Paragraph("Monto total", metric_label_style),
        Paragraph("Pedidos totales", metric_label_style),
        Paragraph("Ticket promedio", metric_label_style),
    ], [
        Paragraph(f"$ {stats_a['total_monto']:,.0f}", metric_value_style),
        Paragraph(str(stats_a["total_pedidos"]), metric_value_style),
        Paragraph(f"$ {stats_a['ticket_promedio']:,.0f}", metric_value_style),
    ]]
    metric_tbl = Table(metric_data, colWidths=[5.6 * cm, 5.6 * cm, 5.6 * cm])
    metric_tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#dddddd")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f7f7f7")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(metric_tbl)
    elements.append(Spacer(1, 16))

    elements.append(Paragraph(f"Ventas por dia - {label_a}", h2_style))
    trend_img.seek(0)
    elements.append(Image(trend_img, width=17 * cm, height=9.3 * cm))

    elements.append(PageBreak())
    elements.append(Paragraph("Ticket promedio: periodo analizado vs comparativo", h2_style))
    ticket_img.seek(0)
    elements.append(Image(ticket_img, width=14 * cm, height=8.5 * cm))

    elements.append(Spacer(1, 16))
    elements.append(Paragraph(f"Comparacion: {label_a} vs {label_b}", h2_style))
    comparison_img.seek(0)
    elements.append(Image(comparison_img, width=17 * cm, height=9.3 * cm))

    elements.append(PageBreak())
    elements.append(Paragraph(f"Ventas por dia de la semana - {label_a}", h2_style))
    elements.append(Paragraph(
        f"El dia de la semana con mas ventas en este periodo es <b>{stats_a['dia_semana_mas_fuerte']}</b>.",
        body_style))
    elements.append(Spacer(1, 6))
    weekday_img.seek(0)
    elements.append(Image(weekday_img, width=17 * cm, height=9.3 * cm))

    elements.append(PageBreak())
    elements.append(Paragraph(f"Tabla diaria - {label_a}", h2_style))

    table_data = [["Dia", "Pedidos", "Monto (UYU)", "% Crec. Monto", "% Crec. Pedidos"]]
    for r in rows_a:
        crec_monto = f"{r['amount_growth_percent']:.1f}%" if r["amount_growth_percent"] is not None else "-"
        crec_pedidos = f"{r['order_count_growth_percent']:.1f}%" if r["order_count_growth_percent"] is not None else "-"
        table_data.append([
            r["dia_label"],
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


def generate_report_pdf(file_stream, fecha_inicio_a, fecha_fin_a, fecha_inicio_b=None, fecha_fin_b=None):
    rows = parse_csv(file_stream)

    rows_a = filter_period(rows, fecha_inicio_a, fecha_fin_a)
    if not rows_a:
        raise ValueError("No hay datos de ventas en el periodo a analizar seleccionado.")

    if fecha_inicio_b is None or fecha_fin_b is None:
        fecha_inicio_b, fecha_fin_b = default_comparison_period(fecha_inicio_a, fecha_fin_a)
    rows_b = filter_period(rows, fecha_inicio_b, fecha_fin_b)

    stats_a = compute_period_stats(rows_a)
    stats_b = compute_period_stats(rows_b)

    label_a = f"{fecha_inicio_a.strftime('%d/%m/%Y')} - {fecha_fin_a.strftime('%d/%m/%Y')}"
    label_b = f"{fecha_inicio_b.strftime('%d/%m/%Y')} - {fecha_fin_b.strftime('%d/%m/%Y')}"

    comparacion = compute_comparison(stats_a, stats_b) if stats_b else {"crecimiento_monto": None, "crecimiento_pedidos": None}
    stats_b_safe = stats_b or {
        "desde": "-", "hasta": "-", "dias_con_ventas": 0, "total_pedidos": 0, "total_monto": 0,
        "ticket_promedio": 0, "promedio_diario_monto": 0, "promedio_diario_pedidos": 0,
        "mejor_dia": "-", "mejor_dia_monto": 0, "peor_dia": "-", "peor_dia_monto": 0,
        "dia_semana_mas_fuerte": "-", "monto_por_dia_semana": {},
    }

    insights = agente_analista_groq(rows_a, stats_a, label_a, rows_b, stats_b_safe, label_b, comparacion)
    resumen_breve = agente_redactor_hf(insights, label_a, label_b)

    trend_img = fetch_trend_chart(rows_a, label_a)
    ticket_img = fetch_ticket_chart(stats_a, label_a, stats_b_safe, label_b)
    comparison_img = fetch_comparison_chart(stats_a, label_a, stats_b_safe, label_b)
    weekday_img = fetch_weekday_chart(stats_a, label_a)

    return build_pdf(rows_a, stats_a, label_a, stats_b_safe, label_b, resumen_breve,
                      trend_img, ticket_img, comparison_img, weekday_img)
