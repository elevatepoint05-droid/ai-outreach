"""
sub_agent_research.py
======================
Sub-agent riset — BEDA dari tool biasa di tool_registry.py karena punya
proses "mikir" sendiri (LLM reasoning call), bukan cuma jalanin fungsi tetap.

Batasan sub-agent (mencegah runaway / biaya tak terkendali):
1. TIDAK BISA memanggil sub-agent lain — no recursive spawning
2. READ-ONLY — tidak pernah mengubah data apapun
3. Default OFF (RESEARCH_SUBAGENT_ENABLED=false)
4. Skip tanpa panggil AI kalau data terlalu minim (hemat API call)
5. Degradasi dengan baik — kalau gagal, return None, TIDAK block build_pesan
"""

from pathlib import Path
from dotenv import load_dotenv

try:
    from . import config as cfg
    from .log_setup import buat_logger
except ImportError:
    import config as cfg
    from log_setup import buat_logger

log = buat_logger("sub_agent_research")

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

PROMPT_RESEARCH = """Kamu adalah sub-agent riset yang menganalisis data bisnis
yang SUDAH TERSEDIA (bukan mencari data baru dari internet), untuk menemukan
satu insight personalisasi yang bisa dipakai dalam pesan penawaran jasa website.

Fokus ke: rating (kalau tinggi, itu social proof kuat yang bisa disebut),
kategori spesifik bisnis, atau detail alamat yang unik. JANGAN mengarang
informasi yang tidak ada di data yang diberikan.

Jawab HANYA dengan satu kalimat insight singkat (maksimal 20 kata), atau
jawab PERSIS "TIDAK_ADA_INSIGHT" kalau datanya terlalu generic untuk
menghasilkan insight yang bermakna."""


def riset_lead(lead: dict) -> str | None:
    """
    Jalankan sub-agent riset untuk satu lead.
    Return: insight (string) atau None kalau tidak ada/fitur off/gagal.
    """
    if not getattr(cfg, "RESEARCH_SUBAGENT_ENABLED", False):
        return None

    api_key = cfg.GROQ_API_KEY
    if not api_key:
        return None

    rating  = lead.get("rating")
    kategori = lead.get("kategori", "")
    alamat  = lead.get("alamat", "")

    try:
        from . import data_sanitizer
    except ImportError:
        import data_sanitizer
    lead_bersih = data_sanitizer.sanitasi_lead(lead)

    rating_display = lead_bersih.get("rating_display", "")
    alamat_display = lead_bersih.get("alamat_display", "")
    nama_display   = lead_bersih.get("nama_display", lead.get("nama", ""))

    if not rating_display and not alamat_display:
        return None

    konteks = (
        f"Nama bisnis: {nama_display}\n"
        f"Kategori: {kategori or 'tidak diketahui'}\n"
        f"Rating: {rating_display or 'tidak ada data valid'}\n"
        f"Alamat: {alamat_display or 'tidak diketahui'}\n"
    )

    try:
        client = cfg.get_groq_client(api_key)
        model  = getattr(cfg, "GROQ_MODEL", "llama-3.1-8b-instant")
        respon = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PROMPT_RESEARCH},
                {"role": "user", "content": konteks},
            ],
            temperature=0.4,
            max_tokens=60,
        )
        insight = respon.choices[0].message.content.strip()

        if insight == "TIDAK_ADA_INSIGHT" or not insight:
            return None
        return insight

    except Exception as e:
        log.warning(f"[sub_agent_research] Gagal riset lead '{lead.get('nama')}': {e}")
        return None
