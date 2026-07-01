"""
utils.py
========
Fungsi bantuan kecil yang dipakai bareng oleh beberapa agent.

Cara pakai:
    from agents.utils import klasifikasi_kategori, skor_lead, prioritas_lead
    from agents.utils import adalah_duplikat_fuzzy
"""

from difflib import SequenceMatcher

# ── Klasifikasi kategori ─────────────────────────────────────────────────────

KATA_KUNCI_KLINIK = {
    "klinik", "apotek", "dokter", "laboratorium", "kesehatan",
    "psikolog", "terapis", "perawat", "gigi", "medis", "medika",
}
KATA_KUNCI_HOTEL = {
    "hotel", "penginapan", "losmen", "wisma", "guest house", "homestay",
    "resort", "villa", "inn",
}


def klasifikasi_kategori(kategori: str) -> str:
    """
    Kelompokkan kategori bisnis mentah (mis. "Klinik Medis", "Hotel",
    "warung makan") jadi salah satu dari: 'klinik', 'hotel', atau 'lainnya'.
    """
    k = (kategori or "").lower()
    if any(kata in k for kata in KATA_KUNCI_KLINIK):
        return "klinik"
    if any(kata in k for kata in KATA_KUNCI_HOTEL):
        return "hotel"
    return "lainnya"


# ── Scoring lead (#9) ────────────────────────────────────────────────────────

def skor_lead(lead: dict) -> float:
    """
    Hitung skor prioritas lead. Angka lebih besar = diproses lebih dulu.

    Komponen skor:
    - Kategori group : klinik/hotel = 10 pts, lainnya = 5 pts
    - Rating Google  : ≥4.5 = 3 pts, ≥4.0 = 2 pts, ≥3.5 = 1 pt
    - Punya nomor WA : +1 pt

    Berguna kalau ada banyak lead dengan kategori sama — yang rating-nya lebih
    tinggi diprioritaskan lebih dulu (lebih mungkin bisnis serius & bisa bayar).
    """
    skor = 0.0

    grup = klasifikasi_kategori(lead.get("kategori", ""))
    skor += 10 if grup in ("klinik", "hotel") else 5

    try:
        rating = float(lead.get("rating") or 0)
        if rating >= 4.5:
            skor += 3
        elif rating >= 4.0:
            skor += 2
        elif rating >= 3.5:
            skor += 1
    except (ValueError, TypeError):
        pass

    if lead.get("nomor_wa"):
        skor += 1

    return skor


def prioritas_lead(lead: dict) -> float:
    """
    Key function untuk sorted() — angka lebih kecil = diproses lebih dulu.
    Kita negate skor supaya lead skor tertinggi masuk urutan pertama.
    """
    return -skor_lead(lead)


# ── Fuzzy dedup (#6) ─────────────────────────────────────────────────────────

def _rasio_mirip(a: str, b: str) -> float:
    """Rasio kemiripan dua string (0.0–1.0) pakai difflib.SequenceMatcher."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def adalah_duplikat_fuzzy(
    nama_baru: str,
    nama_existing: list[str],
    threshold: float = 0.80,
) -> tuple[bool, str]:
    """
    Cek apakah nama_baru mirip dengan salah satu nama di nama_existing.
    Mengembalikan (True, nama_yang_mirip) atau (False, "").

    Dua strategi:
    1. Substring: satu nama adalah bagian dari nama lain + rasio panjang ≥ 0.65
       → tangkap "Hotel Berlian" vs "Hotel Berlian Berau"
    2. SequenceMatcher ratio ≥ threshold
       → tangkap variasi penulisan lainnya

    Threshold default 0.80 (lebih longgar dari sebelumnya biar lebih banyak
    duplikat tertangkap, tapi masih aman menghindari false positive).
    """
    a = nama_baru.lower().strip()
    for nama in nama_existing:
        b = nama.lower().strip()
        if not b:
            continue
        # Strategi 1: substring containment
        if (a in b or b in a) and len(a) > 4 and len(b) > 4:
            rasio_panjang = min(len(a), len(b)) / max(len(a), len(b))
            if rasio_panjang >= 0.65:
                return True, nama
        # Strategi 2: fuzzy ratio
        if _rasio_mirip(a, b) >= threshold:
            return True, nama
    return False, ""


# ── Validasi nomor WA (#7) ────────────────────────────────────────────────────
#
# Logika sederhana & akurat untuk HP Indonesia:
# Setelah normalisasi ke 62xxx, nomor HP valid SELALU mulai "628[1-9]":
#   - "628" = kode negara 62 + awalan HP (8)
#   - digit ke-4 = 1-9 (prefix operator: Telkomsel 811-859, XL 856-878, dll)
# Nomor yang tidak mulai "628[1-9]" = telepon rumah / kantor (area code 62X).
# Contoh: 021 → 6221..., 0554 → 62554..., semua itu bukan HP → reject.

_OPERATOR_PREFIX_VALID = set("123456789")  # digit ke-4 setelah "628"


def validasi_nomor_wa(nomor: str) -> tuple[bool, str, str]:
    """
    Validasi + normalisasi nomor WA Indonesia.
    Return: (valid, nomor_normalized, alasan_jika_invalid)

    >>> validasi_nomor_wa("08123456789")      # HP Telkomsel
    (True, '628123456789', '')
    >>> validasi_nomor_wa("02112345678")      # telepon rumah Jakarta
    (False, '', 'nomor telepon rumah/kantor, bukan HP')
    >>> validasi_nomor_wa("625542743972")     # landline Berau 0554-xxx
    (False, '', 'nomor telepon rumah/kantor, bukan HP')
    """
    if not nomor:
        return False, "", "nomor kosong"

    d = "".join(c for c in str(nomor) if c.isdigit())
    if not d:
        return False, "", "tidak ada digit"

    # Normalisasi ke format 62xxx
    if d.startswith("0"):
        d = "62" + d[1:]
    elif d.startswith("8"):
        d = "62" + d
    elif not d.startswith("62"):
        return False, "", "format tidak dikenali (bukan awalan 0/8/62)"

    # Panjang wajar HP Indonesia: 11–13 digit total
    if len(d) < 11 or len(d) > 13:
        return False, "", f"panjang tidak wajar ({len(d)} digit)"

    # HP Indonesia selalu "628[1-9]xxx" setelah normalisasi
    if len(d) < 4 or d[2] != "8":
        return False, "", "nomor telepon rumah/kantor, bukan HP"
    if d[3] not in _OPERATOR_PREFIX_VALID:
        return False, "", f"prefix tidak valid (6280... tidak dikenal)"

    return True, d, ""


def filter_leads_valid(leads: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Split leads jadi (valid, invalid) berdasarkan validasi nomor WA.
    Lead invalid tetap dikembalikan (untuk logging), bukan dibuang begitu saja.
    """
    valid, invalid = [], []
    for lead in leads:
        ok, nomor_norm, alasan = validasi_nomor_wa(lead.get("nomor_wa", ""))
        if ok:
            lead = {**lead, "nomor_wa": nomor_norm}
            valid.append(lead)
        else:
            invalid.append({**lead, "_alasan_invalid": alasan})
    return valid, invalid

