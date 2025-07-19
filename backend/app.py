# backend/app.py
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from googletrans import Translator
import PyPDF2
import io
import os
import json
import re
import base64 # base64 import edildi
import time

# Celery importları
from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

# To load environment variables from .env file (Sadece yerel geliştirme için)
from dotenv import load_dotenv
load_dotenv()

# Flask uygulamasını başlat
app = Flask(__name__)
CORS(app)

# --- Celery Uygulaması Tanımlaması ---
REDIS_BROKER_URL = os.environ.get('REDIS_BROKER_URL', 'redis://localhost:6379/0')

celery_app = Celery(
    'pdf_translator',
    broker=REDIS_BROKER_URL,
    backend=REDIS_BROKER_URL, # Görev sonuçlarını da Redis'te saklayabiliriz
    include=['backend.app'] 
)

# Firebase Admin SDK Initialization Helper Function
def initialize_firebase_admin_sdk(context_name):
    global db, db_flask # db_flask'ı da global olarak kullanıyoruz
    print(f"[{context_name}] Attempting Firebase Admin SDK initialization...")
    try:
        # Base64 kodlu anahtarı oku
        service_account_info_base64 = os.environ.get('FIREBASE_SERVICE_ACCOUNT_KEY_BASE64')
        
        if service_account_info_base64:
            # Base64'ten çöz ve JSON olarak ayrıştır
            service_account_info_json = base64.b64decode(service_account_info_base64).decode('utf-8')
            cred_json = json.loads(service_account_info_json)
            cred = credentials.Certificate(cred_json)
            print(f"[{context_name}] Service account key decoded and parsed successfully.")
        else:
            raise ValueError("FIREBASE_SERVICE_ACCOUNT_KEY_BASE64 environment variable is NOT set.")
        
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        
        # Context'e göre doğru db değişkenini ayarla
        if context_name == "Backend Startup":
            db_flask = firestore.client()
        elif context_name == "Celery Worker Startup":
            db = firestore.client()
        
        print(f"Firebase Admin SDK initialized successfully for {context_name}. 🎉")
        return True
    except Exception as e:
        print(f"[{context_name} ERROR] Critical error during Firebase Admin SDK initialization: {e}")
        return False

# Celery worker'ı için Firebase başlatma
@worker_process_init.connect 
def init_firebase_for_celery_worker(**kwargs):
    global db 
    if not initialize_firebase_admin_sdk("Celery Worker Startup"):
        db = None # Başarısız olursa db'yi None yap

# Flask uygulaması için Firebase başlatma
# Uygulama başladığında bir kez çalışır
if not initialize_firebase_admin_sdk("Backend Startup"):
    db_flask = None # Başarısız olursa db_flask'ı None yap


# --- General variables from Canvas environment ---
APP_ID = os.environ.get('CANVAS_APP_ID', 'default-app-id')

# --- Translation Logic (Celery Task) ---
translator = Translator()
MAX_CHUNK_SIZE = 4800 # characters

def extract_text_from_pdf(pdf_bytes):
    text = ""
    try:
        pdf_file = io.BytesIO(pdf_bytes)
        reader = PyPDF2.PdfReader(pdf_file)
        for page_num in range(len(reader.pages)):
            page = reader.pages[page_num]
            extracted_page_text = page.extract_text() or ""
            extracted_page_text = extracted_page_text.replace('\r\n', '\n').replace('\r', '\n')
            extracted_page_text = extracted_page_text.replace('ﬁ', 'fi').replace('ﬀ', 'ff')
            extracted_page_text = re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', extracted_page_text)
            extracted_page_text = re.sub(r'(?<=[a-zA-Z0-9.,;])\n(?=[a-zA-Z0-9])', ' ', extracted_page_text)
            extracted_page_text = re.sub(r'\n{2,}', '\n\n', extracted_page_text)
            extracted_page_text = re.sub(r'\s+', ' ', extracted_page_text)
            extracted_page_text = extracted_page_text.strip()
            text += extracted_page_text
            text += "\n\n---PAGE_BREAK---\n\n"
    except Exception as e:
        print(f"Error extracting text from PDF: {e}")
        raise
    return text

def chunk_text(text, max_chunk_size):
    chunks = []
    current_chunk = ""
    pages = text.split("\n\n---PAGE_BREAK---\n\n")

    for page_content in pages:
        if len(current_chunk) + len(page_content) + 2 <= max_chunk_size:
            current_chunk += (page_content + "\n\n")
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            
            current_chunk = page_content + "\n\n"
            
            while len(current_chunk) > max_chunk_size:
                split_point = -1
                last_period = current_chunk.rfind('.', 0, max_chunk_size)
                if last_period != -1:
                    split_point = last_period + 1
                if split_point == -1:
                    last_newline = current_chunk.rfind('\n', 0, max_chunk_size)
                    if last_newline != -1:
                        split_point = last_newline + 1
                if split_point == -1 or split_point == 0:
                    split_point = max_chunk_size 
                
                chunks.append(current_chunk[:split_point].strip())
                current_chunk = current_chunk[split_point:].strip()
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return [chunk for chunk in chunks if chunk]

# Celery görevini tanımla
@celery_app.task(bind=True)
def process_translation_task(self, task_id, user_id, file_name, pdf_content_base64, target_language):
    """
    Celery görevi olarak tüm çeviri iş akışını yönetir.
    """
    print(f"[Celery Task] Starting process_translation_task for task {task_id}.")
    # Celery worker'ı için db'nin initialize edildiğinden emin ol
    if not db: 
        print(f"[Celery Task ERROR] Firebase Admin SDK not initialized for task {task_id}. Attempting re-initialization...")
        if not initialize_firebase_admin_sdk("Celery Worker Startup (Re-attempt)"):
            print(f"[Celery Task ERROR] Firebase Admin SDK re-initialization failed for task {task_id}. Aborting.")
            task_ref = firestore.client().collection(f'artifacts/{APP_ID}/users/{user_id}/translations').document(task_id)
            task_ref.update({
                'status': 'failed',
                'errorMessage': 'Firebase Admin SDK not initialized in worker.',
                'failedAt': firestore.SERVER_TIMESTAMP
            })
            return

    task_ref = db.collection(f'artifacts/{APP_ID}/users/{user_id}/translations').document(task_id)

    try:
        self.update_state(state='PROGRESS', meta={'progress': 10, 'status': 'processing'})
        task_ref.update({'status': 'processing', 'progress': 10, 'initialTimestamp': firestore.SERVER_TIMESTAMP})
        print(f"[Celery Task] Task {task_id}: Initial status set to processing (progress 10%).")
        
        pdf_bytes = base64.b64decode(pdf_content_base64)
        print(f"[Celery Task] PDF received and decoded for task {task_id}: {file_name}")

        self.update_state(state='PROGRESS', meta={'progress': 30, 'status': 'processing'})
        task_ref.update({'status': 'processing', 'progress': 30})
        print(f"[Celery Task] Task {task_id}: Progress updated to 30% (extracting text).")
        original_text = extract_text_from_pdf(pdf_bytes)
        print(f"[Celery Task] Text extracted from PDF for task {task_id}. Length: {len(original_text)} characters.")

        self.update_state(state='PROGRESS', meta={'progress': 50, 'status': 'processing'})
        task_ref.update({'status': 'processing', 'progress': 50})
        print(f"[Celery Task] Task {task_id}: Progress updated to 50% (chunking text).")
        chunks = chunk_text(original_text, MAX_CHUNK_SIZE)
        print(f"[Celery Task] Text for task {task_id} split into {len(chunks)} chunks.")

        translated_chunks = []
        total_chunks = len(chunks)
        for i, chunk in enumerate(chunks):
            try:
                progress = int(50 + ((i + 1) / total_chunks) * 50) 
                self.update_state(state='PROGRESS', meta={'progress': progress, 'status': 'translating'})
                task_ref.update({'status': 'translating', 'progress': progress})
                print(f"[Celery Task] Task {task_id}: Updated progress to {progress}% (translating chunk {i+1}/{total_chunks})")
                translated = translator.translate(chunk, dest=target_language)
                translated_chunks.append(translated.text)
                time.sleep(0.5) 
            except Exception as e:
                print(f"[Celery Task ERROR] Error translating chunk {i+1} for task {task_id}: {e}")
                translated_chunks.append(f"[Translation Error for chunk {i+1}: {e}] Original text: {chunk}")
                task_ref.update({
                    'status': 'failed',
                    'errorMessage': f"Translation of chunk {i+1} failed: {e}",
                    'failedAt': firestore.SERVER_TIMESTAMP
                })
                return 

        task_ref.update({
            'translatedContent': translated_chunks,
            'status': 'completed',
            'progress': 100,
            'completedAt': firestore.SERVER_TIMESTAMP
        })
        print(f"[Celery Task] Translation task {task_id} completed successfully in Firestore.")

        updated_doc = task_ref.get()
        if updated_doc.exists:
            data_after_update = updated_doc.to_dict()
            print(f"[Celery Task] Firestore verification for task {task_id}: status={data_after_update.get('status')}, progress={data_after_update.get('progress')}")
        else:
            print(f"[Celery Task] Firestore verification for task {task_id}: Document does not exist after update attempt.")

    except Exception as e:
        print(f"[Celery Task ERROR] Critical error processing translation task {task_id}: {e}")
        task_ref.update({
            'status': 'failed',
            'errorMessage': f"Critical backend error: {str(e)}",
            'failedAt': firestore.SERVER_TIMESTAMP
        })
        print(f"[Celery Task] Task {task_id}: Critical error reported to Firestore.")


# --- Flask Routes ---

@app.route('/') # Health check için kök URL
def health_check():
    print("Health check endpoint hit!")
    # Flask uygulamasının db_flask'ı initialize edip etmediğini kontrol et
    firebase_status = "initialized" if db_flask else "not initialized (check logs)"
    return jsonify({"status": "Backend is running!", "firebase_status": firebase_status})


@app.route('/translate', methods=['POST'])
def initiate_translation():
    data = request.json
    task_id = data.get('taskId')
    user_id = data.get('userId')
    file_name = data.get('fileName')
    pdf_content_base64 = data.get('pdfContent')
    target_language = data.get('targetLanguage')

    if not all([task_id, user_id, file_name, pdf_content_base64, target_language]):
        print(f"[Flask Main] Missing required parameters for task {task_id}. Data: {data}")
        return jsonify({"error": "Missing required parameters"}), 400

    # Flask uygulamasının db_flask'ı initialize edip etmediğini kontrol et
    if not db_flask: 
        print(f"[Flask Main ERROR] Firebase Admin SDK not initialized for /translate endpoint. Attempting re-initialization...")
        if not initialize_firebase_admin_sdk("Backend Startup (Re-attempt)"):
            print(f"[Flask Main ERROR] Firebase Admin SDK re-initialization failed for /translate endpoint. Aborting.")
            return jsonify({"error": "Firebase Admin SDK not initialized. Check backend logs."}), 500

    print(f"[Flask Main] Translation request received for task {task_id} by user {user_id}")

    process_translation_task.delay(task_id, user_id, file_name, pdf_content_base64, target_language)
    print(f"[Flask Main] Translation task {task_id} sent to Celery queue.")

    return jsonify({"message": "Translation process initiated", "taskId": task_id}), 202

# --- Genel Hata Yakalayıcı ---
@app.errorhandler(Exception)
def handle_exception(e):
    print(f"[Backend Global ERROR] Unhandled exception: {e}")
    return jsonify({
        "error": "Internal Server Error",
        "message": str(e)
    }), 500


# --- PDF Generation Logic ---

# Register Source Serif 4 fonts
from reportlab.pdfbase import pdfmetrics 
from reportlab.pdfbase.ttfonts import TTFont 
try:
    pdfmetrics.registerFont(TTFont('SourceSerif4-Regular', 'fonts/SourceSerif4-Regular.ttf'))
    pdfmetrics.registerFont(TTFont('SourceSerif4-Italic', 'fonts/SourceSerif4-Italic.ttf'))
    pdfmetrics.registerFont(TTFont('SourceSerif4-Bold', 'fonts/SourceSerif4-Bold.ttf'))
    pdfmetrics.registerFont(TTFont('SourceSerif4-BoldItalic', 'fonts/SourceSerif4-BoldItalic.ttf'))
    
    pdfmetrics.registerFont(TTFont('SourceSerif4-Title', 'fonts/SourceSerif4_36pt-Regular.ttf'))
    pdfmetrics.registerFont(TTFont('SourceSerif4-Subtitle', 'fonts/SourceSerif4_18pt-Regular.ttf'))

    print("Source Serif 4 fonts registered successfully.")
except Exception as e:
    print(f"[Backend Startup ERROR] Error registering Source Serif 4 fonts: {e}. PDF generation may use default fonts.")

# ReportLab'den gerekli importlar
from reportlab.lib.pagesizes import letter 
from reportlab.lib.units import inch 
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT 
from reportlab.lib.colors import black 
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak 
from reportlab.lib.styles import getSampleStyleSheet 

def page_number_footer(canvas, doc): 
    canvas.saveState()
    canvas.setFont('SourceSerif4-Regular', 9)
    x_position = letter[0] - doc.rightMargin - (0.75 * inch)
    canvas.drawString(x_position, 0.75 * inch, f"Sayfa {doc.page}")
    canvas.restoreState()

def first_page_layout(canvas, doc): 
    canvas.saveState()
    
    styles = getSampleStyleSheet()

    cover_title_style = styles['h1'] 
    cover_title_style.fontName = 'SourceSerif4-Title'
    cover_title_style.fontSize = 36
    cover_title_style.leading = 40 
    cover_title_style.alignment = TA_CENTER
    cover_title_style.textColor = black

    cover_subtitle_style = styles['Normal']
    cover_subtitle_style.fontName = 'SourceSerif4-Subtitle'
    cover_subtitle_style.fontSize = 14
    cover_subtitle_style.leading = 16
    cover_subtitle_style.alignment = TA_CENTER
    cover_subtitle_style.textColor = black

    page_width, page_height = letter

    content_width = page_width - (1.5 * inch) * 2
    x_offset = 1.5 * inch

    main_title_text = doc.original_file_name.replace('_', ' ').replace('.pdf', '')
    main_title_paragraph = Paragraph(main_title_text, cover_title_style)

    main_title_width, main_title_height = main_title_paragraph.wrapOn(canvas, content_width, page_height)
    
    main_title_y_position = (page_height / 2) - (main_title_height / 2) + (0.5 * inch)

    main_title_paragraph.drawOn(canvas, x_offset, main_title_y_position)

    translated_to_text = f"Translated to {doc.target_language.upper()}"
    translated_to_paragraph = Paragraph(translated_to_text, cover_subtitle_style)

    translated_to_width, translated_to_height = translated_to_paragraph.wrapOn(canvas, content_width, page_height)

    bottom_margin_for_subtitle = 1.5 * inch
    translated_to_y_position = bottom_margin_for_subtitle

    translated_to_paragraph.drawOn(canvas, x_offset, translated_to_y_position)

    canvas.restoreState()


@app.route('/generate-pdf', methods=['POST'])
def generate_pdf():
    """
    Generates a PDF from the provided translated text with aesthetic enhancements.
    """
    data = request.json
    translated_content = data.get('translatedContent')
    original_file_name = data.get('originalFileName', 'translated_document')
    target_language = data.get('targetLanguage', 'en')

    if not translated_content:
        return jsonify({"error": "No translated content provided for PDF generation."}), 400

    buffer = io.BytesIO()
    
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            rightMargin=0.8*inch, leftMargin=0.8*inch,
                            topMargin=0.8*inch, bottomMargin=0.8*inch)
    
    doc.original_file_name = original_file_name
    doc.target_language = target_language

    styles = getSampleStyleSheet()
    
    header_style = styles['h1'] 
    header_style.fontName = 'SourceSerif4-Bold'
    header_style.fontSize = 18
    header_style.leading = 22
    header_style.alignment = TA_CENTER
    header_style.spaceAfter = 12  

    sub_header_style = styles['h2'] 
    sub_header_style.fontName = 'SourceSerif4-Italic'
    sub_header_style.fontSize = 16
    sub_header_style.leading = 20
    sub_header_style.alignment = TA_LEFT
    sub_header_style.spaceAfter = 8  

    body_style = styles['Normal']
    body_style.fontName = 'SourceSerif4-Regular'
    body_style.fontSize = 14
    body_style.leading = 20
    body_style.alignment = TA_LEFT
    body_style.spaceAfter = 6  

    final_story = []
    
    final_story.append(PageBreak()) 
    
    for paragraph_text in translated_content.split('\n\n'):
        if paragraph_text.strip():  
            if paragraph_text.isupper():
                final_story.append(Paragraph(paragraph_text.strip(), header_style))
            elif paragraph_text.strip().lower().startswith("subtitle"):
                final_story.append(Paragraph(paragraph_text.strip(), sub_header_style))
            else:
                final_story.append(Paragraph(paragraph_text.strip(), body_style))
            final_story.append(Spacer(1, 0.1 * inch))  

    try:
        doc.build(final_story, 
                    onFirstPage=first_page_layout, 
                    onLaterPages=page_number_footer) 

        buffer.seek(0) 
        
        pdf_file_name = f"{os.path.splitext(original_file_name)[0]}_translated_{target_language}.pdf"
        
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=pdf_file_name
        )
    except Exception as e:
        print(f"Error creating PDF: {e}")
        return jsonify({"error": f"Could not create PDF: {str(e)}"}), 500
