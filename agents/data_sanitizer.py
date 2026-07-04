"""
data_sanitizer.py
==================
Bersihkan dan normalisasi data lead sebelum dipakai AI untuk generate
pesan atau research insight.
"""

import re
from pathlib import Path

try:
    from .log_setup import buat_logger
except ImportError:
    from log_setup import buat_logger

log = buat_logger("data_sanitizer")

_POLA_GELAR = re.compile(
    r"\b(dr|drg|dr\.|drg\.|s\.h|m\.m|m\.kes|s\.ked|sp\.[a-z]+|"
    r"apt|s\.farm|s\.kep|skm|sst|amd|s\.gz|mt|se|st|sh)\b[,.]?",
    re.IGNORECASE
)
_POLA_KURUNG = re.compile(r"\(.*?\)")
_POLA_DASH_LOKASI = re.compile(
    r"\s*[-–]\s*(fisioterapi|klinik|apotek|rumah sakit|rs|puskesmas|"
    r"berau|bulungan|malinau|tarakan|samarinda|balikpapan|"
    r"jakarta|surabaya|bandung|makassar|cabang|pusat|utama)\b.*$",
    re.IGNORECASE
)
_POLA_MULTI_SPASI = re.compile(r"\s{2,}")
_POLA_SEPARATOR_AKHIR = re.compile(r"[\s,\-–|]+$")
_SINGKATAN_TETAP_UPPER = {"pmb", "rs", "rsud", "rsu", "poli", "kpri", "pt", "cv", "ud"}


def bersihkan_nama(nama: str | None) -> str:
    if not nama:
        return "bisnis ini"
    hasil = nama.strip()
    hasil = _POLA_KURUNG.sub("", hasil).strip()
    hasil = _POLA_DASH_LOKASI.sub("", hasil).strip()
    hasil = _POLA_GELAR.sub("", hasil)
    hasil = _POLA_MULTI_SPASI.sub(" ", hasil).strip()
    hasil = _POLA_SEPARATOR_AKHIR.sub("", hasil).strip()

    if hasil.isupper() and len(hasil) > 3:
        kata = hasil.title().split()
        hasil = " ".join(k.upper() if k.lower() in _SINGKATAN_TETAP_UPPER else k for k in kata)
    elif not any(c.isupper() for c in hasil[1:]):
        kata = hasil.title().split()
        hasil = " ".join(k.upper() if k.lower() in _SINGKATAN_TETAP_UPPER else k for k in kata)

    if len(hasil) < 3:
        return nama.strip().title()
    return hasil


def bersihkan_rating(rating) -> float | None:
    if rating is None:
        return None
    try:
        r = float(rating)
        return r if r > 0 else None
    except (ValueError, TypeError):
        return None


def format_rating_untuk_prompt(rating) -> str:
    r = bersihkan_rating(rating)
    if r is None:
        return ""
    if r >= 5.0:
        return f"Rating {r} di Google Maps (sangat tinggi, jarang ada bisnis yang sampai segini)"
    elif r >= 4.5:
        return f"Rating {r} di Google Maps (di atas rata-rata)"
    elif r >= 4.0:
        return f"Rating {r} di Google Maps"
    else:
        return ""


_ALAMAT_TIDAK_VALID = {
    "di sini", "sini", "sana", "depan", "belakang", "sebelah",
    "dekat", "pojok", "pinggir", "tengah", "atas", "bawah",
    "-", ".", "/", "", "tidak ada", "n/a", "unknown"
}
_PANJANG_MIN_ALAMAT = 10


def alamat_informatif(alamat: str | None) -> str | None:
    if not alamat:
        return None
    bersih = alamat.strip()
    if bersih.lower() in _ALAMAT_TIDAK_VALID:
        return None
    if len(bersih) < _PANJANG_MIN_ALAMAT:
        return None
    if re.match(r'^[\d\s\-.,/]+$', bersih):
        return None
    return bersih


def sanitasi_lead(lead: dict) -> dict:
    """Return versi bersih lead untuk prompt AI. Tidak mengubah data di DB."""
    return {
        **lead,
        "nama_display":   bersihkan_nama(lead.get("nama")),
        "rating_display": format_rating_untuk_prompt(lead.get("rating")),
        "alamat_display": alamat_informatif(lead.get("alamat")),
    }
