# merged_app.py - Código unificado
from datetime import datetime, timedelta
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from google.oauth2 import service_account
import io
from googleapiclient.http import MediaIoBaseUpload
import os
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, session, flash, get_flashed_messages # Importar flash, get_flashed_messages

# --- Configuración de Flask ---
# Asumiendo que tus plantillas HTML están en una carpeta 'templates'
# dentro del mismo directorio que este archivo Python.
app = Flask(__name__, template_folder='templates')
app.secret_key = 'super-secreto-unificado-y-mas-seguro' # Usar una clave más robusta en producción

# --- Configuración de Subida de Archivos (Necesaria para upload_foto) ---
# Asegúrate de crear esta carpeta o ajusta la ruta
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- Datos en memoria (desde main.py) ---
USERS = {'admin@example.com': '1234'} # Credenciales de empleados

TASKS = {                # tareas por usuario
    "admin@example.com": []   # cada tarea: {"texto": "...", "done": False}
}

BITACORA_MEMORIA = {             # entradas por cliente_id (usamos un nombre diferente para no confundir con bitacora de sheets)
    # "CL001": [ {"autor": "...", "nota": "...", "fecha": "2024-06-10"} ]
}

# --- Caché global (desde app.py) ---
cache_sheets = {}

def get_sheet_data_cached(sheet_name, func_get_records):
    ahora = datetime.now()

    if sheet_name in cache_sheets:
        datos_cached = cache_sheets[sheet_name]
        if (ahora - datos_cached["timestamp"]).seconds < 60:
            return datos_cached["data"]

    nuevos_datos = func_get_records(sheet_name)
    cache_sheets[sheet_name] = {
        "data": nuevos_datos,
        "timestamp": ahora
    }
    return nuevos_datos

# =============================
# 🔹 Configuración Google Sheets (desde app.py)
# =============================
SHEET_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive"
]
SHEET_CREDS_FILE = "flaskapp-451604.json" # Asegúrate de que este archivo esté en la raíz
SHEET_ID = "1DjuzaBEznmE3Tp6cWH7jskEKHte8CyGwvhLYBXq-jrM"

# **IMPORTANTE**: Asegúrate de que estas variables estén inicializadas a None ANTES del try
# para evitar NameError si el try falla.
sheet_creds = None
sheet_client = None
sheet_hoja1 = None
sheet_log = None
sheet_construction = None

try:
    sheet_creds = Credentials.from_service_account_file(
        SHEET_CREDS_FILE, scopes=SHEET_SCOPES
    )
    sheet_client = gspread.authorize(sheet_creds)
    sheet_hoja1 = sheet_client.open_by_key(SHEET_ID).worksheet("Hoja 1")
    sheet_log   = sheet_client.open_by_key(SHEET_ID).worksheet("log")
    sheet_construction   = sheet_client.open_by_key(SHEET_ID).worksheet("Construction")
except Exception as e:
    # --- ¡ESTAS SON LAS LÍNEAS QUE DEBES AÑADIR O MODIFICAR AQUÍ! ---
    print(f"Error al inicializar Google Sheets API: {e}")
    import traceback # Asegúrate de que esta línea esté aquí
    traceback.print_exc() # <-- Esto imprimirá la traza completa de la excepción
    # Las variables se mantienen como None si hay un error, como ya estaban.
    sheet_hoja1 = None
    sheet_log = None
    sheet_construction = None

# =============================
# 🔹 Configuración Google Drive (desde app.py)
# =============================
DRIVE_SCOPES         = ["https://www.googleapis.com/auth/drive"]
DRIVE_CREDS_FILE     = "drive-creds.json" # Asegúrate de que este archivo esté en la raíz
PDF_POLICY_PARENT_ID = "1ATyyR2xScAZr4VR7p2v4WOkf6b4s9RVi"

try:
    drive_creds = service_account.Credentials.from_service_account_file(
        DRIVE_CREDS_FILE, scopes=DRIVE_SCOPES
    )
    drive_service = build('drive', 'v3', credentials=drive_creds)
except Exception as e:
    # --- CAMBIO AQUÍ: EL MENSAJE DE ERROR DEBE REFERIRSE A GOOGLE DRIVE ---
    print(f"Error al inicializar Google Drive API: {e}")
    import traceback # Asegúrate de que esta línea esté aquí
    traceback.print_exc() # Esto imprimirá la traza completa de la excepción
    drive_service = None # Para evitar errores si la API no se inicializa

def ensure_client_folder(nombre: str) -> str:
    if not drive_service:
        print("Drive service no inicializado. No se puede crear carpeta.")
        return None # O lanzar una excepción
    query = (
        "mimeType='application/vnd.google-apps.folder' "
        f"and name='{nombre}' and '{PDF_POLICY_PARENT_ID}' in parents and trashed = false"
    )
    resp = drive_service.files().list(
        q=query,
        spaces='drive',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields='files(id)'
    ).execute()
    files = resp.get('files', [])
    if files:
      return files[0]['id']

    metadata = {
      'name': nombre,
      'mimeType': 'application/vnd.google-apps.folder',
      'parents': [PDF_POLICY_PARENT_ID]
    }
    folder = drive_service.files().create(
      body=metadata,
      supportsAllDrives=True,
      fields='id'
    ).execute()
    return folder['id']

# =============================
# 🔹 Decorador de Autenticación (desde main.py)
# =============================
def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapper

# =============================
# 🔹 Rutas de Autenticación (desde main.py)
# =============================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        pwd   = request.form['password']
        if email in USERS and USERS[email] == pwd:
            session['user'] = email
            # Redirigir al dashboard de empleados después del login
            return redirect(url_for('dashboard'))
        flash("Credenciales inválidas", "error") # Usa flash para mensajes de error
        return render_template('login.html', messages=get_flashed_messages(with_categories=True))
    return render_template('login.html', messages=get_flashed_messages(with_categories=True))

@app.route('/logout')
def logout():
    session.pop('user', None)
    flash("Has cerrado sesión correctamente.", "info") # Mensaje de logout
    return redirect(url_for('login'))

# =============================
# 🔹 Rutas Públicas (desde main.py, accesibles sin login)
# =============================
@app.route('/')
def inicio():
    return render_template('index.html') # Siempre muestra la página de inicio

@app.route('/mision-vision')
def mision_vision():
    return render_template('mision_vision.html')

@app.route('/servicios')
def servicios():
    return render_template('servicios.html')

# ... tu código existente ...

@app.route('/proceso')
def proceso():
    steps = [
        (1, 'Free Inspection', 'With your approval, we inspect the damages and take photos. We review your policy before proceeding.',  'step1.jpg', '<i class="fas fa-search-dollar"></i>'),
        (2, 'Agreements',            'You sign with the Public Adjuster and with us to formally authorize the process.',                     'step2.jpg', '<i class="fas fa-file-signature"></i>'),
        (3, 'Claim Management', 'We communicate with your insurer and manage everything for you.',                                         'step3.jpg', '<i class="fas fa-headset"></i>'),
        (4, 'Approval & Checks','We await approval; you will receive the first check and others if applicable.',                                  'step4.jpg', '<i class="fas fa-money-check-alt"></i>'),
        (5, 'Construction',        'You choose style and colors. We rebuild with quality materials.',                                    'step5.jpg', '<i class="fas fa-hard-hat"></i>'),
        (6, 'Job Finished!', 'Final review, warranty delivery, and project closing.',                                              'step6.jpg', '<i class="fas fa-check-circle"></i>')
    ]
    return render_template('proceso.html', steps=steps) # <-- Pasa la variable steps a la plantilla

# ... tu código existente ...

@app.route('/galeria')
def galeria():
    return render_template('galeria.html')

# =============================
# 🔹 Rutas y Lógica del Dashboard y Tareas (desde main.py y app.py)
# Se unifica el concepto de Dashboard, usando la integración de Google Sheets de app.py
# y manteniendo las tareas en memoria de main.py
# =============================
@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    user = session['user']
    
    # Lógica de manejo de tareas (desde main.py)
    if request.method == 'POST':
        texto = request.form.get('tarea', '').strip()
        if texto:
            TASKS.setdefault(user, []).append({"texto": texto, "done": False})
    
    tareas_usuario = TASKS.get(user, []) # Tareas específicas del usuario logueado

    # Obtener datos para el dashboard (desde app.py, usando Google Sheets)
    data_sheets = obtener_datos()
    eventos_calendario_combinados = combinar_eventos()
    
    return render_template('dashboard.html',
                           tareas=tareas_usuario, # Tareas en memoria
                           user=user,
                           data=data_sheets, # Datos de Google Sheets (estadísticas)
                           eventos=eventos_calendario_combinados) # Eventos de calendario

# Marcar tarea completada (desde main.py)
@app.route('/task/<int:t_idx>/toggle')
@login_required
def toggle_task(t_idx):
    user = session['user']
    tareas = TASKS.get(user, [])
    if 0 <= t_idx < len(tareas):
        tareas[t_idx]["done"] = not tareas[t_idx]["done"]
    return redirect(url_for('dashboard'))

# =============================
# 🔹 Rutas para subir PDFs directamente a Drive (desde app.py)
# =============================
@app.route("/upload_poliza/<int:cliente_id>", methods=["POST"])
@login_required # Proteger rutas sensibles
def upload_poliza(cliente_id):
    if not drive_service:
        flash("Servicio de Drive no disponible. Contacta al administrador.", "error")
        return redirect(request.referrer or url_for('clients'))

    file = request.files.get("poliza")
    if not file or not file.filename.lower().endswith(".pdf"):
        flash("Selecciona un PDF de póliza válido.", "error")
        return redirect(request.referrer)

    buf = io.BytesIO(file.read())
    cliente = next((c for c in obtener_clientes_log()
                    if str(c["id"]) == str(cliente_id)), None)
    if not cliente:
        flash("Cliente no encontrado para Drive.", "error")
        return redirect(request.referrer)

    folder_id = ensure_client_folder(cliente["nombre"])
    if not folder_id:
        flash("No se pudo crear/encontrar la carpeta del cliente en Drive.", "error")
        return redirect(request.referrer)

    media = MediaIoBaseUpload(buf, mimetype='application/pdf')
    try:
        drive_service.files().create(
            body={
                'name': secure_filename(file.filename),
                'parents': [folder_id]
            },
            media_body=media,
            supportsAllDrives=True,
            fields='id'
        ).execute()
        flash("Póliza subida a Drive con éxito.", "success")
    except Exception as e:
        flash(f"Error al subir póliza a Drive: {e}", "error")

    return redirect(request.referrer)


@app.route("/upload_contrato/<int:cliente_id>", methods=["POST"])
@login_required # Proteger rutas sensibles
def upload_contrato(cliente_id):
    if not drive_service:
        flash("Servicio de Drive no disponible. Contacta al administrador.", "error")
        return redirect(request.referrer or url_for('clients'))

    file = request.files.get("contrato")
    if not file or not file.filename.lower().endswith(".pdf"):
        flash("Selecciona un PDF de contrato válido.", "error")
        return redirect(request.referrer)

    buf = io.BytesIO(file.read())
    cliente = next((c for c in obtener_clientes_log()
                    if str(c["id"]) == str(cliente_id)), None)
    if not cliente:
        flash("Cliente no encontrado para Drive.", "error")
        return redirect(request.referrer)

    folder_id = ensure_client_folder(cliente["nombre"])
    if not folder_id:
        flash("No se pudo crear/encontrar la carpeta del cliente en Drive.", "error")
        return redirect(request.referrer)

    media = MediaIoBaseUpload(buf, mimetype='application/pdf')
    try:
        drive_service.files().create(
            body={
                'name': secure_filename(file.filename),
                'parents': [folder_id]
            },
            media_body=media,
            fields='id',
            supportsAllDrives=True
        ).execute()
        flash("Contrato subida a Drive con éxito.", "success")
    except Exception as e:
        flash(f"Error al subir contrato a Drive: {e}", "error")

    return redirect(request.referrer)


# =============================
# 🔹 Funciones de obtención de datos (desde app.py)
# =============================
def obtener_datos():
    if not sheet_hoja1: # Si la hoja no se inicializó
        return {
            "total_clientes": 0, "contacto_atrasado": 0, "no_suplementado": 0,
            "pendientes_someter": 0, "listas_para_construir": 0, "contactos_proximos": []
        }
    
    all_values = sheet_hoja1.get_values("A1:Z200")
    if not all_values or len(all_values) < 2: # Asegurar que hay al menos encabezados y una fila de datos
        return {
            "total_clientes": 0, "contacto_atrasado": 0, "no_suplementado": 0,
            "pendientes_someter": 0, "listas_para_construir": 0, "contactos_proximos": []
        }
        
    df = pd.DataFrame(all_values)
    df.columns = df.iloc[0].str.strip().str.replace(r"[^\w\s]", "", regex=True)
    df = df[1:].reset_index(drop=True)

    total_clientes = contacto_atrasado = no_suplementado = pendientes_someter = listas_para_construir = 0
    contactos_proximos = []

    if "Nombre del Cliente" in df.columns:
        total_clientes = df["Nombre del Cliente"].replace("", pd.NA).dropna().shape[0]

    fecha_col = "FECHA DE ULTIMO CONTACTO"
    if fecha_col in df.columns:
        # Asegurarse de manejar diferentes formatos de fecha si es necesario
        df[fecha_col] = pd.to_datetime(df[fecha_col], errors="coerce", dayfirst=False) # Considera dayfirst=True si el formato es DD/MM/YYYY
        fecha_limite = datetime.today() - timedelta(days=7)
        contacto_atrasado = df[df[fecha_col].notna() & (df[fecha_col] < fecha_limite)].shape[0]

    if "Suplementado" in df.columns:
        df["Suplementado"] = df["Suplementado"].astype(str).str.strip().str.lower()
        no_suplementado = df[df["Suplementado"] == "no"].shape[0]

    if "Estatus" in df.columns:
        df["Estatus"] = df["Estatus"].astype(str).str.strip().str.upper()
        pendientes_someter = df[df["Estatus"] == "NO SOMETIDA"].shape[0]
        if "PRIMER CHEQUE" in df.columns:
            df["PRIMER CHEQUE"] = df["PRIMER CHEQUE"].astype(str).str.strip().str.upper()
            listas_para_construir = df[
                (df["Estatus"] == "APROVADA") & (df["PRIMER CHEQUE"] == "OBTENIDO")
            ].shape[0]
        else:
            listas_para_construir = 0

    if fecha_col in df.columns:
        fecha_actual = datetime.today()
        fecha_limite = fecha_actual + timedelta(days=7)
        # Solo filtrar por fechas válidas
        df_contactos = df[df[fecha_col].notna() & (df[fecha_col] >= fecha_actual) & (df[fecha_col] <= fecha_limite)]
        for _, row in df_contactos.iterrows():
            dias_restantes = (row[fecha_col] - fecha_actual).days
            contactos_proximos.append({
                "id": _ + 1,
                "nombre": row.get("Nombre del Cliente", "Desconocido"),
                "direccion": row.get("Dirección", "No disponible"),
                "ultimo_contacto": row[fecha_col].strftime("%m/%d/%Y"),
                "dias_restantes": dias_restantes,
                "estatus": row.get("Estatus", "No especificado")
            })

    return {
        "total_clientes": total_clientes,
        "contacto_atrasado": contacto_atrasado,
        "no_suplementado": no_suplementado,
        "pendientes_someter": pendientes_someter,
        "listas_para_construir": listas_para_construir,
        "contactos_proximos": contactos_proximos
    }

def obtener_eventos():
    CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
    CALENDAR_CREDS_FILE = "static/credentials.json" # Asegúrate de que este archivo exista en static/

    if not os.path.exists(CALENDAR_CREDS_FILE):
        print(f"Advertencia: Archivo de credenciales de calendario no encontrado en {CALENDAR_CREDS_FILE}")
        return []

    try:
        credentials = service_account.Credentials.from_service_account_file(
            CALENDAR_CREDS_FILE, scopes=CALENDAR_SCOPES)
        service = build('calendar', 'v3', credentials=credentials)

        calendar_id = 'andrealizeth768@gmail.com' # Asegúrate de que este ID sea correcto
        now = datetime.utcnow().isoformat() + 'Z'
        events_result = service.events().list(
            calendarId=calendar_id, timeMin=now, maxResults=10,
            singleEvents=True, orderBy='startTime'
        ).execute()

        eventos = []
        for e in events_result.get('items', []):
            start = e['start'].get('dateTime') or e['start'].get('date')
            end = e['end'].get('dateTime') or e['end'].get('date')
            if start and end and 'T' in start:
                try: # Manejo de posible error en parseo de fecha
                    hora_inicio = datetime.fromisoformat(start.replace('Z', '+00:00')).strftime("%I:%M %p")
                    hora_fin    = datetime.fromisoformat(end.replace('Z', '+00:00')).strftime("%I:%M %p")
                    hora = f"{hora_inicio} - {hora_fin}"
                except ValueError:
                    hora = "Hora no disponible" # Fallback si el formato no es el esperado
            else:
                hora = "Todo el día"
            eventos.append({
                'title': e.get('summary','Sin título'),
                'start': start,
                'hora': hora,
                'address': e.get('location','') # Usar 'location' en lugar de 'Address' para eventos de calendario
            })
        return eventos
    except Exception as e:
        print(f"Error al obtener eventos del calendario: {e}")
        return []

def obtener_clientes_log():
    if not sheet_log: # Si la hoja no se inicializó
        return []
        
    values = sheet_log.get_all_values()
    if not values or len(values) < 1: # Si no hay datos o solo encabezados sin filas
        return []

    df = pd.DataFrame(values[1:], columns=values[0])
    df.columns = df.columns.str.strip()

    clientes = []
    for i, row in df.iterrows():
        cliente = {
            "id": row.get("ID", str(i)),
            "nombre": row.get("Full Name", "").strip(),
            "telefono": row.get("Phone", ""),
            "address": row.get("Address", ""),
            "email": row.get("Email", ""),
            "idioma": row.get("Language", ""),
            "hora_preferida": row.get("Preferred hours", ""),
            "vendedor": row.get("Sold By", ""),
            "estatus": row.get("Status", ""),
            "insurance": row.get("Insurance", ""),
            "deductible": row.get("Deductible", ""),
            "policy_number": row.get("Policy Number", ""),
            "claim_number": row.get("Claim Number", ""),
            "adjuster": row.get("Adjuster", ""),
            "estimate": row.get("Estimate", ""),
            "other_documents": row.get("Other Documents", ""),
            "acciones_recientes": [row.get("Recent Actions", "")] if row.get("Recent Actions") else [],

            "visita_programada": {
                "Date": row.get("Scheduled Visit", "") or "",
                "User": row.get("Sold By", "") or "",
                "Hour": "",
                "Reason": ""
            },

            "ajustacion": {
                "fecha": row.get("Adjustment Date", "") or "",
                "nombre": row.get("Adjuster", "") or "",
                "numero": row.get("Claim Number", "") or ""
            },

            "pdf_poliza_url": "", # Estas URL no se obtienen directamente de Sheets en tu código
            "pdf_contrato_url": "", # Tendrías que implementar lógica para obtener enlaces de Drive
            "notas": "" # Este campo tampoco se obtiene directamente de Sheets
        }
        clientes.append(cliente)
    return clientes

def obtener_tareas_calendario():
    if not sheet_log: # Si la hoja no se inicializó
        return []
        
    log_data = sheet_log.get_all_values()
    if not log_data or len(log_data) < 1: # Si no hay datos o solo encabezados sin filas
        return []

    df = pd.DataFrame(log_data[1:], columns=log_data[0])
    df.columns = df.columns.str.strip()

    tareas = []
    hoy = datetime.today().date()

    for _, row in df.iterrows():
        cliente_id = row.get("ID", "")
        nombre = row.get("Full Name", "").strip()
        scheduled_str = row.get("Scheduled Visit", "").strip()
        adjustment_str = row.get("Adjustment Date", "").strip()

        # Scheduled Visit
        if scheduled_str:
            try:
                fecha = datetime.strptime(scheduled_str, "%m/%d/%Y").date()
                if fecha >= hoy:
                    tareas.append({
                        "title": f"Visit: {nombre}",
                        "start": fecha.strftime("%Y-%m-%d"),
                        "tipo": "Scheduled",
                        "id": cliente_id,
                        "address": row.get("Address", "")
                    })
            except ValueError:
                pass

        # Adjustment Date
        if adjustment_str:
            try:
                # Intentar varios formatos de fecha
                parsed_date = None
                for fmt in ("%m/%d/%Y", "%Y-%m-%d"): # Añadido "%Y-%m-%d" por si la actualización lo guarda así
                    try:
                        parsed_date = datetime.strptime(adjustment_str, fmt).date()
                        break
                    except ValueError:
                        continue
                
                if parsed_date and parsed_date >= hoy:
                    tareas.append({
                        "title": f"Adjustment: {nombre}",
                        "start": parsed_date.strftime("%Y-%m-%d"),
                        "hora": "Todo el día",
                        "tipo": "Adjustment",
                        "id": cliente_id
                    })
            except ValueError:
                pass

    return tareas

def combinar_eventos():
    eventos_gmail = obtener_eventos()
    eventos_crm   = obtener_tareas_calendario()
    return eventos_gmail + eventos_crm

# =============================
# 🔹 Rutas de Gestión de Clientes (desde app.py)
# =============================
@app.route("/clients", methods=["GET"])
@login_required # Proteger ruta
def clients():
    clientes = obtener_clientes_log()
    busqueda = request.args.get("search", "").strip().lower()

    cliente = None
    if busqueda:
        # Busca por ID o por nombre parcial
        coincidencias = [
            c for c in clientes
            if str(c.get("id", "")).strip() == busqueda or busqueda in c.get("nombre", "").lower()
        ]
        cliente = coincidencias[0] if coincidencias else None
    else:
        # Si no hay búsqueda, muestra el primer cliente si existe
        cliente = clientes[0] if clientes else None

    if cliente and sheet_client and sheet_log: # Asegurarse que Google Sheets esté inicializado
        try:
            sheet_log_ws = sheet_client.open_by_key(SHEET_ID).worksheet("log")
            # Usa get_all_records() para obtener dicts más fáciles de manejar
            log_data_records = sheet_log_ws.get_all_records() 
            logs_filtrados = [
                {
                    "usuario": log.get("Log User", "N/A"),
                    "descripcion": log.get("Log Description", "N/A"),
                    "fecha": log.get("Log Date", "N/A"),
                    "tipo": log.get("Log Type", "N/A")
                }
                for log in log_data_records
                if str(log.get("ID", "")).strip() == str(cliente.get("id", "")).strip()
            ]
            cliente["logs"] = logs_filtrados
        except Exception as e:
            flash(f"Error al cargar logs del cliente: {e}", "error")
            cliente["logs"] = []
    
    return render_template("clients.html", cliente=cliente, todos_los_clientes=clientes, messages=get_flashed_messages(with_categories=True))


@app.route("/upload_foto/<int:cliente_id>", methods=["POST"])
@login_required # Proteger ruta
def upload_foto(cliente_id):
    file = request.files.get("foto")
    if not file or file.filename == "":
        flash("Selecciona una imagen.", "error")
        return redirect(request.referrer)
    if not file.filename.lower().endswith((".jpg", ".jpeg", ".png")):
        flash("Solo JPG o PNG.", "error")
        return redirect(request.referrer)

    filename = secure_filename(f"foto_{cliente_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg") # Nombre único
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    try:
        file.save(path)
        flash(f"Foto subida localmente para el cliente {cliente_id}.", "success")
    except Exception as e:
        flash(f"Error al subir la foto: {e}", "error")
    return redirect(request.referrer)

@app.route("/add_log/<cliente_id>", methods=["POST"])
@login_required # Proteger ruta
def add_log(cliente_id):
    if not sheet_client or not sheet_log:
        flash("Servicio de Google Sheets no disponible.", "error")
        return redirect(url_for("clients", search=cliente_id))

    usuario      = request.form.get("User", session.get('user', 'Desconocido')).strip() # Usar usuario de sesión por defecto
    tipo         = request.form.get("Log_Type", "").strip()
    descripcion  = request.form.get("log_description", "").strip()
    fecha_actual = datetime.now().strftime("%m/%d/%Y")

    try:
        sheet_log_ws = sheet_client.open_by_key(SHEET_ID).worksheet("log")
        registros    = sheet_log_ws.get_all_values()
        if not registros:
            flash("Error: No se pudieron obtener los encabezados de la hoja 'log'.", "error")
            return redirect(url_for("clients", search=cliente_id))
        
        encabezados  = [h.strip() for h in registros[0]] # Limpiar encabezados
        
        nueva_fila = [""] * len(encabezados)
        # Usar un diccionario para mapear el índice del encabezado
        header_map = {header: i for i, header in enumerate(encabezados)}

        if "ID" in header_map: fila_log[header_map["ID"]] = cliente_id
        if "Log User" in header_map: fila_log[header_map["Log User"]] = usuario
        if "Log Description" in header_map: fila_log[header_map["Log Description"]] = descripcion
        if "Log Date" in header_map: fila_log[header_map["Log Date"]] = fecha_actual
        if "Log Type" in header_map: fila_log[header_map["Log Type"]] = tipo
        
        sheet_log_ws.append_row(nueva_fila, value_input_option="USER_ENTERED")
        flash("Log agregado correctamente", "success")
    except Exception as e:
        flash(f"Error al agregar log: {e}", "error")

    return redirect(url_for("clients", search=cliente_id))

@app.route("/client_profile")
@login_required # Proteger ruta
def client_profile_selector():
    cliente_id = request.args.get("cliente_id", "").strip()

    if not cliente_id:
        flash("ID de cliente no proporcionado", "error")
        return redirect(url_for("mostrar_cliente")) # Redirige a la búsqueda de perfil

    if not sheet_client or not sheet_log:
        flash("Servicio de Google Sheets no disponible.", "error")
        return redirect(url_for("mostrar_cliente"))

    try:
        sheet_log_records = sheet_log.get_all_records()
        cliente = next((c for c in sheet_log_records if str(c.get("ID", "")).strip() == cliente_id), None)

        if not cliente:
            flash("Cliente no encontrado", "error")
            return redirect(url_for("mostrar_cliente"))

        cliente["id"] = cliente.get("ID", cliente_id)
        
        # Cargar logs específicos para este cliente desde la hoja 'log'
        logs_filtrados = [
            {
                "usuario": log.get("Log User", "N/A"),
                "descripcion": log.get("Log Description", "N/A"),
                "fecha": log.get("Log Date", "N/A"),
                "tipo": log.get("Log Type", "N/A")
            }
            for log in sheet_log_records # Ya tenemos los records de sheet_log
            if str(log.get("ID", "")).strip() == str(cliente_id).strip()
        ]
        cliente["logs"] = logs_filtrados

        return render_template("clients.html", cliente=cliente, messages=get_flashed_messages(with_categories=True))
    except Exception as e:
        flash(f"Error al cargar perfil del cliente: {e}", "error")
        return redirect(url_for("mostrar_cliente"))


@app.route("/perfil-cliente")
@login_required # Proteger ruta
def mostrar_cliente():
    nombre_buscado = request.args.get("nombre", "").strip().lower()

    if not sheet_client or not sheet_log:
        flash("Servicio de Google Sheets no disponible.", "error")
        return render_template("client_profile.html", clientes=[], nombres=[], messages=get_flashed_messages(with_categories=True))

    def get_perfil_records(_):
        return sheet_client.open_by_key(SHEET_ID).worksheet("Perfil").get_all_records()

    try:
        registros_perfil = get_sheet_data_cached("Perfil", get_perfil_records)
        registros_log    = sheet_log.get_all_records() # Obtener todos los logs una vez

        coincidencias = []
        nombres_clientes_perfil = []
        for perfil in registros_perfil:
            full_name = perfil.get("Full Name", "").strip()
            if full_name:
                nombres_clientes_perfil.append(full_name) # Para el dropdown de búsqueda

            if nombre_buscado and nombre_buscado in full_name.lower():
                id_cliente = str(perfil.get("ID", "")).strip()
                # Buscar el log correspondiente usando el ID
                log_data = next((log_r for log_r in registros_log if str(log_r.get("ID", "")).strip() == id_cliente), {})
                combinado = {**perfil, **log_data}
                coincidencias.append(combinado)
        
        return render_template("client_profile.html",
                               clientes=coincidencias,
                               nombres=sorted(list(set(nombres_clientes_perfil))), # Nombres únicos y ordenados
                               messages=get_flashed_messages(with_categories=True))
    except Exception as e:
        flash(f"Error al cargar perfiles de clientes: {e}", "error")
        return render_template("client_profile.html", clientes=[], nombres=[], messages=get_flashed_messages(with_categories=True))


@app.route("/agregar-cliente", methods=["GET", "POST"])
@login_required # Proteger ruta
def agregar_cliente():
    CAMPOS_PERFIL = [
        "Full Name", "Phone Number", "Address", "Email", "Language",
        "Preferred contact time", "Do we have permission to inspect the property?", "Additional Notes",
        "Type of Property", "How many floors does the property have?", "Is the garage attached or detached?",
        "Type of roof", "Type of siding", "Roof pitch (slope or inclination of the roof)", "Walkable?",
        "Has the property been inspected?", "Dogs?", "What caused the damage?", "Type of Damage Observed",
        "Upload photos", "Additional notes about damage", "Insurance Company Name", "How much is your deductible?",
        "Policy Number", "PDF Policy", "Have you filed an insurance claim?", "Claim Status", "Claim Number",
        "Claim Documents", "Adjuster Name (if known)", "Storm Date", "Adjuster Name Insurance",
        "Additional notes about claim", "Ready Form", "Case Delivered", "Email Admin By Us", "Password",
        "App Login", "Sold By", "Fecha de Ajustacion", "Visita Programada"
    ]

    if request.method == "POST":
        if not sheet_client or not sheet_log:
            flash("Servicio de Google Sheets no disponible.", "error")
            return redirect(url_for("agregar_cliente"))

        try:
            sheet_perfil = sheet_client.open_by_key(SHEET_ID).worksheet("Perfil")
            sheet_log_ws = sheet_client.open_by_key(SHEET_ID).worksheet("log")

            # Obtener el ID de la columna AO (índice 40) de la hoja 'Perfil'
            # y generar un nuevo ID único
            id_column_values = sheet_perfil.col_values(41) # Columna AO
            ids_validos = [int(i) for i in id_column_values if i.strip().isdigit()]
            nuevo_id = max(ids_validos) + 1 if ids_validos else 1

            datos_perfil = []
            for campo in CAMPOS_PERFIL:
                valores = request.form.getlist(campo)
                datos_perfil.append(
                    ", ".join(valores) if len(valores) > 1 else (valores[0] if valores else "")
                )

            # Asegurarse de que haya suficientes columnas para el ID en la posición correcta (AO - índice 40)
            while len(datos_perfil) <= 40: # Asegura que la lista tenga al menos 41 elementos para el índice 40
                datos_perfil.append("")
            datos_perfil[40] = str(nuevo_id) # Columna AO (índice 40 en Python)

            sheet_perfil.append_row(datos_perfil, value_input_option="USER_ENTERED")

            # Preparar y añadir fila a la hoja 'log'
            log_headers = sheet_log_ws.row_values(1)
            fila_log    = [""] * len(log_headers)
            MAPA_CAMPOS_LOG = {
                "ID": "ID", # Aunque el ID se maneja aparte, lo mantengo para referencia
                "Full Name": "Full Name",
                "Phone": "Phone Number",
                "Address": "Address",
                "Email": "Email",
                "Language": "Language",
                "Preferred hours": "Preferred contact time",
                "Sold By": "Sold By",
                "Policy Number": "Policy Number",
                "Claim Number": "Claim Number",
                "Insurance": "Insurance Company Name",
                "Deductible": "How much is your deductible?",
                "Adjuster": "Adjuster Name (if known)",
                "Estimate": "Claim Documents",
                "Other Documents": "Other Documents",
                "Adjustment Date": "Fecha de Ajustacion",
                "Scheduled Visit": "Visita Programada"
            }
            
            # Mapeo de campos del formulario a las columnas del log
            for i, header in enumerate(log_headers):
                h = header.strip()
                if h == "ID":
                    fila_log[i] = str(nuevo_id)
                elif h in MAPA_CAMPOS_LOG:
                    form_field_name = MAPA_CAMPOS_LOG[h]
                    vals = request.form.getlist(form_field_name)
                    fila_log[i] = ", ".join(vals) if len(vals) > 1 else (vals[0] if vals else "")
                elif h == "Log Date": # Añadir fecha de creación del cliente como un log inicial
                    fila_log[i] = datetime.now().strftime("%m/%d/%Y")
                elif h == "Log Type":
                    fila_log[i] = "Cliente Creado"
                elif h == "Log User":
                    fila_log[i] = session.get('user', 'Sistema') # Usuario que crea el cliente

            sheet_log_ws.append_row(fila_log, value_input_option="USER_ENTERED")
            flash("Client successfully added with ID: " + str(nuevo_id), "success")
            return redirect(url_for("client_profile_selector", cliente_id=nuevo_id)) # Redirige al perfil del nuevo cliente
        except Exception as e:
            flash(f"Error al agregar cliente: {e}", "error")
            # Log el error para depuración
            print(f"Error en agregar_cliente: {e}")

    return render_template("add_client.html", campos=CAMPOS_PERFIL, messages=get_flashed_messages(with_categories=True))

@app.route("/editar-cliente/<int:cliente_id>", methods=["GET", "POST"])
@login_required # Proteger ruta
def editar_cliente(cliente_id):
    if not sheet_client or not sheet_perfil:
        flash("Servicio de Google Sheets no disponible.", "error")
        return redirect(url_for("client_profile_selector"))

    try:
        sheet_perfil = sheet_client.open_by_key(SHEET_ID).worksheet("Perfil")
        registros = sheet_perfil.get_all_records()
        cliente = next((r for r in registros if str(r.get("ID")) == str(cliente_id)), None)

        if not cliente:
            flash("Cliente no encontrado para edición.", "error")
            return redirect(url_for("client_profile_selector"))

        def convertir_fecha_a_formato_html(fecha_str):
            try:
                # Intenta parsear desde formatos comunes y devuelve YYYY-MM-DD para input type="date"
                return datetime.strptime(fecha_str, "%m/%d/%Y").strftime("%Y-%m-%d")
            except ValueError:
                try:
                    return datetime.strptime(fecha_str, "%Y-%m-%d").strftime("%Y-%m-%d")
                except ValueError:
                    return ""

        # Convierte fechas para que el input type="date" las maneje correctamente
        cliente['Fecha de Ajustacion'] = convertir_fecha_a_formato_html(cliente.get('Fecha de Ajustacion', ''))
        cliente['Visita Programada'] = convertir_fecha_a_formato_html(cliente.get('Visita Programada', ''))
        
        # Obtener los encabezados actuales de la hoja 'Perfil' para mantener el orden
        columnas_perfil_sheet = sheet_perfil.row_values(1)

        if request.method == "POST":
            nuevos_datos_ordenados = []
            for col_header in columnas_perfil_sheet:
                # Manejar campos de fecha que vienen en formato YYYY-MM-DD y guardarlos como MM/DD/YYYY
                if col_header in ["Fecha de Ajustacion", "Visita Programada"]:
                    raw_date = request.form.get(col_header, "")
                    if raw_date:
                        try:
                            # Convertir YYYY-MM-DD a MM/DD/YYYY para guardar en Sheets
                            parsed_date = datetime.strptime(raw_date, "%Y-%m-%d")
                            nuevos_datos_ordenados.append(parsed_date.strftime("%m/%d/%Y"))
                        except ValueError:
                            nuevos_datos_ordenados.append(raw_date) # Si hay error, guardar como está
                    else:
                        nuevos_datos_ordenados.append("")
                else:
                    valores = request.form.getlist(col_header)
                    nuevos_datos_ordenados.append(
                        ", ".join(valores) if len(valores) > 1 else (valores[0] if valores else "")
                    )

            fila_cliente_idx = next((i + 2 for i, row in enumerate(registros) if str(row.get("ID")) == str(cliente_id)), None)

            if fila_cliente_idx:
                # Gspread no tiene un método directo para actualizar una fila entera por su contenido ordenado
                # La opción más limpia es leer la fila, modificarla y luego actualizar el rango
                # O si estás dispuesto a reinsertar la fila como en tu código original:
                sheet_perfil.delete_rows(fila_cliente_idx)
                sheet_perfil.insert_row(nuevos_datos_ordenados, index=fila_cliente_idx, value_input_option="USER_ENTERED")
                flash("Cliente actualizado correctamente.", "success")
            else:
                flash("No se pudo actualizar el cliente.", "error")

            return redirect(url_for("mostrar_cliente", nombre=cliente.get("Full Name", "")))

        # Para el GET, los campos para el formulario se obtienen de las columnas del sheet
        # y se prellenan con los datos del cliente encontrado
        campos_para_form = []
        for col in columnas_perfil_sheet:
            if col == 'ID': # El ID no se edita directamente en el formulario
                continue
            campos_para_form.append({
                'name': col,
                'value': cliente.get(col, '')
            })

        return render_template("edit_client.html", cliente=cliente, campos=campos_para_form, messages=get_flashed_messages(with_categories=True))
    except Exception as e:
        flash(f"Error al editar cliente: {e}", "error")
        return redirect(url_for("client_profile_selector"))


@app.route("/update_log_date/<cliente_id>/<campo>", methods=["POST"])
@login_required # Proteger ruta
def update_log_date(cliente_id, campo):
    if not sheet_client or not sheet_log or not sheet_perfil:
        flash("Servicio de Google Sheets no disponible.", "error")
        return redirect(url_for('client_profile_selector', cliente_id=cliente_id))

    raw_date = request.form.get("log_date")
    fecha_formateada_para_sheet = ""
    # Intentar parsear y formatear la fecha
    for fmt_input in ("%Y-%m-%d", "%m/%d/%Y"): # Primero YYYY-MM-DD para HTML input, luego MM/DD/YYYY
        try:
            fecha_obj = datetime.strptime(raw_date, fmt_input)
            fecha_formateada_para_sheet = fecha_obj.strftime("%m/%d/%Y") # Formato para guardar en Sheets
            break
        except ValueError:
            continue
    else:
        flash(f"Formato de fecha inválido: {raw_date}", "error")
        return redirect(url_for('client_profile_selector', cliente_id=cliente_id))

    try:
        # 🔹 Actualiza hoja 'log'
        log_sheet = sheet_client.open_by_key(SHEET_ID).worksheet("log")
        log_records = log_sheet.get_all_records()
        log_headers = log_sheet.row_values(1)
        log_row_index = next((i + 2 for i, row in enumerate(log_records) if str(row.get("ID", "")).strip() == cliente_id), None)

        # 🔹 Actualiza hoja 'Perfil'
        perfil_sheet = sheet_client.open_by_key(SHEET_ID).worksheet("Perfil")
        perfil_records = perfil_sheet.get_all_records()
        perfil_headers = perfil_sheet.row_values(1)
        perfil_row_index = next((i + 2 for i, row in enumerate(perfil_records) if str(row.get("ID", "")).strip() == cliente_id), None)

        # Actualiza en log
        if log_row_index and campo in log_headers:
            col_index = log_headers.index(campo) + 1
            log_sheet.update_cell(log_row_index, col_index, fecha_formateada_para_sheet, value_input_option="USER_ENTERED")
            flash(f"Fecha en log ({campo}) actualizada correctamente.", "success")

        # Actualiza en perfil si existe ese campo
        if perfil_row_index and campo in perfil_headers:
            col_index = perfil_headers.index(campo) + 1
            perfil_sheet.update_cell(perfil_row_index, col_index, fecha_formateada_para_sheet, value_input_option="USER_ENTERED")
            flash(f"Fecha en perfil ({campo}) actualizada correctamente.", "success")
        elif perfil_row_index and campo not in perfil_headers:
            flash(f"Campo '{campo}' no encontrado en la hoja 'Perfil'.", "warning")
        
        # Si no se encontró el cliente en ninguna hoja
        if not log_row_index and not perfil_row_index:
            flash("Cliente no encontrado en las hojas de log o perfil para actualizar fecha.", "error")

    except Exception as e:
        flash(f"Error al actualizar fecha del log: {e}", "error")
        print(f"Error en update_log_date: {e}")

    return redirect(url_for('client_profile_selector', cliente_id=cliente_id))


@app.route("/eliminar_cliente/<cliente_id>", methods=["POST"])
@login_required # Proteger ruta
def eliminar_cliente(cliente_id):
    if not sheet_client or not sheet_perfil or not sheet_log:
        flash("Servicio de Google Sheets no disponible.", "error")
        return redirect(url_for("clients"))

    try:
        sheet_perfil = sheet_client.open_by_key(SHEET_ID).worksheet("Perfil")
        data_perfil  = sheet_perfil.get_all_records()

        fila_a_borrar_perfil = next((i+2 for i,row in enumerate(data_perfil) if str(row.get("ID"))==str(cliente_id)), None)
        if fila_a_borrar_perfil:
            sheet_perfil.delete_rows(fila_a_borrar_perfil)
            flash("Cliente eliminado de la hoja 'Perfil'.", "success")
        else:
            flash("Cliente no encontrado en la hoja 'Perfil'.", "warning")


        sheet_log_ws = sheet_client.open_by_key(SHEET_ID).worksheet("log")
        data_log     = sheet_log_ws.get_all_records()
        # Obtener todas las filas de log para el cliente y borrar en orden inverso
        filas_log    = sorted([i+2 for i,row in enumerate(data_log) if str(row.get("ID"))==str(cliente_id)], reverse=True)
        if filas_log:
            for idx in filas_log:
                sheet_log_ws.delete_rows(idx)
            flash("Logs del cliente eliminados correctamente.", "success")
        else:
            flash("No se encontraron logs para el cliente.", "warning")

        flash("Proceso de eliminación de cliente completado.", "info")
    except Exception as e:
        flash(f"Error al eliminar cliente y/o logs: {e}", "error")
        print(f"Error en eliminar_cliente: {e}") # Para depuración

    return redirect(url_for("clients"))

# =============================
# 🔹 Rutas de Construction (desde app.py)
# =============================
@app.route("/construction_detail/<cliente_id>")
@login_required # Proteger ruta
def construction_detail(cliente_id):
    if not sheet_client or not sheet_construction:
        flash("Servicio de Google Sheets no disponible para construcción.", "error")
        return render_template("construction_detail.html", cliente=None, not_found=cliente_id)

    try:
        sheet = sheet_client.open_by_key(SHEET_ID).worksheet("Construction")
        data = sheet.get_all_records()

        cliente = next((row for row in data if str(row.get("ID", "")).strip() == str(cliente_id).strip()), None)
        
        return render_template("construction_detail.html", cliente=cliente, not_found=cliente_id if not cliente else None, messages=get_flashed_messages(with_categories=True))
    except Exception as e:
        flash(f"Error al cargar detalles de construcción: {e}", "error")
        return render_template("construction_detail.html", cliente=None, not_found=cliente_id)


@app.route("/construction_profile", methods=["GET"])
@login_required # Proteger ruta
def construction_profile():
    if not sheet_client or not sheet_construction:
        flash("Servicio de Google Sheets no disponible para construcción.", "error")
        return render_template("construction_profile.html", clientes=[], not_found="", messages=get_flashed_messages(with_categories=True))

    try:
        sheet = sheet_client.open_by_key(SHEET_ID).worksheet("Construction")
        data = sheet.get_all_records()

        name_query = request.args.get("name", "").strip().lower()

        clientes_encontrados = []
        for row in data:
            # Asegúrate de que 'Full Name' exista y sea una cadena para evitar errores
            full_name = row.get("Full Name", "").strip().lower()
            # Asegúrate de que 'ID' exista para evitar errores al acceder
            cliente_id = str(row.get("ID", "")).strip()

            if name_query in full_name or (name_query and name_query == cliente_id):
                clientes_encontrados.append(row)

        if not clientes_encontrados and name_query:
            flash(f"No se encontraron clientes para la búsqueda: '{name_query}' en Construcción.", "warning")

        return render_template("construction_profile.html", clientes=clientes_encontrados, not_found="" if clientes_encontrados else name_query, messages=get_flashed_messages(with_categories=True))
    except Exception as e:
        flash(f"Error al cargar perfiles de construcción: {e}", "error")
        return render_template("construction_profile.html", clientes=[], not_found=name_query, messages=get_flashed_messages(with_categories=True))

@app.route("/editar_construction/<cliente_id>", methods=["GET", "POST"])
@login_required # Proteger ruta
def editar_construction(cliente_id):
    if not sheet_client or not sheet_construction:
        flash("Servicio de Google Sheets no disponible para edición de construcción.", "error")
        return redirect(url_for('construction_detail', cliente_id=cliente_id))

    try:
        sheet = sheet_client.open_by_key(SHEET_ID).worksheet("Construction")
        data = sheet.get_all_records()

        row_index = next((idx + 2 for idx, row in enumerate(data) if str(row.get("ID", "")).strip() == cliente_id), None)
        if row_index is None:
            flash(f"Cliente con ID {cliente_id} no encontrado en Construction.", "error")
            return redirect(url_for('construction_profile'))

        columnas = list(data[0].keys()) if data else []
        cliente_existente = next((row for row in data if str(row.get("ID", "")) == cliente_id), {})

        if request.method == "POST":
            nueva_fila_valores = []
            for col in columnas:
                # Usa .get() para evitar KeyError si el campo no está en el formulario POST
                nueva_fila_valores.append(request.form.get(col, cliente_existente.get(col, "")))

            sheet.update(f"A{row_index}", [nueva_fila_valores], value_input_option="USER_ENTERED")
            flash("Detalles de construcción actualizados correctamente.", "success")
            return redirect(url_for('construction_detail', cliente_id=cliente_id))

        return render_template("editar_construction.html", cliente=cliente_existente, columnas=columnas, messages=get_flashed_messages(with_categories=True))
    except Exception as e:
        flash(f"Error al editar detalles de construcción: {e}", "error")
        print(f"Error en editar_construction: {e}")
        return redirect(url_for('construction_profile'))

# --- Ejecución de la Aplicación ---
if __name__ == "__main__":
    # La parte de impresión de tareas de calendario de `app.py` se puede mantener aquí
    # para depuración si lo deseas.
    # tareas_iniciales = obtener_tareas_calendario()
    # print("TAREAS ENCONTRADAS AL INICIAR:")
    # for tarea in tareas_iniciales:
    #     print(tarea)
    app.run(debug=True, port=5000) # Puedes elegir un puerto si el 5000 ya está en uso