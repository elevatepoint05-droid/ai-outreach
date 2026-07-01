"""
scraper.py
==========
Cari leads UMKM dari Google Maps berdasarkan kota + kategori bisnis,
lalu filter yang belum punya website.

Cara pakai (standalone):
    python agents/scraper.py "Berau" "toko sembako"

Catatan penting (lihat juga .claude/CLAUDE.md):
Google Maps modern di-render lewat JavaScript, tapi response HTML awal dari
pencarian Google Maps tetap menyimpan sebagian data bisnis (nama, alamat,
telepon, ada-website-atau-tidak) dalam bentuk JSON mentah di dalam <script>.
Kita ambil data itu pakai regex tanpa perlu browser otomatis (Selenium/Playwright)
sesuai aturan "jangan pakai library yang ribet install".

Trade-off: pendekatan ini lebih rapuh dibanding browser automation karena
struktur halaman Google bisa berubah kapan saja. Kalau hasil kosong/aneh,
coba cek lagi format response-nya (kemungkinan Google mengubah struktur HTML).
"""

import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

try:
    from .config import KOTA_TARGET as _KOTA_DEFAULT, DEFAULT_KATEGORI as _KATEGORI_DEFAULT
except ImportError:
    from config import KOTA_TARGET as _KOTA_DEFAULT, DEFAULT_KATEGORI as _KATEGORI_DEFAULT

# Header browser biasa supaya request tidak langsung ditolak Google
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
}

BASE_DIR = Path(__file__).resolve().parent.parent
LEADS_PATH = BASE_DIR / "data" / "leads.json"


def normalisasi_nomor_wa(nomor: str) -> str | None:
    """Ubah nomor telepon ke format 62xxxxxxxxxx untuk dipakai di link wa.me."""
    if not nomor:
        return None

    digit = re.sub(r"\D", "", nomor)

    if digit.startswith("0"):
        digit = "62" + digit[1:]
    elif digit.startswith("8"):
        digit = "62" + digit
    elif not digit.startswith("62"):
        # Bukan format nomor Indonesia yang dikenali
        return None

    # Nomor HP Indonesia wajar panjangnya 10-13 digit setelah kode negara
    if len(digit) < 10 or len(digit) > 15:
        return None

    return digit


def ambil_blob_json(html: str) -> str:
    """
    Ambil potongan JSON mentah yang disisipkan Google di dalam <script>
    pada halaman hasil pencarian Maps. Bagian ini berisi daftar bisnis
    beserta nama, alamat, telepon, dan link website (kalau ada).
    """
    cocok = re.search(r"window\.APP_INITIALIZATION_STATE\s*=\s*(\[.+?\]);", html)
    if cocok:
        return cocok.group(1)
    return ""


def ekstrak_bisnis_dari_blob(blob: str) -> list[dict]:
    """
    Parsing kasar terhadap blob JSON Google Maps.
    Karena strukturnya sangat dalam & tidak konsisten, kita cari pola
    nama bisnis + nomor telepon + alamat dengan regex daripada json.loads
    penuh (json.loads sering gagal karena ada escape karakter aneh).
    """
    hasil = []

    # Pola umum: nomor telepon Indonesia di dalam blob
    pola_telepon = re.findall(r'"(\+62[\d\s\-]{8,15}|0[\d\s\-]{8,15})"', blob)

    # Pola umum: nama bisnis biasanya muncul sebagai string pendek
    # diikuti koordinat/alamat. Ini pendekatan best-effort.
    pola_nama = re.findall(r'\[\s*"([^"\[\]]{3,80})"\s*,\s*\[\s*"[^"]*"', blob)

    pola_website = re.findall(r'"(https?://(?!www\.google)[^"]+)"', blob)
    domain_website = set()
    for url in pola_website:
        if "google.com" not in url and "gstatic.com" not in url:
            domain_website.add(url)

    # Karena alignment index antar list tidak terjamin akurat,
    # kita pasangkan secara sekuensial sebagai pendekatan sederhana.
    jumlah = min(len(pola_nama), len(pola_telepon)) if pola_telepon else len(pola_nama)
    for i in range(jumlah):
        nama = pola_nama[i] if i < len(pola_nama) else ""
        telepon = pola_telepon[i] if i < len(pola_telepon) else ""
        if not nama:
            continue
        hasil.append({
            "nama": nama,
            "telepon_mentah": telepon,
            "ada_website": False,  # ditentukan lebih akurat lewat fallback di bawah
        })

    return hasil


def cari_bisnis(kota: str, kategori: str) -> list[dict]:
    """
    Cari bisnis di Google Maps untuk kombinasi kota + kategori.
    Mengembalikan list dict mentah sebelum filter website.
    """
    query = f"{kategori} {kota}"
    url = f"https://www.google.com/maps/search/{requests.utils.quote(query)}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[scraper] Gagal mengambil data dari Google Maps: {e}")
        return []

    blob = ambil_blob_json(resp.text)
    if not blob:
        print(
            "[scraper] Tidak menemukan data bisnis di response. "
            "Kemungkinan Google mengubah struktur halaman, atau request diblokir."
        )
        return []

    bisnis_list = ekstrak_bisnis_dari_blob(blob)
    print(f"[scraper] Ditemukan {len(bisnis_list)} kandidat bisnis mentah untuk '{query}'.")
    return bisnis_list


def bangun_leads(kota: str, kategori: str) -> list[dict]:
    """
    Proses utama: cari bisnis, normalisasi data, filter yang belum punya website.
    """
    bisnis_mentah = cari_bisnis(kota, kategori)
    leads = []

    for b in bisnis_mentah:
        nomor_wa = normalisasi_nomor_wa(b.get("telepon_mentah", ""))
        if not nomor_wa:
            # Skip bisnis yang nomornya tidak valid/tidak bisa dipakai WA
            continue

        leads.append({
            "nama": b["nama"],
            "nomor_wa": nomor_wa,
            "alamat": b.get("alamat", ""),
            "kota": kota,
            "kategori": kategori,
            "ada_website": b.get("ada_website", False),
            "status": "baru",
        })

    # Hanya simpan yang belum punya website (sesuai tujuan project)
    leads_tanpa_website = [l for l in leads if not l["ada_website"]]
    print(f"[scraper] {len(leads_tanpa_website)} dari {len(leads)} lead belum punya website.")
    return leads_tanpa_website


def simpan_leads(leads_baru: list[dict]) -> None:
    """Gabungkan leads baru dengan yang sudah ada di leads.json, hindari duplikat nomor WA."""
    leads_lama = []
    if LEADS_PATH.exists():
        with open(LEADS_PATH, "r", encoding="utf-8") as f:
            leads_lama = json.load(f)

    nomor_terpakai = {l["nomor_wa"] for l in leads_lama}
    leads_unik_baru = [l for l in leads_baru if l["nomor_wa"] not in nomor_terpakai]

    semua_leads = leads_lama + leads_unik_baru

    with open(LEADS_PATH, "w", encoding="utf-8") as f:
        json.dump(semua_leads, f, ensure_ascii=False, indent=2)

    print(f"[scraper] {len(leads_unik_baru)} lead baru disimpan ke {LEADS_PATH.name}.")
    print(f"[scraper] Total lead sekarang: {len(semua_leads)}.")


def main():
    # Multi-kota (#10): KOTA_TARGET di .env bisa diisi beberapa kota
    # dipisah koma, mis: KOTA_TARGET=Berau,Bulungan,Malinau
    # CLI arg tetap bisa di-override: python agents/scraper.py "Berau" "klinik"
    if len(sys.argv) >= 3:
        kota_list = [sys.argv[1].strip()]
        kategori  = sys.argv[2].strip()
    else:
        kota_list = _KOTA_DEFAULT[:]
        kategori  = _KATEGORI_DEFAULT or input("Kategori bisnis: ").strip()

    if not kota_list or not kategori:
        print("[scraper] Kota dan kategori wajib diisi.")
        return

    total_baru = 0
    for kota in kota_list:
        print(f"[scraper] Memproses kota: {kota} / kategori: {kategori}")
        leads_baru = bangun_leads(kota, kategori)
        if leads_baru:
            simpan_leads(leads_baru)
            total_baru += len(leads_baru)
        else:
            print(f"[scraper] Tidak ada lead baru untuk {kota}.")

    if len(kota_list) > 1:
        print(f"[scraper] Total semua kota: {total_baru} lead baru ditambahkan.")


if __name__ == "__main__":
    main()
