import os
import traceback

from flask import Flask, render_template, request, send_file, flash, redirect, url_for
from dotenv import load_dotenv

load_dotenv()

from report_generator import generate_report_pdf

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "willys-pizza-dev-secret")


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/generar", methods=["POST"])
def generar():
    file = request.files.get("csv_file")
    if not file or file.filename == "":
        flash("Subi un archivo CSV primero.")
        return redirect(url_for("index"))

    try:
        pdf_buffer = generate_report_pdf(file.stream)
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
