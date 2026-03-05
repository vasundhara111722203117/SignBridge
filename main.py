from flask import Flask, render_template, request, send_file, send_from_directory, Response
import os, subprocess, json, math, tempfile, shutil
import speech_recognition as sr
import nltk
from PIL import Image, ImageDraw, ImageFont
import datetime
import smtplib
from email.mime.multipart import MIMEMultipart  
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import smtplib
import speech_recognition as sr
from flask import Flask, render_template, request, redirect, url_for, flash
from googletrans import Translator
from gtts import gTTS
from email.utils import formataddr
try:
    from deep_translator import GoogleTranslator
    HAS_TRANSLATOR = True
except ImportError:
    HAS_TRANSLATOR = False

import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from flask import session, redirect, url_for, flash

from flask import jsonify


app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.secret_key = "supersecretkey123"  

DB_PATH = "users.db"
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            mail TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

FFMPEG_PATH  = r"C:\ffmpeg\bin\ffmpeg.exe"
FFPROBE_PATH = r"C:\ffmpeg\bin\ffprobe.exe"

nltk.download('punkt',     quiet=True)
nltk.download('punkt_tab', quiet=True)

LANG_TRANSLATE = {
    "en-US": None,
    "ta-IN": "ta",   # Tamil
    "te-IN": "te",   # Telugu
    "kn-IN": "kn",   # Kannada
    "ml-IN": "ml",   # Malayalam
    "mr-IN": "mr",   # Marathi
    "or-IN": "or",   # Odia
}
CURRENT_LANG = "en-US"  

@app.route('/')
def home(): 
    return render_template("landing.html")

@app.route("/live_voice")
def live_voice():
    return render_template("live_voice.html")


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        mail = request.form['mail'].strip()
        password = request.form['password'].strip()
        if not username or not password:
            flash("Username and password are required!")
            return redirect(url_for('register'))

        hashed = generate_password_hash(password)
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("INSERT INTO users (username, mail, password) VALUES (?, ?, ?)", (username, mail, hashed))
            conn.commit()
            conn.close()
            flash("Registration successful! Please login.")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("Username already exists.")
            return redirect(url_for('register'))

    return render_template("register.html")


@app.route("/process_speech", methods=["POST"])
def process_speech():
    data = request.json
    spoken_text  = data.get("text", "").strip()
    selected_lang = data.get("language", "en")   # output language code (short)
    input_lang    = data.get("input_lang", "auto") # input language BCP-47 or "auto"

    # Supported output language codes
    LANG_SHORT_MAP = {
        "ta": "ta",   # Tamil
        "te": "te",   # Telugu
        "kn": "kn",   # Kannada
        "ml": "ml",   # Malayalam
        "mr": "mr",   # Marathi
        "or": "or",   # Odia
        "en": "en",
    }
    dest_lang = LANG_SHORT_MAP.get(selected_lang, "en")

    if not spoken_text:
        return jsonify({"translated_text": ""})

    # Derive source language for translation
    # input_lang arrives as BCP-47 (e.g. "ta-IN", "en-US") or "auto"
    if input_lang and input_lang != "auto":
        src_short = input_lang.split("-")[0].lower()  # "ta-IN" -> "ta"
    else:
        src_short = "auto"

    # If source == destination, no translation needed
    if src_short == dest_lang:
        return jsonify({"translated_text": spoken_text})

    # Try deep_translator first (reliable for Indian languages)
    try:
        from deep_translator import GoogleTranslator as DeepGT
        result = DeepGT(source=src_short, target=dest_lang).translate(spoken_text)
        if result:
            return jsonify({"translated_text": result})
    except Exception:
        pass

    # Fallback: googletrans with auto-detect source
    try:
        translator = Translator()
        translated = translator.translate(spoken_text, src=src_short if src_short != "auto" else "auto", dest=dest_lang)
        if translated and translated.text:
            return jsonify({"translated_text": translated.text})
    except Exception:
        pass

    # Last resort: return original text unchanged
    return jsonify({"translated_text": spoken_text})

    
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        mail = request.form['mail'].strip()
        password = request.form['password'].strip()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT password FROM users WHERE mail = ?", (mail,))
        row = c.fetchone()
        conn.close()

        if row and check_password_hash(row[0], password):
            session['mail'] = mail
            
            return redirect(url_for('index'))
        else:
            flash("Invalid username or password.")
            return redirect(url_for('login'))

    return render_template("login.html")


@app.route('/logout')
def logout():
    session.pop('username', None)
    flash("Logged out successfully.")
    return redirect(url_for('login'))


from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            flash("Login required.")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function



def ffprobe_json(path, show="format"):
    r = subprocess.run([
        FFPROBE_PATH, "-v", "quiet", "-print_format", "json",
        f"-show_{show}", os.path.abspath(path)
    ], capture_output=True, text=True)
    return json.loads(r.stdout)

def get_video_duration(path):
    return float(ffprobe_json(path)["format"]["duration"])

def get_video_info(path):
    data = json.loads(subprocess.run([
        FFPROBE_PATH, "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", os.path.abspath(path)
    ], capture_output=True, text=True).stdout)
    dur, vh, fps = float(data["format"]["duration"]), 720, 25.0
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            vh = int(s.get("height", 720))
            try:
                n, d = s["r_frame_rate"].split("/")
                fps = float(n) / float(d)
            except: pass
            break
    return dur, vh, fps

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr

def send_video_complete_email(
    sender_email: str,
    sender_password: str,
    receiver_email: str,
    receiver_name: str,
    video_name: str,
    download_link: str,
    sender_name: str = "SignBridge"
):
   
    subject = f"Your video '{video_name}' is ready!"
    body_text = f"""
Hello {receiver_name},

Your video '{video_name}' has been successfully downloaded.



Thank you for using SignBridge!
- {sender_name}
"""
    

    msg = MIMEMultipart()
    msg['From'] = formataddr((sender_name, sender_email))
    msg['To'] = formataddr((receiver_name, receiver_email))
    msg['Subject'] = subject
    msg.attach(MIMEText(body_text, 'plain'))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.ehlo()
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, [receiver_email], msg.as_string())
        server.quit()
        print(f"Video ready email sent to {receiver_email}")
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False


import threading




def extract_audio(video_path):
    out = os.path.join(UPLOAD_FOLDER, "audio.wav")
    subprocess.run([
        FFMPEG_PATH, "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", out
    ], check=True)
    return out



def speech_to_text(audio_path, language="en-US"):
    CHUNK = 30
    rec   = sr.Recognizer()
    rec.energy_threshold = 300

    dur   = float(ffprobe_json(audio_path)["format"]["duration"])
    n     = max(1, math.ceil(dur / CHUNK))
    parts = []

    for i in range(n):
        tmp = os.path.join(UPLOAD_FOLDER, f"_c{i}.wav")
        subprocess.run([
            FFMPEG_PATH, "-y", "-ss", str(i*CHUNK), "-t", str(CHUNK),
            "-i", audio_path,
            "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", tmp
        ], check=True)
        try:
            with sr.AudioFile(tmp) as src:
                audio = rec.record(src)
            parts.append(rec.recognize_google(audio, language="en-US"))
        except sr.UnknownValueError: pass
        except sr.RequestError as e: print(f"[STT] chunk {i}: {e}")
        finally:
            try: os.remove(tmp)
            except: pass

    english = " ".join(parts) or "[Could not understand audio]"

    # Translate if needed
    target = LANG_TRANSLATE.get(language)
    if target and HAS_TRANSLATOR and not english.startswith("["):
        try:
            english = GoogleTranslator(source="auto", target=target).translate(english)
        except Exception as e:
            print(f"[TRANSLATE] {e}")

    return english



def create_sign_video_from_text(text):
    tokens = nltk.word_tokenize(text.lower())
    assets = os.path.join("static", "assets")
    paths  = []
    for word in tokens:
        wf = os.path.join(assets, word + ".mp4")
        if os.path.exists(wf): paths.append(wf)
        else:
            for ch in word:
                lf = os.path.join(assets, ch + ".mp4")
                if os.path.exists(lf): paths.append(lf)

    lst = os.path.join(UPLOAD_FOLDER, "sign_list.txt")
    out = os.path.join(UPLOAD_FOLDER, "sign_output.mp4")
    with open(lst, "w") as f:
        for p in paths:
            f.write(f"file '{os.path.abspath(p).replace(chr(92), '/')}'\n")
    subprocess.run([
        FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0", "-i", lst,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p", "-an", out
    ], check=True)
    return out

def speed_up_video(input_path, speed_factor):
    out = os.path.join(UPLOAD_FOLDER, "sign_fast.mp4")
    subprocess.run([
        FFMPEG_PATH, "-y", "-i", input_path.replace("\\", "/"),
        "-filter:v", f"setpts=PTS/{speed_factor}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p", "-an", out
    ], check=True)
    return out

def merge_videos(original, sign):
    import cv2
    out     = os.path.join(UPLOAD_FOLDER, "final.mp4")
    resized = os.path.join(UPLOAD_FOLDER, "sign_resized.mp4")
    cap     = cv2.VideoCapture(original)
    h       = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)); cap.release()
    h      += h % 2
    subprocess.run([
        FFMPEG_PATH, "-y", "-i", sign, "-vf", f"scale=-2:{h}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p", "-an", resized
    ], check=True)
    subprocess.run([
        FFMPEG_PATH, "-y", "-i", original, "-i", resized,
        "-filter_complex", "[0:v][1:v]hstack=inputs=2[v]",
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", out
    ], check=True)
    return out



def srt_to_vtt(srt, vtt):
    with open(srt, encoding="utf-8") as f: c = f.read()
    with open(vtt, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n" + c.replace(",", "."))

def parse_srt(path):
    cues = []
    with open(path, encoding="utf-8") as f: raw = f.read()
    for block in raw.strip().split("\n\n"):
        lines = block.strip().splitlines()
        ts, txt = None, []
        for i, l in enumerate(lines):
            if "-->" in l: ts = l; txt = lines[i+1:]; break
        if not ts: continue
        try:
            p = ts.split("-->")
            def t2s(t):
                t = t.strip().replace(",", ".")
                h, m, rest = t.split(":"); s, ms = rest.split(".")
                return int(h)*3600+int(m)*60+int(s)+float("0."+ms)
            cues.append({"start": t2s(p[0]), "end": t2s(p[1]),
                         "text": " ".join(txt).strip()})
        except: pass
    return cues




def burn_subtitles_beside(video_path, srt_path, output_path, language="en-US"):
    vp = os.path.abspath(video_path)
    op = os.path.abspath(output_path)

    duration, vh, fps = get_video_info(vp)
    rh = min(vh, 480); rh += rh % 2
    pw = max(320, rh); pw += pw % 2
    fs = max(14, rh // 18)

    STATIC_FONTS = "static/fonts"
    LANG_FONT = {
        "ml-IN": "NotoSansMalayalam-Regular.ttf",
        "ta-IN": "NotoSansTamil-Regular.ttf",
        "hi-IN": "NotoSansDevanagari-Regular.ttf",
        "te-IN": "NotoSansTelugu-Regular.ttf",
        "kn-IN": "NotoSansKannada-Regular.ttf",
        # New languages
        "or-IN": "NotoSansOriya-Regular.ttf",    # Odia
        "mr-IN": "NotoSansDevanagari-Regular.ttf", # Marathi (Devanagari script)
        "as-IN": "NotoSansBengali-Regular.ttf",   # Assamese (Bengali script)
    }

    font = None
    lang_font_file = LANG_FONT.get(language)
    if lang_font_file:
        lp = os.path.join(STATIC_FONTS, lang_font_file)
        if os.path.exists(lp):
            try: font = ImageFont.truetype(lp, fs); print(f"[BURN] font={lp}")
            except Exception as e: print(f"[BURN] lang font error: {e}")

    if font is None and os.path.exists(STATIC_FONTS):
        for fn in sorted(os.listdir(STATIC_FONTS)):
            if fn.lower().endswith(".ttf"):
                try: font = ImageFont.truetype(os.path.join(STATIC_FONTS, fn), fs); print(f"[BURN] font={fn}"); break
                except: pass

    # 3. Windows system fonts
    if font is None:
        for fp in [
            r"C:\Windows\Fonts\NirmalaUI.ttf",
            r"C:\Windows\Fonts\Kartika.ttf",
            r"C:\Windows\Fonts\Latha.ttf",
            r"C:\Windows\Fonts\Mangal.ttf",
            r"C:\Windows\Fonts\arial.ttf",
        ]:
            if os.path.exists(fp):
                try: font = ImageFont.truetype(fp, fs); print(f"[BURN] font={fp}"); break
                except: pass

    if font is None:
        font = ImageFont.load_default()
        print("[BURN] WARNING: default font — Indic text will show as boxes!")

    cues = parse_srt(srt_path)

    def make_img(text):
        img  = Image.new("RGB", (pw, rh), (0, 0, 0))
        if not text: return img
        draw = ImageDraw.Draw(img)
        words = text.split(); rows, cur = [], ""
        for w in words:
            test = (cur+" "+w).strip()
            try:    tw = draw.textlength(test, font=font)
            except: tw = len(test)*fs*0.6
            if tw <= pw-20: cur = test
            else:
                if cur: rows.append(cur)
                cur = w
        if cur: rows.append(cur)
        lh = fs + 6
        y  = (rh - len(rows)*lh) // 2
        for row in rows:
            try:    lw = draw.textlength(row, font=font)
            except: lw = len(row)*fs*0.6
            draw.text(((pw-lw)//2, y), row, font=font, fill=(255,255,255))
            y += lh
        return img

    timeline = []
    prev = 0.0
    for c in cues:
        if c["start"] > prev + 0.01:
            timeline.append((c["start"] - prev, ""))
        timeline.append((c["end"] - c["start"], c["text"]))
        prev = c["end"]
    if prev < duration - 0.01:
        timeline.append((duration - prev, ""))

    tmpdir = tempfile.mkdtemp()
    try:
        seen   = {}
        concat = []
        print(f"[BURN] Rendering {len(set(t for _, t in timeline))} unique images...")
        for seg_dur, text in timeline:
            if text not in seen:
                img_path = os.path.join(tmpdir, f"img_{len(seen):04d}.png")
                make_img(text).save(img_path)
                seen[text] = img_path.replace("\\", "/")
            concat.append(f"file '{seen[text]}'")
            concat.append(f"duration {max(seg_dur, 0.04):.6f}")

        concat_file = os.path.join(tmpdir, "concat.txt")
        with open(concat_file, "w") as f:
            f.write("\n".join(concat))

        panel = os.path.join(tmpdir, "panel.mp4")
        subprocess.run([
            FFMPEG_PATH, "-y",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-vf", f"scale={pw}:{rh},fps={fps:.3f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-an", panel
        ], check=True)
        print("[BURN] Panel done, merging...")

        scaled = os.path.join(tmpdir, "scaled.mp4")
        subprocess.run([
            FFMPEG_PATH, "-y", "-i", vp,
            "-vf", f"scale=-2:{rh}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-an", scaled
        ], check=True)

        subprocess.run([
            FFMPEG_PATH, "-y",
            "-i", scaled, "-i", panel, "-i", vp,
            "-filter_complex", "[0:v][1:v]hstack=inputs=2[v]",
            "-map", "[v]", "-map", "2:a?",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
            "-movflags", "+faststart", op
        ], check=True)

        print(f"[BURN] Done -> {op}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return output_path



@app.route('/index')
def index(): return render_template("index.html")

@app.route('/language_video')
def language_video(): return render_template("language_video.html")

@app.route('/language_trans', methods=['POST'])
def language_trans():
    input_type    = request.form.get("input_type", "video")
    language_code = request.form.get("language", "en-US")
    if language_code not in LANG_TRANSLATE:
        language_code = "en-US"

    global CURRENT_LANG
    CURRENT_LANG = language_code

    if input_type == "audio":
        # Audio-only upload: convert to WAV directly, no original.mp4
        file = request.files.get("audio")
        if not file or file.filename == "": return "No audio file uploaded", 400
        ext = os.path.splitext(file.filename)[-1].lower() or ".mp3"
        ap  = os.path.join(UPLOAD_FOLDER, "uploaded_audio" + ext)
        file.save(ap)
        wav_path = os.path.join(UPLOAD_FOLDER, "audio.wav")
        subprocess.run([
            FFMPEG_PATH, "-y", "-i", ap,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", wav_path
        ], check=True)
        orig_path = os.path.join(UPLOAD_FOLDER, "original.mp4")
        if os.path.exists(orig_path): os.remove(orig_path)
    else:
        # Video upload
        file = request.files.get("video")
        if not file or file.filename == "": return "No video file uploaded", 400
        vp = os.path.join(UPLOAD_FOLDER, "original.mp4")
        file.save(vp)
        wav_path = extract_audio(vp)

    text = speech_to_text(wav_path, language=language_code)

    sp = os.path.join(UPLOAD_FOLDER, "subtitles.srt")
    with open(sp, "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,000 --> 00:10:00,000\n" + text + "\n\n")

    return render_template("vi_audio.html",
                           subtitle_file="subtitles.srt",
                           subtitle_text=text,
                           input_type=input_type)

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        file = request.files['video']
        vp   = os.path.join(UPLOAD_FOLDER, "original.mp4")
        file.save(vp)
        extract_audio(vp)
        return render_template("audio.html")
    return render_template("upload.html")

@app.route('/sign', methods=['POST'])
def sign():
    audio    = os.path.join(UPLOAD_FOLDER, "audio.wav")
    text     = speech_to_text(audio)
    sign_p   = create_sign_video_from_text(text)
    original = os.path.join(UPLOAD_FOLDER, "original.mp4")
    od, sd   = get_video_duration(original), get_video_duration(sign_p)
    speed    = min((sd/od) if od > 0 else 1, 5)
    speed_up_video(sign_p, speed)
    return render_template("sign.html", sign_file="sign_fast.mp4",
        original_time=round(od,2), sign_time=round(sd,2), speed_value=round(speed,2))

@app.route('/merge', methods=['POST'])
def merge():
    original = os.path.join(UPLOAD_FOLDER, "original.mp4")
    sign     = os.path.join(UPLOAD_FOLDER, "sign_output.mp4")
    od, sd   = get_video_duration(original), get_video_duration(sign)
    speed    = min((sd/od) if od > 0 else 1, 5)
    fast     = speed_up_video(sign, speed)
    merge_videos(original, fast)
    return render_template("final.html",
        original_time=round(od,2), sign_time=round(sd,2), speed_value=round(speed,2))

@app.route('/merge_subtitle', methods=['POST'])
def merge_subtitle():
    out = os.path.join(UPLOAD_FOLDER, "video_with_subtitles.mp4")
    burn_subtitles_beside(
        os.path.join(UPLOAD_FOLDER, "original.mp4"),
        os.path.join(UPLOAD_FOLDER, "subtitles.srt"),
        out,
        language=CURRENT_LANG
    )
    user_email = session.get('mail')

    if user_email:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE mail = ?", (user_email,))
        row = c.fetchone()
        conn.close()

        if row:
            receiver_name = row[1]

            # 🔥 Send email in background (recommended)
            threading.Thread(
                target=send_video_complete_email,
                args=(
                    "vasundharac96@gmail.com",      # sender email
                    "ascayisctoyoesxu",             # app password
                    user_email,
                    receiver_name,
                    "signbridge_final.mp4",
                    "Your subtitle video building has started."
                )
            ).start()
    return send_file(out, as_attachment=True, download_name="video_with_subtitles.mp4")

@app.route('/uploads/subtitles.vtt')
def serve_vtt():
    sp = os.path.join(UPLOAD_FOLDER, "subtitles.srt")
    vp = os.path.join(UPLOAD_FOLDER, "subtitles.vtt")
    if os.path.exists(sp): srt_to_vtt(sp, vp)
    if not os.path.exists(vp): return Response("WEBVTT\n\n", mimetype="text/vtt")
    with open(vp, encoding="utf-8") as f: c = f.read()
    return Response(c, mimetype="text/vtt")

@app.route('/download')
def download():
    return send_file(os.path.join(UPLOAD_FOLDER, "final.mp4"),
                     as_attachment=True, download_name="signbridge_final.mp4")

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

if __name__ == '__main__':
    app.run(debug=False, port=3000)
