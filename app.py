import os
import shutil
import tempfile
import uuid

from flask import Flask, render_template_string, request, send_file
import yt_dlp

app = Flask(__name__)

MIN_CLIP = 15
MAX_CLIP = 60
DEFAULT_CLIP = 30

PAGE = """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Hype Klip</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px 16px;
    background: #f5f5f5; color: #1a1a1a; }
  h1 { font-size: 22px; margin-bottom: 4px; }
  p.sub { color: #555; font-size: 14px; margin-top: 0; }
  form { background: #fff; border-radius: 12px; padding: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
  label { display: block; font-size: 13px; color: #444; margin: 14px 0 6px; }
  input[type=url] { width: 100%; padding: 10px 12px; font-size: 15px; border: 1px solid #ccc;
    border-radius: 8px; box-sizing: border-box; }
  select { width: 100%; padding: 10px 12px; font-size: 15px; border: 1px solid #ccc; border-radius: 8px; }
  button { width: 100%; margin-top: 18px; padding: 12px; font-size: 16px; font-weight: 600;
    background: #0088b0; color: #fff; border: 0; border-radius: 8px; cursor: pointer; }
  button:disabled { opacity: 0.6; }
  .error { background: #ffe9e9; color: #7a0d0d; padding: 10px 12px; border-radius: 8px;
    font-size: 14px; margin-top: 14px; }
  .note { font-size: 12px; color: #777; margin-top: 18px; line-height: 1.5; }
</style>
</head>
<body>
  <h1>Hype Klip</h1>
  <p class="sub">Bir YouTube linki yapıştır, videonun en çok tekrar izlenen ("hype") anını klip olarak indir.</p>
  <form method="post" action="/clip" onsubmit="document.getElementById('btn').disabled=true; document.getElementById('btn').innerText='İşleniyor... (biraz sürebilir)';">
    <label for="url">YouTube linki</label>
    <input type="url" id="url" name="url" placeholder="https://www.youtube.com/watch?v=..." required value="{{ url or '' }}" />
    <label for="length">Klip uzunluğu</label>
    <select id="length" name="length">
      {% for l in [15, 20, 30, 45, 60] %}
        <option value="{{ l }}" {% if l == default_clip %}selected{% endif %}>{{ l }} saniye</option>
      {% endfor %}
    </select>
    <button id="btn" type="submit">Hype anını kes</button>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
  </form>
  <p class="note">Not: "hype anı", YouTube'un o videoda topladığı "en çok tekrar izlenen" grafiğinden bulunuyor —
  az izlenen ya da yeni videolarda bu veri olmayabilir. Sadece kullanma hakkın olan videolarda kullan.</p>
</body>
</html>
"""


def get_info(url):
    ydl_opts = {"quiet": True, "skip_download": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def best_window(heatmap, duration, length):
    """Slide a `length`-second window over the heatmap and return the
    (start, end) with the highest summed "most replayed" intensity."""
    if not heatmap or not duration:
        return None
    length = max(MIN_CLIP, min(MAX_CLIP, length, duration))
    segments = [(h["start_time"], h["end_time"], h.get("value") or 0) for h in heatmap]

    best_start, best_score = 0, -1
    for t0, _, _ in segments:
        t1 = min(duration, t0 + length)
        t0 = max(0, t1 - length)
        score = sum(v for (s, e, v) in segments if s < t1 and e > t0)
        if score > best_score:
            best_score, best_start = score, t0

    end = min(duration, best_start + length)
    start = max(0, end - length)
    return start, end


@app.route("/", methods=["GET"])
def index():
    return render_template_string(PAGE, error=None, url=None, default_clip=DEFAULT_CLIP)


@app.route("/clip", methods=["POST"])
def clip():
    url = (request.form.get("url") or "").strip()
    try:
        length = int(request.form.get("length", DEFAULT_CLIP))
    except ValueError:
        length = DEFAULT_CLIP
    length = max(MIN_CLIP, min(MAX_CLIP, length))

    if not url:
        return render_template_string(PAGE, error="Bir YouTube linki gir.", url=url, default_clip=length)

    try:
        info = get_info(url)
    except Exception as e:
        return render_template_string(PAGE, error=f"Video bilgisi alınamadı: {e}", url=url, default_clip=length)

    heatmap = info.get("heatmap")
    duration = info.get("duration") or 0

    if not heatmap:
        return render_template_string(
            PAGE,
            error="Bu videoda YouTube'un 'en çok tekrar izlenen' verisi yok (genelde az izlenen ya da yeni videolarda bulunmuyor). Farklı bir video dene.",
            url=url,
            default_clip=length,
        )

    window = best_window(heatmap, duration, length)
    if not window:
        return render_template_string(PAGE, error="Hype anı hesaplanamadı.", url=url, default_clip=length)

    start, end = window
    job_id = uuid.uuid4().hex[:10]
    workdir = tempfile.mkdtemp(prefix=f"clip-{job_id}-")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bv*[height<=1080]+ba/best[height<=1080]",
        "download_ranges": yt_dlp.utils.download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": True,
        "outtmpl": os.path.join(workdir, "clip.%(ext)s"),
        "merge_output_format": "mp4",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return render_template_string(PAGE, error=f"İndirme/kesme hatası: {e}", url=url, default_clip=length)

    files = os.listdir(workdir)
    if not files:
        shutil.rmtree(workdir, ignore_errors=True)
        return render_template_string(PAGE, error="Klip oluşturulamadı.", url=url, default_clip=length)

    out_file = os.path.join(workdir, files[0])
    response = send_file(out_file, as_attachment=True, download_name="hype-klip.mp4", mimetype="video/mp4")
    response.call_on_close(lambda: shutil.rmtree(workdir, ignore_errors=True))
    return response


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
