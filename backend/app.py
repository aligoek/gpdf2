# backend/app.py
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from googletrans import Translator
import PyPDF2
import io
import os
import threading
import time
import json
import re # Import regex for text cleaning
import base64 # Added to get PDF content as base64

# To load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# ReportLab imports for PDF creation
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.colors import black

app = Flask(__name__)
CORS(app)

# --- Firebase Admin SDK Initialization ---
# Securely read the FIREBASE_SERVICE_ACCOUNT_KEY environment variable.
# This variable should contain the content of your Firebase service account JSON file as a string.
# It is MANDATORY to set this variable in production environments.
try:
    service_account_info_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT_KEY')
    if service_account_info_json:
        # Load the JSON string obtained from the environment variable
        cred = credentials.Certificate(json.loads(service_account_info_json))
    else:
        # Explicitly raise an error if the environment variable is not set
        raise ValueError("FIREBASE_SERVICE_ACCOUNT_KEY environment variable is not set. Cannot initialize Firebase Admin SDK.")
    
    firebase_admin.initialize_app(cred, {
        # 'storageBucket': 'gpdf-c00c6.firebasestorage.app' # Not necessary if Firebase Storage is not used
    })
    db = firestore.client()
    print("Firebase Admin SDK initialized successfully. 🎉")
except Exception as e:
    print(f"Error initializing Firebase Admin SDK: {e}")
    # Ensure 'db' variable is None if initialization fails
    db = None 

# --- General variables from Canvas environment (though applicable for frontend) ---
APP_ID = os.environ.get('CANVAS_APP_ID', 'default-app-id')

# --- Translation Logic ---
translator = Translator()
MAX_CHUNK_SIZE = 4800 # characters

def extract_text_from_pdf(pdf_bytes):
    """
    Extracts text from PDF bytes using PyPDF2 and performs aggressive cleaning
    to ensure proper word and paragraph separation.
    """
    text = ""
    try:
        pdf_file = io.BytesIO(pdf_bytes)
        reader = PyPDF2.PdfReader(pdf_file)
        for page_num in range(len(reader.pages)):
            page = reader.pages[page_num]
            extracted_page_text = page.extract_text() or ""
            
            # --- Aggressive Text Cleaning ---
            # 1. Normalize all newlines to '\n'
            extracted_page_text = extracted_page_text.replace('\r\n', '\n').replace('\r', '\n')

            # 2. Replace common ligatures or problematic characters
            extracted_page_text = extracted_page_text.replace('ﬁ', 'fi').replace('ﬀ', 'ff')
            
            # 3. Join hyphenated words at line breaks (e.g., "trans-\nlation" -> "translation")
            extracted_page_text = re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', extracted_page_text)
            
            # 4. Replace single newlines (soft line breaks) with a space.
            # This helps to merge lines that are part of the same logical sentence/paragraph.
            # Targets newlines that are after an alphanumeric character or common punctuation and before an alphanumeric character.
            extracted_page_text = re.sub(r'(?<=[a-zA-Z0-9.,;])\n(?=[a-zA-Z0-9])', ' ', extracted_page_text)
            
            # 5. Normalize multiple newlines to exactly two newlines (paragraph breaks).
            # This ensures consistent and clear paragraph separation after soft line breaks are handled.
            extracted_page_text = re.sub(r'\n{2,}', '\n\n', extracted_page_text)
            
            # 6. Replace multiple spaces with a single space.
            extracted_page_text = re.sub(r'\s+', ' ', extracted_page_text)
            
            # 7. Trim whitespace from the beginning/end of the page content.
            extracted_page_text = extracted_page_text.strip()

            text += extracted_page_text
            text += "\n\n---PAGE_BREAK---\n\n" # Special separator for page breaks
    except Exception as e:
        print(f"Error extracting text from PDF: {e}")
        raise
    return text

def chunk_text(text, max_chunk_size):
    """
    Splits text into smaller chunks, trying to adhere to page breaks.
    """
    chunks = []
    current_chunk = ""
    pages = text.split("\n\n---PAGE_BREAK---\n\n")

    for page_content in pages:
        # If adding the current page content to the current chunk exceeds max chunk size,
        # or if the current chunk is empty and the page content is too large,
        # finalize the current chunk and start a new one.
        if len(current_chunk) + len(page_content) + 2 <= max_chunk_size:
            current_chunk += (page_content + "\n\n")
        else:
            if current_chunk: # If there's content in the current chunk, add it to chunks
                chunks.append(current_chunk.strip())
            
            current_chunk = page_content + "\n\n" # Start a new chunk with the current page content
            
            # If a single page's content is larger than MAX_CHUNK_SIZE,
            # split further within the page by sentence or newline.
            while len(current_chunk) > max_chunk_size:
                split_point = -1
                # Try to split at the last period within the limit
                last_period = current_chunk.rfind('.', 0, max_chunk_size)
                if last_period != -1:
                    split_point = last_period + 1
                # If no period, try to split at the last newline (paragraph break)
                if split_point == -1:
                    last_newline = current_chunk.rfind('\n', 0, max_chunk_size)
                    if last_newline != -1:
                        split_point = last_newline + 1
                # Fallback: if no natural split point, just cut at max chunk size
                if split_point == -1 or split_point == 0:
                    split_point = max_chunk_size 
                
                chunks.append(current_chunk[:split_point].strip())
                current_chunk = current_chunk[split_point:].strip()
    
    # Add any remaining content in current_chunk
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    # Filter out any empty chunks that might have been created
    return [chunk for chunk in chunks if chunk]

def translate_text_chunks(chunks, dest_lang, task_ref):
    """Translates text chunks and updates Firestore progress."""
    translated_chunks = []
    total_chunks = len(chunks)
    for i, chunk in enumerate(chunks):
        try:
            progress = int(((i + 1) / total_chunks) * 100)
            task_ref.update({
                'status': 'translating',
                'progress': progress
            })
            translated = translator.translate(chunk, dest=dest_lang)
            translated_chunks.append(translated.text)
            time.sleep(0.5) # Small delay to avoid hitting API rate limits
        except Exception as e:
            print(f"Error translating chunk {i+1}: {e}")
            translated_chunks.append(f"[Translation Error for chunk {i+1}: {e}] Original text: {chunk}")
            task_ref.update({
                'status': 'failed',
                'errorMessage': f"Translation of chunk {i+1} failed: {e}"
            })
            return None
    return translated_chunks

def process_translation_task(task_id, user_id, file_name, pdf_content_base64, target_language):
    """
    Manages the entire translation workflow in a separate thread.
    Now receives PDF content directly as a base64 string.
    """
    if not db:
        print(f"Firebase Admin SDK not initialized for task {task_id}. Aborting.")
        return

    task_ref = db.collection(f'artifacts/{APP_ID}/users/{user_id}/translations').document(task_id)

    try:
        # Use set with merge=True for initial update to ensure the document exists
        task_ref.set({'status': 'processing', 'progress': 10, 'initialTimestamp': firestore.SERVER_TIMESTAMP}, merge=True)
        
        pdf_bytes = base64.b64decode(pdf_content_base64)
        print(f"PDF received and decoded for task {task_id}: {file_name}")

        task_ref.update({'status': 'processing', 'progress': 30})
        original_text = extract_text_from_pdf(pdf_bytes)
        print(f"Text extracted from PDF for task {task_id}. Length: {len(original_text)} characters.")

        task_ref.update({'status': 'processing', 'progress': 50})
        chunks = chunk_text(original_text, MAX_CHUNK_SIZE)
        print(f"Text for task {task_id} split into {len(chunks)} chunks.")

        translated_chunks = translate_text_chunks(chunks, target_language, task_ref)
        if translated_chunks is None:
            return # Translation failed, error already reported to Firestore

        task_ref.update({
            'translatedContent': translated_chunks,
            'status': 'completed',
            'progress': 100,
            'completedAt': firestore.SERVER_TIMESTAMP
        })
        print(f"Translation task {task_id} completed successfully.")

    except Exception as e:
        print(f"Critical error processing translation task {task_id}: {e}")
        task_ref.update({
            'status': 'failed',
            'errorMessage': f"Critical backend error: {str(e)}",
            'failedAt': firestore.SERVER_TIMESTAMP
        })

# --- Flask Routes ---

@app.route('/')
def health_check():
    return jsonify({"status": "Backend is running!"})

@app.route('/translate', methods=['POST'])
def initiate_translation():
    """
    Receives a translation task initiation request from the frontend.
    Now receives PDF content as a base64 string directly.
    """
    data = request.json
    task_id = data.get('taskId')
    user_id = data.get('userId')
    file_name = data.get('fileName')
    pdf_content_base64 = data.get('pdfContent')
    target_language = data.get('targetLanguage')

    if not all([task_id, user_id, file_name, pdf_content_base64, target_language]):
        return jsonify({"error": "Missing required parameters"}), 400

    if not db:
        return jsonify({"error": "Firebase Admin SDK not initialized. Check backend logs."}), 500

    print(f"Translation request received for task {task_id} by user {user_id}")

    thread = threading.Thread(target=process_translation_task, 
                              args=(task_id, user_id, file_name, pdf_content_base64, target_language))
    thread.daemon = True # Allows the main program to exit even if the thread is still running
    thread.start()

    return jsonify({"message": "Translation process initiated", "taskId": task_id}), 202


# --- PDF Generation Logic ---

# Register Source Serif 4 fonts
# Ensure the 'fonts/' directory is present in your backend environment and contains the .ttf files.
# This 'fonts' directory should be in the ROOT directory of your project,
# not inside the 'backend' folder, for Render to find it correctly.
try:
    pdfmetrics.registerFont(TTFont('SourceSerif4-Regular', 'fonts/SourceSerif4-Regular.ttf'))
    pdfmetrics.registerFont(TTFont('SourceSerif4-Italic', 'fonts/SourceSerif4-Italic.ttf'))
    pdfmetrics.registerFont(TTFont('SourceSerif4-Bold', 'fonts/SourceSerif4-Bold.ttf'))
    pdfmetrics.registerFont(TTFont('SourceSerif4-BoldItalic', 'fonts/SourceSerif4-BoldItalic.ttf'))
    
    pdfmetrics.registerFont(TTFont('SourceSerif4-Title', 'fonts/SourceSerif4_36pt-Regular.ttf'))
    pdfmetrics.registerFont(TTFont('SourceSerif4-Subtitle', 'fonts/SourceSerif4_18pt-Regular.ttf'))

    print("Source Serif 4 fonts registered successfully.")
except Exception as e:
    print(f"Error registering Source Serif 4 fonts: {e}. PDF generation may use default fonts.")

# Define a function to add page numbers to later pages
def page_number_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont('SourceSerif4-Regular', 9)
    # Position the page number at the bottom right
    x_position = letter[0] - doc.rightMargin - (0.75 * inch)
    canvas.drawString(x_position, 0.75 * inch, f"Sayfa {doc.page}")
    canvas.restoreState()

# Define a first page layout function for a cover page
def first_page_layout(canvas, doc):
    canvas.saveState()
    
    styles = getSampleStyleSheet()

    # Main title style on the cover page
    cover_title_style = styles['h1'] # Clone from Heading1
    cover_title_style.fontName = 'SourceSerif4-Title'
    cover_title_style.fontSize = 36
    cover_title_style.leading = 40 # Line spacing
    cover_title_style.alignment = TA_CENTER
    cover_title_style.textColor = black

    # Subtitle style on the cover page (e.g., "Translated to X")
    cover_subtitle_style = styles['Normal']
    cover_subtitle_style.fontName = 'SourceSerif4-Subtitle'
    cover_subtitle_style.fontSize = 14
    cover_subtitle_style.leading = 16
    cover_subtitle_style.alignment = TA_CENTER
    cover_subtitle_style.textColor = black

    page_width, page_height = letter

    # Calculate content width and x-offset for centering
    content_width = page_width - (1.5 * inch) * 2
    x_offset = 1.5 * inch

    # Create the main title paragraph from the original file name
    main_title_text = doc.original_file_name.replace('_', ' ').replace('.pdf', '')
    main_title_paragraph = Paragraph(main_title_text, cover_title_style)

    # Calculate the size of the main title paragraph
    main_title_width, main_title_height = main_title_paragraph.wrapOn(canvas, content_width, page_height)
    
    # Vertically center the main title, slightly above the true center
    main_title_y_position = (page_height / 2) - (main_title_height / 2) + (0.5 * inch)

    # Draw the main title onto the canvas
    main_title_paragraph.drawOn(canvas, x_offset, main_title_y_position)

    # Create the "Translated to" subtitle paragraph
    translated_to_text = f"Translated to {doc.target_language.upper()}"
    translated_to_paragraph = Paragraph(translated_to_text, cover_subtitle_style)

    # Calculate the size of the subtitle paragraph
    translated_to_width, translated_to_height = translated_to_paragraph.wrapOn(canvas, content_width, page_height)

    # Position the subtitle at the bottom of the page with a consistent margin
    bottom_margin_for_subtitle = 1.5 * inch
    translated_to_y_position = bottom_margin_for_subtitle

    # Draw the subtitle onto the canvas
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
    
    # Configure the document with page size, margins, and custom attributes
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            rightMargin=0.8*inch, leftMargin=0.8*inch,
                            topMargin=0.8*inch, bottomMargin=0.8*inch)
    
    # Store original file name and target language as document attributes for the cover page
    doc.original_file_name = original_file_name
    doc.target_language = target_language

    styles = getSampleStyleSheet()
    
    # Customize header style
    header_style = styles['h1'] # Clone from Heading1
    header_style.fontName = 'SourceSerif4-Bold'
    header_style.fontSize = 18
    header_style.leading = 22
    header_style.alignment = TA_CENTER
    header_style.spaceAfter = 12  # Space after header

    # Customize sub-header style
    sub_header_style = styles['h2'] # Clone from Heading2
    sub_header_style.fontName = 'SourceSerif4-Italic'
    sub_header_style.fontSize = 16
    sub_header_style.leading = 20
    sub_header_style.alignment = TA_LEFT
    sub_header_style.spaceAfter = 8  # Space after sub-header

    # Customize normal text style
    body_style = styles['Normal']
    body_style.fontName = 'SourceSerif4-Regular'
    body_style.fontSize = 14
    body_style.leading = 20
    body_style.alignment = TA_LEFT
    body_style.spaceAfter = 6  # Space between paragraphs

    final_story = []
    
    # Add a page break to ensure main content starts on a new page after the cover
    final_story.append(PageBreak()) 
    
    # Create paragraphs from translated content, split by double newlines
    for paragraph_text in translated_content.split('\n\n'):
        if paragraph_text.strip():  # Filter out empty lines
            # Detect headings and text
            # Simple heuristic: treat text that is entirely uppercase as a heading
            if paragraph_text.isupper():
                final_story.append(Paragraph(paragraph_text.strip(), header_style))
            # Another heuristic: treat text starting with "Subtitle" as a sub-header
            elif paragraph_text.strip().lower().startswith("subtitle"):
                final_story.append(Paragraph(paragraph_text.strip(), sub_header_style))
            else:
                final_story.append(Paragraph(paragraph_text.strip(), body_style))
            final_story.append(Spacer(1, 0.1 * inch))  # Space between paragraphs

    try:
        # Build the PDF document with defined story and page layout functions
        doc.build(final_story, 
                  onFirstPage=first_page_layout, # Apply cover page layout
                  onLaterPages=page_number_footer) # Apply page number footer to subsequent pages

        buffer.seek(0) # Rewind the buffer
        
        # Create the download file name
        pdf_file_name = f"{os.path.splitext(original_file_name)[0]}_translated_{target_language}.pdf"
        
        # Send the generated PDF file as a response
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=pdf_file_name
        )
    except Exception as e:
        print(f"Error creating PDF: {e}")
        return jsonify({"error": f"Could not create PDF: {str(e)}"}), 500

# The `if __name__ == '__main__':` block is removed for Render deployment
# as Gunicorn will manage the application's lifecycle.
