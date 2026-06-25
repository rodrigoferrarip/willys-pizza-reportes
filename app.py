import io
import os
import json
import traceback
from datetime import datetime

from flask import Flask, render_template, request, send_file, flash, redirect, url_for
from dotenv import load_dotenv

load_dotenv()

from report_generator import generate_report_pdf

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "willys-pizza-dev-secret")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
LAST_CSV_PATH = os.path.join(DATA_DIR, "last_upload.csv")
LAST_CSV_META_PATH = os.path.join(DATA_DIR, "last_upload_meta.json")
os.makedirs(DATA_DIR, exist_ok=True)


def _parse_date(value, end_of_day=False):
    if not value:
        return None
    dt = datetime.strptime(value, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt


def _get_last_csv_meta():
    if not os.path.exists(LAST_CSV_META_PATH):
        return None
    with open(LAST_CSV_META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_uploaded_csv(file):
    file.stream.seek(0)
    content = file.stream.read()
    with open(LAST_CSV_PATH, "wb") as f:
        f.write(content)
    meta = {
        "filename": file.filename,
        "uploaded_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }
    with open(LAST_CSV_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return io.BytesIO(content)


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", last_csv=_get_last_csv_meta())


@app.route("/generar", methods=["POST"])
def generar():
    file = request.files.get("csv_file")

    if file and file.filename != "":
        stream = _save_uploaded_csv(file)
    elif os.path.exists(LAST_CSV_PATH):
        with open(LAST_CSV_PATH, "rb") as f:
            stream = io.BytesIO(f.read())
    else:
        flash("No hay ningun CSV subido todavia. Subi uno primero.")
        return redirect(url_for("index"))

    fecha_inicio_a = _parse_date(request.form.get("fecha_inicio_a"))
    fecha_fin_a = _parse_date(request.form.get("fecha_fin_a"), end_of_day=True)
    fecha_inicio_b = _parse_date(request.form.get("fecha_inicio_b"))
    fecha_fin_b = _parse_date(request.form.get("fecha_fin_b"), end_of_day=True)

    if not fecha_inicio_a or not fecha_fin_a:
        flash("Indica el periodo a analizar (fecha desde y hasta).")
        return redirect(url_for("index"))

    try:
        pdf_buffer = generate_report_pdf(
            stream, fecha_inicio_a, fecha_fin_a, fecha_inicio_b, fecha_fin_b
        )
    except Exception as exc:
        traceback.print_exc()
        flash(f"Error generando el reporte: {exc}")
        return redirect(url_for("index"))

    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name="reporte_willys_pizza.pdf",
        mimetype="application/pdf",
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
