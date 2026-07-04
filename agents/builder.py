"""
builder.py
==========
Baca leads.json, lalu generate pesan WhatsApp personal untuk tiap lead
pakai Groq API (model: llama-3.1-8b-instant).

Hasilnya disimpan ke sent.json dengan status awal "pending"
(pesan sudah dibuat, tapi belum benar-benar dikirim).

Cara pakai (standalone):
    python agents/builder.py

Catatan:
- leads.json untuk sekarang diisi manual / lewat merge_leads.py.
- Lead yang nomor WA-nya sudah ada di sent.json dengan status final
  (sent/replied/closed) akan dilewati (tidak dibuatkan pesan dobel).
- Lead berstatus "followup_due" (ditandai tracker.cek_followup) akan
  dibuatkan pesan follow-up, bukan pesan pembuka baru.

Upgrade dari versi sebelumnya:
- Prioritas: lead kategori klinik/hotel diproses lebih dulu (lihat agents/utils.py).
- Sudut pandang pesan disesuaikan per kategori (klinik/hotel/lainnya).
- Budget cap: maksimal N panggilan Groq API per run (safety layer, hindari
  boros kuota kalau leads.json membengkak). Atur lewat GROQ_MAX_CALLS_PER_RUN di .env.
- Mode follow-up otomatis untuk lead yang sudah lama tidak respons.
"""

import json
import os
import random
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

try:
    from .utils import klasifikasi_kategori, prioritas_lead
    from . import notif, backup, db
    from .log_setup import buat_logger
    from . import config as cfg
except ImportError:  # dijalankan standalone: python agents/builder.py
    from utils import klasifikasi_kategori, prioritas_lead
    import notif, backup, db
    from log_setup import buat_logger
    import config as cfg

load_dotenv()

log = buat_logger("builder")

BASE_DIR = Path(__file__).resolve().parent.parent
LEADS_PATH = BASE_DIR / "data" / "leads.json"
SENT_PATH = BASE_DIR / "data" / "sent.json"

MODEL = "llama-3.1-8b-instant"

# Safety layer — batasi jumlah panggilan API per run supaya tidak boros kuota
# kalau leads.json membengkak (mis. setelah merge_leads.py). Bisa di-override
# lewat .env: GROQ_MAX_CALLS_PER_RUN=60
BUDGET_DEFAULT = cfg.GROQ_MAX_CALLS

# Link portofolio contoh — diambil dari .env (PORTFOLIO_URL).
# Kalau kosong, pesan tidak menyebut link (tetap jalan normal).
PORTFOLIO_URL = cfg.PORTFOLIO_URL

PROMPT_SISTEM = """Kamu adalah seorang web developer freelance di Indonesia.
Tugasmu menulis pesan WhatsApp pertama yang tujuannya BUKAN langsung jualan,
tapi bikin pemilik bisnis REPLY dulu — apapun balasannya.

Filosofi "teaser first":
Pesan pertama yang langsung menawarkan jasa -> mudah diabaikan / di-report spam.
Pesan pertama yang bikin penasaran atau minta konfirmasi kecil -> reply rate jauh lebih tinggi.
Begitu mereka reply, baru percakapan bisa dilanjutkan ke arah obrolan yang lebih serius.

Contoh pendekatan yang EFEKTIF (pahami polanya, jangan ditiru kata-per-kata):
A. Konfirmasi sederhana:
   "Halo Bapak/Ibu dari [nama bisnis], saya lagi cari referensi [kategori] di [kota] --
   [nama bisnis] masih aktif melayani ya?"

B. Pseudo-audit (pendekatan value-first):
   "Selamat pagi, saya cek [nama bisnis] di Google Maps dan belum nemu website-nya.
   Apa memang sengaja tidak pakai website, atau belum sempat bikin?"

C. Curiosity hook dari data spesifik:
   "Halo dari [nama bisnis], lihat rating-nya bagus -- bisnis ramai ya?
   Selama ini pelanggan baru biasanya dapet info dari mana?"

Aturan WAJIB:
- Gunakan "saya", sapa dengan "Bapak/Ibu" atau nama bisnisnya langsung.
- Bahasa Indonesia sopan tapi ringan -- bukan kaku, bukan gaul.
- Maksimal 2 kalimat. Lebih pendek = lebih natural.
- JANGAN sebut kata "website", "jasa", "harga", "tawaran", atau kata-kata sales apapun.
- JANGAN pakai emoji.
- JANGAN tanya "apakah tertarik?" atau "apakah berkenan?" -- itu ciri pesan sales.
- HANYA pakai info yang ada di data. JANGAN mengarang detail yang tidak diberikan.
- Kalau ada "Data tambahan" (rating) atau "Insight riset" di data, pakai itu untuk memperkuat hook.
- Tujuan satu-satunya: bikin mereka reply satu kalimat apapun.
- Output HANYA isi pesannya, tanpa tanda kutip atau penjelasan tambahan."""

PROMPT_SISTEM_FOLLOWUP = """Kamu adalah seorang web developer freelance yang sopan dan profesional.
Tugasmu menulis pesan WhatsApp FOLLOW-UP singkat — ini BUKAN pesan pertama,
UMKM ini sudah pernah ditawari jasa pembuatan website beberapa hari lalu
tapi belum membalas.

Aturan:
- Gunakan "saya", sapa dengan "Bapak/Ibu" atau nama bisnisnya.
- Akui ini follow-up dengan halus (mis. "Menyambung pesan sebelumnya...", "Mau cek lagi apakah...") — jangan menuduh atau terkesan memaksa.
- 1-2 kalimat saja, lebih pendek dari pesan pertama.
- Tetap sopan, beri ruang untuk menolak (mis. "kalau belum tertarik tidak masalah").
- Jangan pakai emoji.
- Jangan sebut harga spesifik.
- Output HANYA isi pesannya, tanpa tanda kutip atau penjelasan tambahan."""

# Sudut pandang tambahan per kelompok kategori — diselipkan ke konteks user
# supaya pesan terasa relevan, bukan template generik yang sama untuk semua bisnis.
SUDUT_PANDANG = {
    "klinik": (
        "Pakai pendekatan pseudo-audit atau konfirmasi: tanya cara mereka dapat "
        "pasien baru sekarang, atau tanya kenapa belum ada di Google. "
        "Jangan sebut website atau jasa di pesan pertama."
    ),
    "hotel": (
        "Pakai pendekatan curiosity: tanya cara tamu dari luar kota biasanya "
        "nemuin penginapan mereka. Bikin mereka cerita dulu."
    ),
    "lainnya": "",
}

# A/B Testing — dua varian nada pesan.
# Template A: fokus masalah (belum ditemukan online = kehilangan pelanggan).
# Template B: fokus peluang (dengan website = bisa jangkau lebih banyak).
# Tiap lead di-assign salah satu secara acak, disimpan di field "template_id"
# supaya bisa dihitung reply rate per template di dashboard.
AB_TEMPLATES = {
    "A": (
        "Pendekatan: konfirmasi sederhana — tanya satu hal yang bikin mereka "
        "harus jawab (masih aktif, masih buka, dll). "
        "Jangan sebut website atau jasa sama sekali."
    ),
    "B": (
        "Pendekatan: pseudo-audit — sampaikan lo cek bisnis mereka dan "
        "tanya kenapa belum ada online presence, nada netral bukan judging. "
        "Bikin mereka jelasin situasi mereka sendiri."
    ),
    "C": (
        "Pendekatan: curiosity hook dari data spesifik — kalau ada rating tinggi "
        "atau kategori spesifik, jadikan itu hook genuine yang bikin mereka mau reply. "
        "Tutup dengan pertanyaan terbuka yang ringan."
    ),
}


def muat_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def simpan_json(path: Path, data: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def validasi_pesan(client: Groq, pesan: str, lead: dict) -> tuple[int, str]:
    """
    Self-critique (#18): minta Groq nilai sendiri kualitas pesan yang sudah dibuat.
    Return: (skor 1-10, alasan)

    Kriteria yang dinilai:
    - Naturalness: tidak kaku seperti template
    - Repetisi: nama bisnis tidak disebut > 2x
    - Panjang: 2-3 kalimat, tidak bertele-tele
    - Akurasi: tidak mengarang detail yang tidak ada di data

    Kalau parse JSON gagal → return (10, "") supaya tidak block pipeline.
    """
    prompt = (
        f"Nilai pesan WhatsApp bisnis berikut dari skala 1-10.\n\n"
        f"Pesan:\n{pesan}\n\n"
        f"Konteks: untuk bisnis \"{lead.get('nama', '')}\" "
        f"bidang \"{lead.get('kategori', '')}\" di {lead.get('kota', '')}.\n\n"
        "Kriteria:\n"
        "- Natural (tidak kaku / copy-paste template)\n"
        "- Nama bisnis disebut ≤ 2x\n"
        "- Panjang 2-3 kalimat, tidak bertele-tele\n"
        "- Tidak mengarang fakta yang tidak ada di konteks\n\n"
        "Jawab HANYA dalam format JSON (tanpa teks lain):\n"
        '{\"skor\": <1-10>, \"alasan\": \"<1 kalimat singkat>\"}'
    )
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=80,
        )
        raw = resp.choices[0].message.content.strip()
        # Bersihkan kalau ada backtick atau teks extra
        raw = raw.replace("```json", "").replace("```", "").strip()
        hasil = json.loads(raw)
        skor  = max(1, min(10, int(hasil.get("skor", 10))))
        return skor, str(hasil.get("alasan", ""))
    except Exception:
        return 10, ""   # gagal parse = anggap bagus, jangan block


def buat_pesan(
    client: Groq,
    lead: dict,
    mode: str = "baru",
    template_id: str = "A",
) -> tuple[str, int]:
    """
    Generate satu pesan WA personal untuk satu lead lewat Groq API.
    Kalau SELF_CRITIQUE_ENABLED aktif, validasi kualitas pesan sebelum return.

    Return: (isi_pesan, skor_kualitas)
    - mode "baru"     -> pesan pembuka pertama kali
    - mode "followup" -> pesan follow-up
    - template_id     -> "A" atau "B" (diabaikan untuk followup)
    """
    # Sanitasi data lead sebelum dipakai di prompt
    try:
        from . import data_sanitizer
    except ImportError:
        import data_sanitizer
    lead_bersih = data_sanitizer.sanitasi_lead(lead)

    if mode == "followup":
        konteks_user = (
            f"Nama bisnis: {lead_bersih['nama_display']}\n"
            f"Kategori usaha: {lead.get('kategori', 'tidak diketahui')}\n"
            f"Lokasi: {lead.get('kota', lead.get('alamat', 'tidak diketahui'))}\n"
            "Tulis pesan follow-up WA untuk bisnis ini (sudah pernah ditawari sebelumnya, belum dibalas)."
        )
        sistem = PROMPT_SISTEM_FOLLOWUP
    else:
        grup = klasifikasi_kategori(lead.get("kategori", ""))
        sudut_pandang = SUDUT_PANDANG.get(grup, "")
        ab_instruksi  = AB_TEMPLATES.get(template_id, AB_TEMPLATES["A"])
        konteks_user = (
            f"Nama bisnis: {lead_bersih['nama_display']}\n"
            f"Kategori usaha: {lead.get('kategori', 'tidak diketahui')}\n"
            f"Lokasi: {lead.get('kota', lead.get('alamat', 'tidak diketahui'))}\n"
        )
        if lead_bersih['rating_display']:
            konteks_user += f"Data tambahan: {lead_bersih['rating_display']}\n"
        if lead_bersih['alamat_display']:
            konteks_user += f"Alamat: {lead_bersih['alamat_display']}\n"
        if sudut_pandang:
            konteks_user += f"Sudut pandang: {sudut_pandang}\n"
        if PORTFOLIO_URL:
            konteks_user += f"Contoh website yang sudah pernah dibuat: {PORTFOLIO_URL}\n"
        # Sub-agent riset (opsional) — insight tambahan dari rating/alamat
        # yang selama ini belum dimanfaatkan. Read-only, degradasi aman
        # kalau gagal (insight None, proses generate pesan tetap lanjut).
        try:
            from . import sub_agent_research
        except ImportError:
            import sub_agent_research
        insight = sub_agent_research.riset_lead(lead)
        if insight:
            konteks_user += f"Insight riset (opsional, pakai kalau relevan): {insight}\n"
        konteks_user += f"Instruksi tambahan: {ab_instruksi}\n"
        konteks_user += "Tulis pesan WhatsApp pertama untuk bisnis ini. Ingat: JANGAN jualan, tujuannya bikin mereka reply dulu."
        sistem = PROMPT_SISTEM

    max_retry = cfg.SELF_CRITIQUE_MAX_RETRY if cfg.SELF_CRITIQUE_ENABLED else 0
    min_skor  = cfg.SELF_CRITIQUE_MIN_SKOR

    for percobaan in range(max_retry + 1):
        respon = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": sistem},
                {"role": "user", "content": konteks_user},
            ],
            temperature=0.8 + (percobaan * 0.05),  # sedikit naikkan variasi saat retry
            max_tokens=300,
        )
        isi_pesan = respon.choices[0].message.content.strip()

        # Self-critique (#18) — hanya kalau diaktifkan di .env
        if cfg.SELF_CRITIQUE_ENABLED:
            skor, alasan = validasi_pesan(client, isi_pesan, lead)
            if skor >= min_skor or percobaan >= max_retry:
                if percobaan > 0:
                    log.info(
                        f"[builder] Self-critique: skor {skor}/10 "
                        f"(setelah {percobaan} retry) — {alasan[:60]}"
                    )
                return isi_pesan, skor
            else:
                log.info(
                    f"[builder] Self-critique: skor {skor}/10 < {min_skor} "
                    f"— regenerate (percobaan {percobaan+1}/{max_retry}). Alasan: {alasan[:60]}"
                )
        else:
            return isi_pesan, 0   # self-critique mati, skor = 0 (tidak diukur)


def main(mode_draft: bool = False, kirim_notif: bool = True):
    """
    mode_draft=True  -> pesan di-generate ke status "draft" (perlu approve di dashboard dulu)
    mode_draft=False -> pesan langsung jadi "pending" (default, behaviour lama)

    kirim_notif=True  -> kirim notif Telegram ringkasan build (default, behaviour lama).
                         Dipakai oleh CLI, orchestrator, dan /build command.
    kirim_notif=False -> skip notif individual. Dipakai HANYA oleh agent loop
                         (_tool_build_pesan), karena agent_loop.py sudah punya
                         format_ringkasan() sendiri yang melaporkan hasil akhir ke user.

    Bisa juga dipanggil lewat CLI:
        python agents/builder.py --draft
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("[builder] GROQ_API_KEY belum diset. Isi dulu file .env (lihat .env.example).")
        return

    leads = db.muat_leads()
    if not leads:
        log.info("[builder] Belum ada lead di database. Isi dulu data lead-nya (lihat merge_leads.py).")
        return

    # Backup dulu sebelum ada perubahan (#11)
    backup.simpan()

    # Prioritas: klinik & hotel diproses duluan (lihat agents/utils.py)
    leads = sorted(leads, key=prioritas_lead)

    sent = db.muat_sent()
    # Status yang tidak boleh di-regenerate (sudah dikirim final / ada respons)
    STATUS_FINAL = {"sent", "replied", "closed"}
    # Lead berstatus "draft" yang sudah ada tidak di-regenerate (biar tidak overwrite edit manual)
    STATUS_SKIP = STATUS_FINAL | {"draft"}
    nomor_skip = {item["nomor_wa"] for item in sent if item.get("status") in STATUS_SKIP}

    # Index sent list berdasarkan nomor_wa untuk update data pending/followup_due
    indeks_sent = {item["nomor_wa"]: i for i, item in enumerate(sent)}

    client = Groq(api_key=api_key)
    pesan_baru = []
    pesan_diperbarui = 0
    panggilan_terpakai = 0
    budget_tercapai = False

    status_baru_default = "draft" if mode_draft else "pending"

    for lead in leads:
        nomor_wa = lead.get("nomor_wa")
        if not nomor_wa or nomor_wa in nomor_skip:
            continue

        # Skip lead yang terbukti sudah punya website (#8)
        if lead.get("ada_website"):
            continue

        if panggilan_terpakai >= BUDGET_DEFAULT:
            budget_tercapai = True
            break

        entri_lama = sent[indeks_sent[nomor_wa]] if nomor_wa in indeks_sent else None
        mode = "followup" if entri_lama and entri_lama.get("status") == "followup_due" else "baru"

        # A/B testing: lead baru dapat template acak, lead lama pertahankan template lama
        if mode == "followup":
            template_id = entri_lama.get("template_id", "A") if entri_lama else "A"
        elif entri_lama and entri_lama.get("template_id"):
            template_id = entri_lama["template_id"]  # pertahankan template lama (biar konsisten)
        else:
            template_id = random.choice(["A", "B", "C"])  # lead baru: acak

        try:
            isi_pesan, skor_kualitas = buat_pesan(client, lead, mode=mode, template_id=template_id)
            panggilan_terpakai += 1
        except Exception as e:
            log.info(f"[builder] Gagal generate pesan untuk '{lead.get('nama')}': {e}")
            continue

        grup = klasifikasi_kategori(lead.get("kategori", ""))

        if entri_lama is not None:
            entri_lama["pesan"]          = isi_pesan
            entri_lama["status"]         = status_baru_default
            entri_lama["kategori_group"] = grup
            entri_lama["template_id"]    = template_id
            if skor_kualitas:
                entri_lama["skor_pesan"] = skor_kualitas
            if mode == "followup":
                entri_lama["follow_up_count"] = entri_lama.get("follow_up_count", 0) + 1
            pesan_diperbarui += 1
            label = "follow-up" if mode == "followup" else status_baru_default
            log.info(f"[builder] Pesan {label} diperbarui untuk '{lead.get('nama')}' [tmpl:{template_id}].")
        else:
            pesan_baru.append({
                "nama":           lead.get("nama"),
                "nomor_wa":       nomor_wa,
                "kota":           lead.get("kota", ""),
                "kategori":       lead.get("kategori", ""),
                "kategori_group": grup,
                "template_id":    template_id,
                "skor_pesan":     skor_kualitas,
                "pesan":          isi_pesan,
                "status":         status_baru_default,
                "follow_up_count": 0,
            })
            log.info(f"[builder] Pesan dibuat untuk '{lead.get('nama')}' ({grup}) [tmpl:{template_id}] → {status_baru_default}.")

    if not pesan_baru and pesan_diperbarui == 0:
        print("[builder] Tidak ada lead baru atau pending yang perlu dibuatkan pesan.")
        return

    sent.extend(pesan_baru)
    db.simpan_sent(sent)

    bagian = []
    if pesan_baru:      bagian.append(f"{len(pesan_baru)} pesan baru")
    if pesan_diperbarui: bagian.append(f"{pesan_diperbarui} diperbarui")
    log.info(f"[builder] {', '.join(bagian)} (status: {status_baru_default}) — disimpan ke data/outreach.db (tabel sent).")
    if budget_tercapai:
        log.info(f"[builder] Budget {BUDGET_DEFAULT} panggilan API/run tercapai — sisa lead diproses di run berikutnya.")

    # Notif Telegram ringkasan build (#17) — dengan breakdown per kategori.
    # Di-skip kalau kirim_notif=False (dipanggil dari agent loop, yang punya
    # format_ringkasan() sendiri — tidak perlu notif individual tiap tool call).
    if kirim_notif:
        total_pending = sum(1 for s in sent if s.get("status") in {"pending", "draft"})
        breakdown = {"klinik": 0, "hotel": 0, "lainnya": 0}
        for s in sent:
            if s.get("status") in {"pending", "draft"}:
                grup = s.get("kategori_group", "lainnya")
                if grup in breakdown:
                    breakdown[grup] += 1
        notif.notif_build_selesai_v2(len(pesan_baru), pesan_diperbarui, total_pending, breakdown=breakdown)


if __name__ == "__main__":
    import sys
    main(mode_draft="--draft" in sys.argv)
