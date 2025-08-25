# server.py
import eventlet
eventlet.monkey_patch()

import pandas as pd
import io
import threading
import os
import base64
import json
from datetime import datetime
from flask import Flask, Response
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from worker import QuantumBot
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret-key-for-hillside-automation'

# Updated CORS for your Netlify frontend
FRONTEND_ORIGIN = "https://quantbot.netlify.app"
CORS(app, resources={r"/*": {"origins": FRONTEND_ORIGIN}})
socketio = SocketIO(app, cors_allowed_origins=FRONTEND_ORIGIN, async_mode='eventlet')

bot_instance = None
session_data = {}
automation_thread = None

class EmailService:
    def __init__(self):
        self.sender_email = os.getenv('EMAIL_SENDER')
        self.password = os.getenv('EMAIL_PASSWORD')
        self.smtp_server = "smtp.gmail.com"; self.smtp_port = 587
        if not self.sender_email or not self.password:
            print("[Email] WARNING: Email credentials not found in environment variables.")
    
    def send_report(self, recipients, subject, body, attachments=None):
        if not self.sender_email or not self.password: return False
        try:
            msg = MIMEMultipart(); msg['From'] = self.sender_email; msg['To'] = ", ".join(recipients); msg['Subject'] = subject
            msg.attach(MIMEText(body, 'html'))
            if attachments:
                for filename, content in attachments.items():
                    part = MIMEBase('application', 'octet-stream'); part.set_payload(content.encode('utf-8'))
                    encoders.encode_base64(part); part.add_header('Content-Disposition', f'attachment; filename="{filename}"')
                    msg.attach(part)
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls(); server.login(self.sender_email, self.password); server.send_message(msg)
            print(f"Email sent successfully to {', '.join(recipients)}"); return True
        except Exception as e:
            print(f"Failed to send email: {e}"); return False

class GoogleDriveService:
    def __init__(self):
        self.creds = None; self.service = None
        self.folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            base64_creds = os.getenv('GDRIVE_SA_KEY_BASE64')
            if not base64_creds or not self.folder_id:
                raise ValueError("Google Drive secrets not found.")
            creds_json = base64.b64decode(base64_creds).decode('utf-8'); creds_dict = json.loads(creds_json)
            self.creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=['https://www.googleapis.com/auth/drive'])
            self.service = build('drive', 'v3', credentials=self.creds)
            print("[G-Drive] Service initialized securely from environment variables.")
        except Exception as e:
            print(f"[G-Drive] CRITICAL ERROR: Could not initialize Google Drive service: {e}")
    
    def upload_file(self, filename, file_content):
        if not self.service: return False
        try:
            from googleapiclient.http import MediaIoBaseUpload
            file_metadata = {'name': filename, 'parents': [self.folder_id]}
            media = MediaIoBaseUpload(io.BytesIO(file_content.encode('utf-8')), mimetype='text/csv', resumable=True)
            self.service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            print(f"[G-Drive] File '{filename}' uploaded successfully.")
            return True
        except Exception as e:
            print(f"[G-Drive] ERROR: File upload failed: {e}"); return False

email_service = EmailService()
drive_service = GoogleDriveService()

def get_email_list():
    try:
        with open('config/emails.conf', 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []

def run_automation_process(session_id):
    global bot_instance
    results = []; is_terminated = False; is_crash = False
    try:
        data = session_data.get(session_id, {}); csv_content = data.get('csv_content')
        df = pd.read_csv(io.StringIO(csv_content));
        if 'Status' not in df.columns: df.insert(1, 'Status', '')
        patient_list = df[(df['Status'] != 'Done') & (df['Status'] != 'Bad')]['Name'].tolist()
        socketio.emit('initial_stats', {'total': len(patient_list)})
        results = bot_instance.process_patient_list(patient_list)
        is_terminated = bot_instance.termination_event.is_set()
    except Exception as e:
        print(f"Fatal error in automation thread: {e}"); is_crash = True
        socketio.emit('error', {'message': f'A fatal error occurred: {e}'})
    finally:
        socketio.emit('micro_status_update', {'message': 'Generating final reports...'})
        generate_and_send_reports(session_id, results, is_crash_report=is_crash, is_terminated=is_terminated)
        if bot_instance: bot_instance.shutdown(); bot_instance = None
        if session_id in session_data: del session_data[session_id]

def generate_and_send_reports(session_id, results, is_crash_report=False, is_terminated=False):
    if not results:
        socketio.emit('process_complete', {'message': 'No patients were processed.'}); return
    data = session_data.get(session_id, {}); original_df = pd.read_csv(io.StringIO(data.get('csv_content')))
    if 'Status' not in original_df.columns: original_df.insert(1, 'Status', '')
    result_df = pd.DataFrame(results).set_index('Name'); original_df.set_index('Name', inplace=True)
    original_df.update(result_df); full_df = original_df.reset_index()
    bad_df = full_df[full_df['Status'] == 'Bad'][['Name', 'Status']]
    timestamp = datetime.now().strftime("%d_%b_%Y"); custom_name = data.get('filename') or timestamp
    full_report_name = f"{custom_name}_Full.csv"; bad_report_name = f"{custom_name}_Bad.csv"
    full_report_content = full_df.to_csv(index=False)
    drive_service.upload_file(full_report_name, full_report_content)
    attachments = {full_report_name: full_report_content, bad_report_name: bad_df.to_csv(index=False)}
    status_text = "terminated by user" if is_terminated else "crashed due to an error" if is_crash_report else "completed successfully"
    subject = f"Automation Report [{status_text.upper()}]: {custom_name}"
    body = f"""<html><body><h2>Hillside's Quantum Automation Report</h2><p><b>The process was {status_text}.</b></p><p><b>Total Patients in File:</b> {len(full_df)}</p><p><b>Processed in this run:</b> {len(results)}</p><p><b>Successful ('Done'):</b> {len([r for r in results if r['Status'] == 'Done'])}</p><p><b>Bad State ('Bad'):</b> {len([r for r in results if r['Status'] == 'Bad'])}</p><p>The full report and a list of 'Bad' status patients from this run are attached.</p></body></html>"""
    email_service.send_report(data.get('emails'), subject, body, attachments)
    socketio.emit('process_complete', {'message': f'Process {status_text}. Report sent.'})

@app.route('/')
def status_page():
    APP_STATUS_HTML = """<!DOCTYPE html><html lang="en"><head><title>Hillside Automation API - Railway</title><style>body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background:#f0f2f5;}.status-box{text-align:center;padding:40px 60px;background:white;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,0.1);}h1{font-size:24px;color:#333;margin-bottom:10px;} .indicator{font-size:18px;font-weight:600;padding:8px 16px;border-radius:20px;}.active{color:#28a745;background-color:#e9f7ea;}.info{margin-top:20px;color:#666;}</style></head><body><div class="status-box"><h1>🚀 Hillside Automation API</h1><div class="indicator active">● Running on Railway</div><div class="info">Frontend: <a href="https://quantbot.netlify.app" target="_blank">quantbot.netlify.app</a></div></div></body></html>"""
    return Response(APP_STATUS_HTML)

@socketio.on('connect')
def handle_connect():
    print(f'Frontend connected from: {FRONTEND_ORIGIN}')
    emit('email_list', {'emails': get_email_list()})

@socketio.on('initialize_session')
def handle_init(data):
    session_id = 'user_session'; global bot_instance
    session_data[session_id] = {'csv_content': data['content'], 'emails': data['emails'], 'filename': data['filename']}
    if bot_instance: bot_instance.shutdown()
    bot_instance = QuantumBot(socketio, app)

    is_success, error_message = bot_instance.initialize_driver()
    if is_success:
        emit('bot_initialized')
    else:
        emit('error', {'message': f'Failed to initialize automation bot: {error_message}'})

@socketio.on('start_login')
def handle_login(credentials):
    if not bot_instance: return emit('error', {'message': 'Bot not initialized.'})
    is_success, error_message = bot_instance.login(credentials['username'], credentials['password'])
    if is_success: emit('otp_required')
    else: emit('error', {'message': f'Login failed: {error_message}'})

@socketio.on('submit_otp')
def handle_otp(data):
    if not bot_instance: return emit('error', {'message': 'Bot not initialized.'})
    is_success, error_message = bot_instance.submit_otp(data['otp'])
    if is_success:
        emit('login_successful')
        session_id = 'user_session'
        # FIXED: Correct syntax for start_background_task
        socketio.start_background_task(run_automation_process, session_id)
    else: emit('error', {'message': f'OTP failed: {error_message}'})

@socketio.on('terminate_process')
def handle_terminate():
    if bot_instance: print("Termination signal received."); bot_instance.stop()

if __name__ == '__main__':
    print("====================================================================")
    print("  🚀 Hillside Automation Backend - Railway Deployment")
    print(f"  Frontend URL: {FRONTEND_ORIGIN}")
    print(f"  Port: {os.getenv('PORT', 7860)}")
    print("====================================================================")
    socketio.run(app, host='0.0.0.0', port=int(os.getenv('PORT', 7860)))
