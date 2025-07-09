from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime

# Ruta al archivo de credenciales
SERVICE_ACCOUNT_FILE = 'static/credentials.json'
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

def obtener_eventos():
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)

    service = build('calendar', 'v3', credentials=credentials)

    # ✅ Asegúrate de que este correo sea el ID correcto del calendario compartido con tu service account
    calendar_id = 'andrealizeth768@gmail.com'

    now = datetime.utcnow().isoformat() + 'Z'

    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=now,
        maxResults=10,
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])

    eventos = []
    for e in events:
        eventos.append({
            'titulo': e.get('summary', 'Sin título'),
            'fecha': e['start'].get('date', e['start'].get('dateTime', ''))
        })

    return eventos
