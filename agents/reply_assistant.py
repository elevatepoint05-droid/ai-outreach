"""
reply_assistant.py
===================
Bantu draftin balasan pas lead reply — bukan cuma tracking status "replied",
tapi kasih saran balasan yang natural, sesuai konteks pesan awal yang dikirim.

Kenapa dibutuhkan:
- Sistem sekarang cuma bisa nandain "replied" tapi user harus mikir sendiri
  mau balas apa. Kadang balasan lead itu ambigu (auto-reply chatbot,
  pertanyaan harga, penolakan halus, dll) — susah nebak respon yang pas
  tanpa mikir dari nol tiap kali.
- Reply assistant kasih draft balasan berdasarkan: pesan awal yang dikirim,
  kategori bisnis, dan isi balasan yang diterima dari lead.

PENTING - bukan auto-reply:
Ini BUKAN otomatis ngirim balasan (itu butuh WA Business API berbayar +
resiko ToS, sudah diputuskan skip). Ini cuma bantu DRAFT teks balasan,
user tetap yang review dan kirim manual dari WA-nya sendiri.

Cara pakai:
    python main.py balas 628xxxxxxxxx "isi pesan yang diterima dari lead"
    atau dari Telegram:
    /balas 628xxxxxxxxx <isi pesan yang diterima>
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

try:
    from . import db, tracker
    from .log_setup import buat_logger
    from . import config as cfg
except ImportError:
    import db, tracker
    from log_setup import buat_logger
    import config as cfg

log = buat_logger("reply_assistant")

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

MODEL = cfg.GROQ_MODEL if hasattr(cfg, "GROQ_MODEL") else "llama-3.1-8b-instant"

PROMPT_SISTEM = """Kamu adalah asisten yang membantu Bre, seorang freelancer web developer \
dan AI automation di Berau, Kalimantan Timur.

KONTEKS PENTING: Bre sedang menjalankan outreach — menawari jasa website/AI automation \
ke UMKM lokal (klinik, hotel, dll). Kamu bantu Bre draftin BALASAN dari Bre kepada \
pemilik bisnis tersebut. BUKAN sebaliknya.

Selalu ingat:
- Yang KIRIM pesan = Bre (freelancer)
- Yang DIKIRIMI = pemilik bisnis UMKM
- Draft yang kamu buat = kata-kata yang akan Bre kirim ke mereka

Gaya bahasa: santai tapi sopan, Bahasa Indonesia sehari-hari, 2-4 kalimat, \
tidak kaku, tidak terdengar template.

ATURAN PALING PENTING — CLOSING LOGIC:
Kalau balasan lead mengandung sinyal positif (contoh: "boleh", "iya", "tertarik",
"mau", "coba", "silakan", "oke", "bisa", "boleh juga", "gimana caranya") —
JANGAN tanya-tanya lagi. LANGSUNG kasih next step konkret yaitu tawarkan mockup/draft
gratis dulu. Contoh draft yang benar:
"Oke Pak, saya bikinin draft tampilan website-nya dulu gratis — kalau cocok baru
kita lanjut. Boleh kirim 3-5 foto usaha + nama lengkap & alamat bisnisnya?"
Ini low-commitment, mudah di-iya-in, dan langsung maju ke arah deal.

Jenis balasan dan cara meresponnya:
1. Tertarik/sinyal positif -> JANGAN tanya lagi, langsung tawarkan mockup gratis + minta foto & info bisnis
2. Nanya harga -> kasih range harga (Rp500rb-2jt tergantung fitur) + tawarkan mockup gratis dulu sebelum komitmen
3. Nanya detail/portfolio -> jelaskan singkat + tawarkan kirim contoh + tawarkan mockup gratis
4. Auto-reply/chatbot bisnis -> draft pesan singkat acknowledge auto-reply, minta diteruskan ke pemilik
5. Penolakan halus -> jangan maksa, kasih ruang, tawarkan follow-up kapan saja
6. Penolakan tegas -> terima kasih sopan, jangan push lagi
7. Pertanyaan teknis -> jawab jujur, sarankan diskusi lebih lanjut

Jawab HANYA dalam format:
JENIS: <jenis balasan>
DRAFT: <isi balasan dari Bre ke lead>"""


def draft_balasan(nomor_wa: str, pesan_masuk: str) -> dict:
    """
    Generate draft balasan berdasarkan konteks lead + isi pesan yang diterima.

    Return dict: {
        "draft": str,              -> teks balasan yang disarankan
        "jenis_terdeteksi": str,   -> kategori balasan (tertarik/auto-reply/dll)
        "lead": dict | None,       -> data lead untuk konteks
    }
    """
    lead_sent = db.get_sent_by_nomor(nomor_wa)
    lead_info = db.get_lead_by_nomor(nomor_wa)

    if not lead_sent:
        return {
            "draft": None,
            "jenis_terdeteksi": None,
            "lead": None,
            "error": f"Nomor {nomor_wa} tidak ditemukan di sistem. Pastikan sudah pernah dikirimi pesan.",
        }

    nama_bisnis = lead_sent.get("nama") or (lead_info.get("nama") if lead_info else "bisnis ini")
    kategori    = lead_sent.get("kategori") or (lead_info.get("kategori") if lead_info else "")
    pesan_awal  = lead_sent.get("pesan") or ""

    konteks_user = (
        f"Nama bisnis: {nama_bisnis}\n"
        f"Kategori: {kategori or 'tidak diketahui'}\n\n"
        f"Pesan yang KITA kirim sebelumnya:\n\"{pesan_awal}\"\n\n"
        f"Balasan yang KITA terima dari mereka:\n\"{pesan_masuk}\"\n\n"
        "Tugas: (1) tentukan jenis balasan ini (tertarik/nanya harga/auto-reply/"
        "penolakan halus/penolakan tegas/pertanyaan teknis/lainnya), "
        "(2) draftkan balasan WhatsApp yang tepat.\n\n"
        "Jawab HANYA dalam format:\nJENIS: <jenis balasan>\nDRAFT: <isi balasan>"
    )

    api_key = cfg.GROQ_API_KEY
    if not api_key:
        return {
            "draft": None, "jenis_terdeteksi": None, "lead": lead_sent,
            "error": "GROQ_API_KEY belum diset di .env",
        }

    client = cfg.get_groq_client(api_key)
    try:
        respon = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": PROMPT_SISTEM},
                {"role": "user", "content": konteks_user},
            ],
            temperature=0.7,
            max_tokens=250,
        )
        teks = respon.choices[0].message.content.strip()
    except Exception as e:
        return {
            "draft": None, "jenis_terdeteksi": None, "lead": lead_sent,
            "error": f"Gagal menghubungi Groq API: {e}",
        }

    # Parse output "JENIS: ...\nDRAFT: ..."
    jenis = "lainnya"
    draft = teks
    if "JENIS:" in teks and "DRAFT:" in teks:
        try:
            bagian_jenis = teks.split("JENIS:")[1].split("DRAFT:")[0].strip()
            bagian_draft = teks.split("DRAFT:")[1].strip()
            jenis = bagian_jenis
            draft = bagian_draft
        except Exception:
            pass  # fallback ke teks mentah kalau parsing gagal

    return {
        "draft": draft,
        "jenis_terdeteksi": jenis,
        "lead": lead_sent,
        "error": None,
    }


def proses_balasan(nomor_wa: str, pesan_masuk: str, tandai_replied: bool = True) -> dict:
    """
    Full flow: tandai status "replied" (kalau belum) + generate draft balasan.
    Ini yang dipanggil dari Telegram bot / CLI.
    """
    hasil = draft_balasan(nomor_wa, pesan_masuk)

    if not hasil.get("error") and tandai_replied:
        lead = hasil.get("lead")
        if lead and lead.get("status") != "replied":
            tracker.update_status(nomor_wa, "replied", kirim_notif=False)
            log.info(f"[reply_assistant] Status '{nomor_wa}' otomatis ditandai replied.")

    return hasil


def main() -> None:
    # Kalau dipanggil dari main.py, argv[1] = "balas" harus di-skip.
    # Kalau dijalankan standalone (python agents/reply_assistant.py), argv[1:] langsung dipakai.
    args = sys.argv[1:]
    if args and args[0] == "balas":
        args = args[1:]

    if len(args) < 2:
        print("[reply_assistant] Format: python main.py balas <nomor_wa> \"<pesan yang diterima>\"")
        return

    nomor_wa    = args[0]
    pesan_masuk = " ".join(args[1:])

    hasil = proses_balasan(nomor_wa, pesan_masuk)
    if hasil.get("error"):
        print(f"[reply_assistant] Error: {hasil['error']}")
        return

    print(f"\n📩 Jenis balasan terdeteksi: {hasil['jenis_terdeteksi']}")
    print(f"\n💬 Draft balasan:\n{hasil['draft']}\n")
    print("(Ini cuma draft — review dulu sebelum kirim manual dari WA kamu)")


if __name__ == "__main__":
    main()
