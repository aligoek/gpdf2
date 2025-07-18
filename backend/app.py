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
import re # Metin temizliği için regex'i içe aktar
import base64 # PDF içeriğini base64 olarak almak için eklendi

# .env dosyasından ortam değişkenlerini yüklemek için
from dotenv import load_dotenv
load_dotenv()

# PDF oluşturma için ReportLab içe aktarmaları
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

# --- Firebase Yönetici SDK Başlatma ---
# FIREBASE_SERVICE_ACCOUNT_KEY ortam değişkenini güvenli bir şekilde okuyun.
# Bu değişken, Firebase hizmet hesabı JSON dosyanızın içeriğini bir dize olarak içermelidir.
# Üretim ortamlarında bu değişkenin ayarlanması ZORUNLUDUR.
try:
    service_account_info_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT_KEY')
    if service_account_info_json:
        # Ortam değişkeninden alınan JSON dizesini yükleyin
        cred = credentials.Certificate(json.loads(service_account_info_json))
    else:
        # Ortam değişkeni ayarlanmamışsa açıkça hata verin
        raise ValueError("FIREBASE_SERVICE_ACCOUNT_KEY ortam değişkeni ayarlanmadı. Firebase Yönetici SDK başlatılamıyor.")
    
    firebase_admin.initialize_app(cred, {
        # 'storageBucket': 'gpdf-c00c6.firebasestorage.app' # Firebase Depolama kullanılmıyorsa gerekli değil
    })
    db = firestore.client()
    print("Firebase Yönetici SDK başarıyla başlatıldı. 🎉")
except Exception as e:
    print(f"Firebase Yönetici SDK başlatılırken hata oluştu: {e}")
    # Başlatma başarısız olursa 'db' değişkeninin None olduğundan emin olun
    db = None 

# --- Canvas ortamından genel değişkenler (ön uç için geçerli olsa da) ---
APP_ID = os.environ.get('CANVAS_APP_ID', 'default-app-id')

# --- Çeviri Mantığı ---
translator = Translator()
MAX_CHUNK_SIZE = 4800 # karakter

def extract_text_from_pdf(pdf_bytes):
    """
    PyPDF2 kullanarak PDF baytlarından metin çıkarır ve uygun kelime ve paragraf
    ayrımı sağlamak için agresif temizleme yapar.
    """
    text = ""
    try:
        pdf_file = io.BytesIO(pdf_bytes)
        reader = PyPDF2.PdfReader(pdf_file)
        for page_num in range(len(reader.pages)):
            page = reader.pages[page_num]
            extracted_page_text = page.extract_text() or ""
            
            # --- Agresif Metin Temizliği ---
            # 1. Tüm yeni satırları '\n' olarak normalleştirin
            extracted_page_text = extracted_page_text.replace('\r\n', '\n').replace('\r', '\n')

            # 2. Yaygın ligatürleri veya sorunlu karakterleri değiştirin
            extracted_page_text = extracted_page_text.replace('ﬁ', 'fi').replace('ﬀ', 'ff')
            
            # 3. Satır sonundaki tireli kelimeleri birleştirin (örn: "trans-\nlation" -> "translation")
            extracted_page_text = re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', extracted_page_text)
            
            # 4. Tek yeni satırları (yumuşak satır sonları) bir boşlukla değiştirin.
            # Bu, aynı mantıksal cümle/paragrafın parçası olan satırları birleştirmeye yardımcı olur.
            # Alfanümerik karakter veya yaygın noktalama işaretinden sonra ve alfanümerik karakterden önce gelen yeni satırları hedefler.
            extracted_page_text = re.sub(r'(?<=[a-zA-Z0-9.,;])\n(?=[a-zA-Z0-9])', ' ', extracted_page_text)
            
            # 5. Birden fazla yeni satırı tam olarak iki yeni satıra (paragraf sonları) normalleştirin.
            # Bu, yumuşak satır sonları işlendikten sonra tutarlı ve net paragraf ayrımı sağlar.
            extracted_page_text = re.sub(r'\n{2,}', '\n\n', extracted_page_text)
            
            # 6. Birden fazla boşluğu tek bir boşlukla değiştirin.
            extracted_page_text = re.sub(r'\s+', ' ', extracted_page_text)
            
            # 7. Sayfa içeriğinin başındaki/sonundaki boşlukları kırpın.
            extracted_page_text = extracted_page_text.strip()

            text += extracted_page_text
            text += "\n\n---PAGE_BREAK---\n\n" # Sayfa sonları için özel ayırıcı
    except Exception as e:
        print(f"PDF'den metin çıkarılırken hata oluştu: {e}")
        raise
    return text

def chunk_text(text, max_chunk_size):
    """
    Metni daha küçük parçalara böler, sayfa sonlarına uymaya çalışır.
    """
    chunks = []
    current_chunk = ""
    pages = text.split("\n\n---PAGE_BREAK---\n\n")

    for page_content in pages:
        # Mevcut sayfa içeriğini mevcut parçaya eklemek maksimum parça boyutunu aşarsa,
        # veya mevcut parça boşsa ve sayfa içeriği çok büyükse,
        # mevcut parçayı sonlandırın ve yeni bir tane başlatın.
        if len(current_chunk) + len(page_content) + 2 <= max_chunk_size:
            current_chunk += (page_content + "\n\n")
        else:
            if current_chunk: # Mevcut parçada içerik varsa, parçalara ekleyin
                chunks.append(current_chunk.strip())
            
            current_chunk = page_content + "\n\n" # Mevcut sayfa içeriğiyle yeni parça başlatın
            
            # Tek bir sayfanın içeriği MAX_CHUNK_SIZE'dan büyükse,
            # sayfa içinde cümle veya yeni satıra göre daha fazla bölün.
            while len(current_chunk) > max_chunk_size:
                split_point = -1
                # Limit içinde son noktadan bölmeye çalışın
                last_period = current_chunk.rfind('.', 0, max_chunk_size)
                if last_period != -1:
                    split_point = last_period + 1
                # Nokta yoksa, son yeni satırdan (paragraf sonu) bölmeye çalışın
                if split_point == -1:
                    last_newline = current_chunk.rfind('\n', 0, max_chunk_size)
                    if last_newline != -1:
                        split_point = last_newline + 1
                # Geri dönüş: doğal bir bölme noktası yoksa, sadece maksimum parça boyutunda kesin
                if split_point == -1 or split_point == 0:
                    split_point = max_chunk_size 
                
                chunks.append(current_chunk[:split_point].strip())
                current_chunk = current_chunk[split_point:].strip()
    
    # current_chunk'ta kalan içeriği ekleyin
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    # Oluşturulmuş olabilecek boş parçaları filtreleyin
    return [chunk for chunk in chunks if chunk]

def translate_text_chunks(chunks, dest_lang, task_ref):
    """Metin parçalarını çevirir ve Firestore ilerlemesini günceller."""
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
            time.sleep(0.5) # API hız sınırlarını aşmamak için küçük bir gecikme
        except Exception as e:
            print(f"Parça {i+1} çevirilirken hata oluştu: {e}")
            translated_chunks.append(f"[Parça {i+1} için Çeviri Hatası: {e}] Orijinal metin: {chunk}")
            task_ref.update({
                'status': 'failed',
                'errorMessage': f"Parça {i+1} çevirisi başarısız oldu: {e}"
            })
            return None
    return translated_chunks

def process_translation_task(task_id, user_id, file_name, pdf_content_base64, target_language):
    """
    Tüm çeviri iş akışını ayrı bir iş parçacığında yönetir.
    Şimdi PDF içeriğini doğrudan base64 dizesi olarak alır.
    """
    if not db:
        print(f"Görev {task_id} için Firebase Yönetici SDK başlatılmadı. İptal ediliyor.")
        return

    task_ref = db.collection(f'artifacts/{APP_ID}/users/{user_id}/translations').document(task_id)

    try:
        # Belgenin var olduğundan emin olmak için başlangıç güncellemesi için merge=True ile set kullanın
        task_ref.set({'status': 'processing', 'progress': 10, 'initialTimestamp': firestore.SERVER_TIMESTAMP}, merge=True)
        
        pdf_bytes = base64.b64decode(pdf_content_base64)
        print(f"Görev {task_id} için PDF alındı ve çözüldü: {file_name}")

        task_ref.update({'status': 'processing', 'progress': 30})
        original_text = extract_text_from_pdf(pdf_bytes)
        print(f"Görev {task_id} için PDF'den metin çıkarıldı. Uzunluk: {len(original_text)} karakter.")

        task_ref.update({'status': 'processing', 'progress': 50})
        chunks = chunk_text(original_text, MAX_CHUNK_SIZE)
        print(f"Görev {task_id} için metin {len(chunks)} parçaya bölündü.")

        translated_chunks = translate_text_chunks(chunks, target_language, task_ref)
        if translated_chunks is None:
            return # Çeviri başarısız oldu, hata zaten Firestore'a bildirildi

        task_ref.update({
            'translatedContent': translated_chunks,
            'status': 'completed',
            'progress': 100,
            'completedAt': firestore.SERVER_TIMESTAMP
        })
        print(f"Çeviri görevi {task_id} başarıyla tamamlandı.")

    except Exception as e:
        print(f"Çeviri görevi {task_id} işlenirken kritik hata: {e}")
        task_ref.update({
            'status': 'failed',
            'errorMessage': f"Kritik arka uç hatası: {str(e)}",
            'failedAt': firestore.SERVER_TIMESTAMP
        })

# --- Flask Rotaları ---

@app.route('/')
def health_check():
    return jsonify({"status": "Arka uç çalışıyor!"})

@app.route('/translate', methods=['POST'])
def initiate_translation():
    """
    Ön uçtan bir çeviri görevi başlatma isteği alır.
    Şimdi PDF içeriğini base64 dizesi olarak alır.
    """
    data = request.json
    task_id = data.get('taskId')
    user_id = data.get('userId')
    file_name = data.get('fileName')
    pdf_content_base64 = data.get('pdfContent')
    target_language = data.get('targetLanguage')

    if not all([task_id, user_id, file_name, pdf_content_base64, target_language]):
        return jsonify({"error": "Gerekli parametreler eksik"}), 400

    if not db:
        return jsonify({"error": "Firebase Yönetici SDK başlatılmadı. Arka uç günlüklerini kontrol edin."}), 500

    print(f"Kullanıcı {user_id} tarafından görev {task_id} için çeviri isteği alındı")

    thread = threading.Thread(target=process_translation_task, 
                              args=(task_id, user_id, file_name, pdf_content_base64, target_language))
    thread.daemon = True # Ana programın iş parçacığı hala çalışırken bile çıkmasına izin verir
    thread.start()

    return jsonify({"message": "Çeviri süreci başlatıldı", "taskId": task_id}), 202


# --- PDF Oluşturma Mantığı ---

# Source Serif 4 yazı tiplerini kaydet
# 'fonts/' dizininin arka uç ortamınızda bulunduğundan ve .ttf dosyalarını içerdiğinden emin olun.
try:
    pdfmetrics.registerFont(TTFont('SourceSerif4-Regular', 'fonts/SourceSerif4-Regular.ttf'))
    pdfmetrics.registerFont(TTFont('SourceSerif4-Italic', 'fonts/SourceSerif4-Italic.ttf'))
    pdfmetrics.registerFont(TTFont('SourceSerif4-Bold', 'fonts/SourceSerif4-Bold.ttf'))
    pdfmetrics.registerFont(TTFont('SourceSerif4-BoldItalic', 'fonts/SourceSerif4-BoldItalic.ttf'))
    
    pdfmetrics.registerFont(TTFont('SourceSerif4-Title', 'fonts/SourceSerif4_36pt-Regular.ttf'))
    pdfmetrics.registerFont(TTFont('SourceSerif4-Subtitle', 'fonts/SourceSerif4_18pt-Regular.ttf'))

    print("Source Serif 4 yazı tipleri başarıyla kaydedildi.")
except Exception as e:
    print(f"Source Serif 4 yazı tipleri kaydedilirken hata oluştu: {e}. PDF oluşturma varsayılan yazı tiplerini kullanabilir.")

# Sonraki sayfalara sayfa numaraları eklemek için bir fonksiyon tanımlayın
def page_number_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont('SourceSerif4-Regular', 9)
    # Sayfa numarasını sağ alt köşeye konumlandırın
    x_position = letter[0] - doc.rightMargin - (0.75 * inch)
    canvas.drawString(x_position, 0.75 * inch, f"Sayfa {doc.page}")
    canvas.restoreState()

# Bir kapak sayfası için ilk sayfa düzeni fonksiyonunu tanımlayın
def first_page_layout(canvas, doc):
    canvas.saveState()
    
    styles = getSampleStyleSheet()

    # Kapak sayfasındaki ana başlık stili
    cover_title_style = styles['h1']
    cover_title_style.fontName = 'SourceSerif4-Title'
    cover_title_style.fontSize = 36
    cover_title_style.leading = 40 # Satır aralığı
    cover_title_style.alignment = TA_CENTER
    cover_title_style.textColor = black

    # Kapak sayfasındaki alt başlık stili (örn: "Şuraya Çevrildi X")
    cover_subtitle_style = styles['Normal']
    cover_subtitle_style.fontName = 'SourceSerif4-Subtitle'
    cover_subtitle_style.fontSize = 14
    cover_subtitle_style.leading = 16
    cover_subtitle_style.alignment = TA_CENTER
    cover_subtitle_style.textColor = black

    page_width, page_height = letter

    # Ortalamak için içerik genişliğini ve x-ofsetini hesaplayın
    content_width = page_width - (1.5 * inch) * 2
    x_offset = 1.5 * inch

    # Orijinal dosya adından ana başlık paragrafını oluşturun
    main_title_text = doc.original_file_name.replace('_', ' ').replace('.pdf', '')
    main_title_paragraph = Paragraph(main_title_text, cover_title_style)

    # Ana başlık paragrafının boyutunu hesaplayın
    main_title_width, main_title_height = main_title_paragraph.wrapOn(canvas, content_width, page_height)
    
    # Ana başlığı dikey olarak ortalayın, gerçek merkezin biraz üzerine
    main_title_y_position = (page_height / 2) - (main_title_height / 2) + (0.5 * inch)

    # Ana başlığı tuvale çizin
    main_title_paragraph.drawOn(canvas, x_offset, main_title_y_position)

    # "Şuraya Çevrildi" alt başlık paragrafını oluşturun
    translated_to_text = f"Translated to {doc.target_language.upper()}"
    translated_to_paragraph = Paragraph(translated_to_text, cover_subtitle_style)

    # Alt başlık paragrafının boyutunu hesaplayın
    translated_to_width, translated_to_height = translated_to_paragraph.wrapOn(canvas, content_width, page_height)

    # Alt başlığı sayfanın altına tutarlı bir kenar boşluğuyla konumlandırın
    bottom_margin_for_subtitle = 1.5 * inch
    translated_to_y_position = bottom_margin_for_subtitle

    # Alt başlığı tuvale çizin
    translated_to_paragraph.drawOn(canvas, x_offset, translated_to_y_position)

    canvas.restoreState()


@app.route('/generate-pdf', methods=['POST'])
def generate_pdf():
    """
    Sağlanan çevrilmiş metinden estetik geliştirmelerle bir PDF oluşturur.
    """
    data = request.json
    translated_content = data.get('translatedContent')
    original_file_name = data.get('originalFileName', 'translated_document')
    target_language = data.get('targetLanguage', 'en')

    if not translated_content:
        return jsonify({"error": "PDF oluşturma için çevrilmiş içerik sağlanmadı."}), 400

    buffer = io.BytesIO()
    
    # Belgeyi sayfa boyutu, kenar boşlukları ve özel özniteliklerle yapılandırın
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            rightMargin=0.8*inch, leftMargin=0.8*inch,
                            topMargin=0.8*inch, bottomMargin=0.8*inch)
    
    # Orijinal dosya adını ve hedef dili kapak sayfası için belge öznitelikleri olarak saklayın
    doc.original_file_name = original_file_name
    doc.target_language = target_language

    styles = getSampleStyleSheet()
    
    # Başlık stilini özelleştirme
    header_style = styles['h1'] # Heading1'den klonlayın
    header_style.fontName = 'SourceSerif4-Bold'
    header_style.fontSize = 18
    header_style.leading = 22
    header_style.alignment = TA_CENTER
    header_style.spaceAfter = 12  # Başlık sonrası boşluk

    # Alt başlık stilini özelleştirme
    sub_header_style = styles['h2'] # Heading2'den klonlayın
    sub_header_style.fontName = 'SourceSerif4-Italic'
    sub_header_style.fontSize = 16
    sub_header_style.leading = 20
    sub_header_style.alignment = TA_LEFT
    sub_header_style.spaceAfter = 8  # Alt başlık sonrası boşluk

    # Normal metin stilini özelleştirme
    body_style = styles['Normal']
    body_style.fontName = 'SourceSerif4-Regular'
    body_style.fontSize = 14
    body_style.leading = 20
    body_style.alignment = TA_LEFT
    body_style.spaceAfter = 6  # Paragraflar arasında boşluk

    final_story = []
    
    # Ana içeriğin kapaktan sonra yeni bir sayfada başlamasını sağlamak için sayfa sonu ekleyin
    final_story.append(PageBreak()) 
    
    # Çevrilmiş içeriği çift yeni satırlarla ayırarak paragraflar oluşturun
    for paragraph_text in translated_content.split('\n\n'):
        if paragraph_text.strip():  # Boş satırları filtrelemek
            # Başlıkları ve metinleri tespit et
            # Basit bir sezgisel: Tamamen büyük harflerle yazılmış metni başlık olarak kabul et
            if paragraph_text.isupper():
                final_story.append(Paragraph(paragraph_text.strip(), header_style))
            # Başka bir sezgisel: "Subtitle" ile başlayan metni alt başlık olarak kabul et
            elif paragraph_text.strip().lower().startswith("subtitle"):
                final_story.append(Paragraph(paragraph_text.strip(), sub_header_style))
            else:
                final_story.append(Paragraph(paragraph_text.strip(), body_style))
            final_story.append(Spacer(1, 0.1 * inch))  # Paragraflar arasında boşluk

    try:
        # Tanımlanmış hikaye ve sayfa düzeni fonksiyonlarıyla PDF belgesini oluşturun
        doc.build(final_story, 
                  onFirstPage=first_page_layout, # Kapak sayfası düzenini uygulayın
                  onLaterPages=page_number_footer) # Sonraki sayfalara sayfa numarası altbilgisini uygulayın

        buffer.seek(0) # Arabelleği başa sarın
        
        # İndirme dosya adını oluşturun
        pdf_file_name = f"{os.path.splitext(original_file_name)[0]}_translated_{target_language}.pdf"
        
        # Oluşturulan PDF dosyasını yanıt olarak gönderin
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=pdf_file_name
        )
    except Exception as e:
        print(f"PDF oluşturulurken hata oluştu: {e}")
        return jsonify({"error": f"PDF oluşturulamadı: {str(e)}"}), 500


if __name__ == '__main__':
    # 'fonts' dizininin app.py ile aynı konumda olduğundan
    # ve 'SourceSerif4-Regular.ttf', 'SourceSerif4-Italic.ttf',
    # 'SourceSerif4-Bold.ttf', 'SourceSerif4-BoldItalic.ttf',
    # 'SourceSerif4_36pt-Regular.ttf', 'SourceSerif4_18pt-Regular.ttf' dosyalarını içerdiğinden emin olun.
    app.run(debug=True, port=5000)