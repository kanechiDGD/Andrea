from flask import Flask, render_template, request, redirect, url_for
import pandas as pd
import os
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import io

app = Flask(__name__, template_folder="templates")

# =============================
# 1️⃣ Configurar Google Sheets y Google Drive
# =============================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive"
]

# ✅ Asegurar ruta absoluta del archivo JSON
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, "flaskapp-451604.json")

SHEET_ID = "1kWaIuALe7gINHVBRTRaeCCLn8Uu-J1ZrtOejk1AmZeg"  # Google Sheets ID
DRIVE_FILE_ID = "1vgucV7LtzOO0laXz-_PZIwqHAp7CQM0N"  # Google Drive File ID

# 🔹 Autenticación con Google
if os.path.exists(SERVICE_ACCOUNT_FILE):
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).sheet1
    print("✅ Conexión a Google Sheets exitosa")
else:
    sheet = None
    print("❌ No se encontró el archivo de credenciales JSON. Asegúrate de que esté en la carpeta correcta.")

def get_drive_service():
    """Devuelve una conexión autenticada con Google Drive."""
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)

# =============================
# 2️⃣ Funciones para manejar los datos
# =============================

def descargar_excel_drive():
    """Descarga el archivo Excel desde Google Drive."""
    try:
        service = get_drive_service()
        request = service.files().get_media(fileId=DRIVE_FILE_ID)
        file_stream = io.BytesIO()
        downloader = MediaIoBaseDownload(file_stream, request)
        
        done = False
        while not done:
            _, done = downloader.next_chunk()

        file_stream.seek(0)
        df = pd.read_excel(file_stream, engine="openpyxl")
        print("✅ Datos descargados correctamente desde Google Drive.")
        return df
    except Exception as e:
        print(f"❌ Error descargando desde Google Drive: {e}")
        return pd.DataFrame(columns=["Nombre del Cliente", "Dirección", "Suplementado"])

def subir_excel_drive(df):
    """Sube el archivo Excel actualizado a Google Drive."""
    try:
        service = get_drive_service()
        file_stream = io.BytesIO()
        df.to_excel(file_stream, index=False, engine="openpyxl")
        file_stream.seek(0)

        media = MediaIoBaseUpload(file_stream, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", resumable=True)
        service.files().update(fileId=DRIVE_FILE_ID, media_body=media).execute()
        print("✅ Datos subidos correctamente a Google Drive.")
    except Exception as e:
        print(f"❌ Error subiendo datos a Google Drive: {e}")

def cargar_datos():
    """Carga los datos desde Google Drive."""
    return descargar_excel_drive()

def guardar_datos(df):
    """Guarda los datos en Google Drive."""
    subir_excel_drive(df)

# =============================
# 3️⃣ Rutas de la Aplicación Flask
# =============================

@app.route("/", methods=["GET", "POST"])
def index():
    df = cargar_datos()

    if request.method == "POST":
        for index, row in df.iterrows():
            key = f"suplementado_{index}"
            if key in request.form:
                df.at[index, "Suplementado"] = request.form[key]

        guardar_datos(df)
        return redirect(url_for("index"))

    return render_template("form.html", clientes=df.iterrows())

@app.route("/add", methods=["POST"])
def add_cliente():
    """Agregar un nuevo cliente desde el formulario."""
    nombre = request.form.get("nombre")
    direccion = request.form.get("direccion")
    suplementado = request.form.get("suplementado", "No")

    if nombre and direccion:
        df = cargar_datos()
        df.loc[len(df)] = [nombre, direccion, suplementado]
        guardar_datos(df)

    return redirect(url_for("index"))

@app.route("/sync")
def sync_data():
    """Ruta para forzar sincronización con Google Drive."""
    df = cargar_datos()
    guardar_datos(df)
    return "✅ Datos sincronizados con Google Drive."

if __name__ == "__main__":
    app.run(debug=True)
