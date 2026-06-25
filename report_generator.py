import io
import os
import json
import urllib.parse

import pandas as pd
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


def parse_csv(file_stream):
    df = pd.read_csv(file_stream, thousands=",")
    df = df.dropna(how="all")
    df = df.dropna(subset=["created_at_gmt3", "total_amount"])
    df["created_at_gmt3"] = pd.to_datetime(df["created_at_gmt3"], format="mixed")
    df = df.sort_values("created_at_gmt3").reset_index(drop=True)
    df["mes_label"] = df["created_at_gmt3"].dt.strftime("%b %Y")
    return df


def compute_stats(df):
    best_idx = df["total_amount"].idxmax()
    worst_idx = df["total_amount"].idxmin()
    return {
        "periodo_inicio": df["mes_label"].iloc[0],
        "periodo_fin": df["mes_label"].iloc[-1],
        "total_pedidos": int(df["order_count"].sum()),
        "total_monto": float(df["total_amount"].sum()),
        "promedio_monto": float(df["total_amount"].mean()),
        "promedio_pedidos": float(df["order_count"].mean()),
        "mejor_mes": df["mes_label"].loc[best_idx],
        "mejor_mes_monto": float(df["total_amount"].loc[best_idx]),
        "peor_mes": df["mes_label"].loc[worst_idx],
        "peor_mes_monto": float(df["total_amount"].loc[worst_idx]),
        "ultimo_mes": df["mes_label"].iloc[-1],
        "ultimo_mes_monto": float(df["total_amount"].iloc[-1]),
        "ultimo_crecimiento": df["amount_growth_percent"].iloc[-1] if pd.notna(df["amount_growth_percent"].iloc[-1]) else None,
    }


def call_gemini(df, stats):
    model = genai.GenerativeModel("gemini-2.0-flash")
    tabla = df[["mes_label", "order_count", "total_amount", "amount_growth_percent", "order_count_growth_percent"]].to_string(index=False)

    prompt = f"""Eres un analista financiero experto en negocios gastronomicos. A continuacion te paso los datos de ventas mensuales de "Willy's Pizza", una pizzeria, extraidos de su plataforma de pedidos online.

Datos (mes | cantidad de pedidos | monto total en UYU | % crecimiento de monto vs mes anterior | % crecimiento de pedidos vs mes anterior):

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


def fetch_chart_image(df):
    last6 = df.tail(6)
    labels = list(last6["mes_label"])
    data = [round(v, 2) for v in last6["total_amount"]]
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


def build_pdf(df, stats, report_text, banner_img, chart_img):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle("TitleY", parent=styles["Title"], textColor=AMARILLO, backColor=NEGRO,
                                  alignment=1, spaceAfter=0, leading=28, fontSize=22, leftIndent=0)
    h2_style = ParagraphStyle("H2", parent=styles["Heading2"], textColor=NEGRO, borderColor=AMARILLO,
                               borderWidth=0, spaceBefore=14, spaceAfter=6, fontSize=14)
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
    for _, row in df.iterrows():
        crec_monto = f"{row['amount_growth_percent']:.1f}%" if pd.notna(row['amount_growth_percent']) else "-"
        crec_pedidos = f"{row['order_count_growth_percent']:.1f}%" if pd.notna(row['order_count_growth_percent']) else "-"
        table_data.append([
            row["mes_label"],
            str(int(row["order_count"])),
            f"{row['total_amount']:,.2f}",
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
    df = parse_csv(file_stream)
    stats = compute_stats(df)
    report_text = call_gemini(df, stats)
    banner_img = fetch_banner_image()
    chart_img = fetch_chart_image(df)
    return build_pdf(df, stats, report_text, banner_img, chart_img)
