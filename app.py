import os
import json
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
import google.generativeai as genai
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from config import Config
from models import db, User, Interview, Answer

load_dotenv()

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Configure Gemini API
if app.config['GEMINI_API_KEY']:
    genai.configure(api_key=app.config['GEMINI_API_KEY'])
    model = genai.GenerativeModel('gemini-pro')
else:
    model = None  # Fallback jika no API key
    print("Warning: GEMINI_API_KEY not set. Using mock evaluation.")

# Data simulasi: Job roles dan pertanyaan per tahap
JOB_ROLES = ["Data Scientist", "Software Engineer", "IT Support", "UI/UX Designer"]

QUESTIONS = {
    "HR": {
        "Data Scientist": "Mengapa Anda tertarik melamar posisi Data Scientist di perusahaan ini?",
        "Software Engineer": "Apa yang membuat Anda tertarik pada peran Software Engineer di tim kami?",
        "IT Support": "Bagaimana Anda melihat peran IT Support dalam mendukung operasional perusahaan?",
        "UI/UX Designer": "Apa motivasi Anda untuk berkarir sebagai UI/UX Designer dan mengapa di sini?"
    },
    "Behavioral": {
        "Data Scientist": "Ceritakan pengalaman Anda menangani dataset besar dan bagaimana Anda mengatasinya (gunakan STAR).",
        "Software Engineer": "Deskripsikan konflik tim yang Anda selesaikan (gunakan STAR).",
        "IT Support": "Ceritakan saat Anda menangani masalah teknis mendadak (gunakan STAR).",
        "UI/UX Designer": "Bagaimana Anda menangani feedback negatif dari user testing (gunakan STAR)?"
    },
    "Technical": {
        "Data Scientist": "Jelaskan bagaimana Anda membangun model machine learning untuk prediksi penjualan.",
        "Software Engineer": "Bagaimana Anda mengoptimasi kode Python untuk performa tinggi?",
        "IT Support": "Jelaskan langkah troubleshooting untuk jaringan yang down.",
        "UI/UX Designer": "Bagaimana Anda menggunakan tools seperti Figma untuk prototyping responsif?"
    }
}

# Prompt templates untuk Gemini (adaptif per tahap)
PROMPT_TEMPLATES = {
    "HR": """
    Anda adalah pewawancara HR. Evaluasi jawaban untuk posisi '{job_role}'.
    Pertanyaan: "{question}"
    Jawaban: "{answer}"
    Nilai berdasarkan motivasi, cultural fit, dan komunikasi (skor 1-5).
    Deteksi elemen STAR dasar.
    Output HARUS JSON valid: {{"score": <int 1-5>, "feedback": "<feedback konstruktif>", "star_elements_detected": {{"situation": <bool>, "task": <bool>, "action": <bool>, "result": <bool>}}}}
    """,
    "Behavioral": """
    Anda adalah pewawancara behavioral. Evaluasi menggunakan metode STAR lengkap untuk '{job_role}'.
    Pertanyaan: "{question}"
    Jawaban: "{answer}"
    Skor 1-5 berdasarkan kelengkapan STAR dan soft skills (teamwork, problem-solving).
    Output HARUS JSON valid: {{"score": <int 1-5>, "feedback": "<feedback spesifik>", "star_elements_detected": {{"situation": <bool>, "task": <bool>, "action": <bool>, "result": <bool>}}}}
    """,
    "Technical": """
    Anda adalah pewawancara teknis. Evaluasi akurasi dan relevansi untuk '{job_role}'.
    Pertanyaan: "{question}"
    Jawaban: "{answer}"
    Skor 1-5 berdasarkan pemahaman konsep teknis. Deteksi STAR jika relevan.
    Output HARUS JSON valid: {{"score": <int 1-5>, "feedback": "<feedback teknis>", "star_elements_detected": {{"situation": <bool>, "task": <bool>, "action": <bool>, "result": <bool>}}}}
    """
}

# Routes
@app.route('/init_db')  # Opsional: Jalankan sekali untuk create tables
def init_db():
    with app.app_context():
        db.create_all()
    return "Database tables created!"

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        job_role = request.form['job_role']
        if job_role not in JOB_ROLES:
            flash('Job role invalid.')
            return redirect(url_for('index'))
        stage = 'HR'
        interview = Interview(user_id=current_user.id, job_role=job_role, stage=stage)
        db.session.add(interview)
        db.session.commit()
        question = QUESTIONS[stage][job_role]
        return render_template('index.html', job_role=job_role, stage=stage, question=question, interview_id=interview.id, job_roles=JOB_ROLES)
    return render_template('index.html', job_roles=JOB_ROLES)

@app.route('/submit_answer/<int:interview_id>', methods=['POST'])
@login_required
def submit_answer(interview_id):
    data = request.get_json()
    stage = data.get('stage', 'HR')
    question = data.get('question')
    answer = data.get('answer')
    job_role = data.get('job_role')

    if not all([question, answer, job_role]):
        return jsonify({"error": "Missing data"}), 400

    # Mock evaluation jika no Gemini
    if not model:
        parsed = {"score": 4, "feedback": "Mock feedback: Jawaban baik, tapi tambah detail STAR.", "star_elements_detected": {"situation": True, "task": True, "action": False, "result": True}}

    else:
        prompt_template = PROMPT_TEMPLATES.get(stage, PROMPT_TEMPLATES['HR'])
        prompt = prompt_template.format(job_role=job_role, question=question, answer=answer)
        try:
            response = model.generate_content(prompt)
            gemini_output = response.text.strip()
            parsed = json.loads(gemini_output)
        except (json.JSONDecodeError, Exception) as e:
            print(f"Gemini error: {e}")
            parsed = {"score": 3, "feedback": f"Error parsing AI response: {gemini_output[:100]}. Coba lagi.", "star_elements_detected": {"situation": False, "task": False, "action": False, "result": False}}

    # Simpan ke DB
    interview = Interview.query.get(interview_id)
    if interview and interview.user_id == current_user.id:
        new_answer = Answer(
            interview_id=interview_id,
            question=question,
            answer_text=answer,
            score=parsed.get('score', 0),
            feedback=parsed.get('feedback', ''),
            star_detected=json.dumps(parsed.get('star_elements_detected', {})),
            stage=stage
        )