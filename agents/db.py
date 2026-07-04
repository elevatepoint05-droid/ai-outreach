"""
db.py
=====
Abstraksi SQLite untuk data leads & sent — pengganti akses langsung ke
data/leads.json dan data/sent.json (#13).

Kenapa pindah dari JSON ke SQLite:
- File JSON gampang korup kalau proses ke-interupsi di tengah `json.dump`
  (mis. laptop mati / script di-kill). Tulis ke SQLite jalan dalam transaksi,
  jadi lebih aman dari corruption.
- Ke depan lebih gampang query/filter (pencarian, sort per kolom) tanpa
  harus load semua data ke memori dulu.

Desain:
- Tiap lead/pesan disimpan sebagai satu baris. Field yang sering dipakai
  untuk filter (nomor_wa, nama, status) punya kolom sendiri (di-index),
  sisanya (kategori, rating, pesan, template_id, skor_pesan, dst — field
  ini banyak dan terus berubah tiap upgrade) disimpan sebagai JSON blob
  di kolom `data` supaya schema tidak perlu di-migrate ulang tiap kali
  ada field baru.
- Interface `muat_leads()` / `simpan_leads()` / `muat_sent()` / `simpan_sent()`
  sengaja dibikin sama persis kayak `muat_json()`/`simpan_json()` versi lama
  (load semua jadi list[dict], simpan = replace semua) — supaya kode yang
  sudah ada (builder.py, tracker.py, csv_import.py, dst) tinggal ganti
  satu baris pemanggilan tanpa perlu ubah logic lain.
- Alias bahasa Inggris (get_leads, get_sent, add_lead, add_sent, update_lead,
  update_sent) disediakan juga sebagai gula sintaksis untuk pemakaian baru.

Cara pakai:
    from agents import db
    leads = db.muat_leads()
    db.simpan_leads(leads)
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "outreach.db"

_SKEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    nomor_wa TEXT,
    nama     TEXT,
    status   TEXT,
    data     TEXT NOT NULL,
    research_insight TEXT DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS sent (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    nomor_wa TEXT,
    nama     TEXT,
    status   TEXT,
    data     TEXT NOT NULL,
    tanggal_deal TEXT DEFAULT NULL,
    nilai_deal   REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_leads_nomor_wa ON leads(nomor_wa);
CREATE INDEX IF NOT EXISTS idx_leads_status   ON leads(status);
CREATE INDEX IF NOT EXISTS idx_sent_nomor_wa  ON sent(nomor_wa);
CREATE INDEX IF NOT EXISTS idx_sent_status    ON sent(status);
CREATE TABLE IF NOT EXISTS agent_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    putaran    INTEGER,
    tool       TEXT,
    alasan     TEXT,
    hasil      TEXT,
    sukses     INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def _konek():
    """Context manager koneksi SQLite — bikin folder data/ kalau belum ada,
    pastikan skema tabel sudah dibuat, commit otomatis kalau tidak ada error."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SKEMA)
        _migrasi_skema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrasi_skema(conn) -> None:
    """Migrasi idempotent: tambah kolom `research_insight` ke tabel leads yang
    dibuat SEBELUM kolom ini ada. ALTER TABLE cuma dijalankan kalau kolomnya
    memang belum ada, jadi aman dipanggil tiap koneksi & tidak merusak data lama."""
    kolom = [r["name"] for r in conn.execute("PRAGMA table_info(leads)").fetchall()]
    if "research_insight" not in kolom:
        conn.execute("ALTER TABLE leads ADD COLUMN research_insight TEXT DEFAULT NULL")

    # Conversion tracking — kolom deal di tabel sent (DB lama belum punya).
    kolom_sent = [r["name"] for r in conn.execute("PRAGMA table_info(sent)").fetchall()]
    if "tanggal_deal" not in kolom_sent:
        conn.execute("ALTER TABLE sent ADD COLUMN tanggal_deal TEXT DEFAULT NULL")
    if "nilai_deal" not in kolom_sent:
        conn.execute("ALTER TABLE sent ADD COLUMN nilai_deal REAL DEFAULT 0")


def _muat_tabel(tabel: str) -> list[dict]:
    with _konek() as conn:
        rows = conn.execute(f"SELECT data FROM {tabel} ORDER BY id").fetchall()
    return [json.loads(row["data"]) for row in rows]


def _simpan_tabel(tabel: str, data: list[dict]) -> None:
    """Replace-all isi tabel dengan `data` — mirror perilaku simpan_json()
    versi lama (tulis ulang seluruh file tiap kali ada perubahan).

    Khusus tabel `leads`, kolom `research_insight` ikut ditulis dari
    item.get("research_insight") supaya full-replace TIDAK menghapus insight
    yang sudah tersimpan (nilainya di-carry lewat JSON blob juga)."""
    with _konek() as conn:
        conn.execute(f"DELETE FROM {tabel}")
        if tabel == "leads":
            conn.executemany(
                "INSERT INTO leads (nomor_wa, nama, status, data, research_insight) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        item.get("nomor_wa") or "",
                        item.get("nama") or "",
                        item.get("status") or "",
                        json.dumps(item, ensure_ascii=False),
                        item.get("research_insight"),
                    )
                    for item in data
                ],
            )
        elif tabel == "sent":
            conn.executemany(
                "INSERT INTO sent (nomor_wa, nama, status, data, tanggal_deal, nilai_deal) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        item.get("nomor_wa") or "",
                        item.get("nama") or "",
                        item.get("status") or "",
                        json.dumps(item, ensure_ascii=False),
                        item.get("tanggal_deal"),
                        item.get("nilai_deal") or 0,
                    )
                    for item in data
                ],
            )
        else:
            conn.executemany(
                f"INSERT INTO {tabel} (nomor_wa, nama, status, data) VALUES (?, ?, ?, ?)",
                [
                    (
                        item.get("nomor_wa") or "",
                        item.get("nama") or "",
                        item.get("status") or "",
                        json.dumps(item, ensure_ascii=False),
                    )
                    for item in data
                ],
            )


# ── Leads ─────────────────────────────────────────────────────────────────────

def muat_leads() -> list[dict]:
    """Ambil semua lead dari tabel `leads`. Setara muat_json(LEADS_PATH) versi lama."""
    return _muat_tabel("leads")


def simpan_leads(data: list[dict]) -> None:
    """Timpa semua isi tabel `leads` dengan `data`. Setara simpan_json(LEADS_PATH, data)."""
    _simpan_tabel("leads", data)


def tambah_lead(lead: dict) -> None:
    """Tambah satu lead baru tanpa perlu load-mutate-save manual di caller."""
    leads = muat_leads()
    leads.append(lead)
    simpan_leads(leads)


def update_lead(nomor_wa: str, **perubahan) -> bool:
    """Update field lead berdasarkan nomor_wa. Return True kalau ketemu & diupdate."""
    leads = muat_leads()
    for lead in leads:
        if lead.get("nomor_wa") == nomor_wa:
            lead.update(perubahan)
            simpan_leads(leads)
            return True
    return False


# ── Sent ──────────────────────────────────────────────────────────────────────

def muat_sent() -> list[dict]:
    """Ambil semua entri dari tabel `sent`. Setara muat_json(SENT_PATH) / muat_sent() versi lama."""
    return _muat_tabel("sent")


def simpan_sent(data: list[dict]) -> None:
    """Timpa semua isi tabel `sent` dengan `data`. Setara simpan_json(SENT_PATH, data)."""
    _simpan_tabel("sent", data)


def tambah_sent(item: dict) -> None:
    """Tambah satu entri sent baru tanpa perlu load-mutate-save manual di caller."""
    sent = muat_sent()
    sent.append(item)
    simpan_sent(sent)


def update_sent(nomor_wa: str, **perubahan) -> bool:
    """Update field entri sent berdasarkan nomor_wa. Return True kalau ketemu & diupdate."""
    sent = muat_sent()
    for item in sent:
        if item.get("nomor_wa") == nomor_wa:
            item.update(perubahan)
            simpan_sent(sent)
            return True
    return False


def get_sent_by_nomor(nomor_wa: str) -> dict | None:
    """Ambil satu entri sent berdasarkan nomor_wa. Return None kalau tidak ketemu.
    (Dipakai reply_assistant.py — butuh lookup satu record tanpa load-filter manual di caller.)"""
    for item in muat_sent():
        if item.get("nomor_wa") == nomor_wa:
            return item
    return None


def get_lead_by_nomor(nomor_wa: str) -> dict | None:
    """Ambil satu lead berdasarkan nomor_wa. Return None kalau tidak ketemu."""
    for lead in muat_leads():
        if lead.get("nomor_wa") == nomor_wa:
            return lead
    return None


# ── Research insight (caching hasil sub_agent_research) ────────────────────────

def simpan_research_insight(nomor_wa: str, insight: str) -> bool:
    """Simpan hasil riset (insight personalisasi) untuk satu lead ke DB.

    Ditulis ke DUA tempat sekaligus supaya konsisten:
    - kolom `research_insight` (buat query cepat get_leads_belum_diresearch)
    - field `research_insight` di JSON blob (biar muat_leads() ikut membawanya,
      dan tidak hilang saat full-replace simpan_leads()).

    Return True kalau lead-nya ketemu & terupdate, False kalau nomor tidak ada.
    """
    with _konek() as conn:
        row = conn.execute(
            "SELECT id, data FROM leads WHERE nomor_wa = ? LIMIT 1", (nomor_wa,)
        ).fetchone()
        if not row:
            return False
        d = json.loads(row["data"])
        d["research_insight"] = insight
        conn.execute(
            "UPDATE leads SET data = ?, research_insight = ? WHERE id = ?",
            (json.dumps(d, ensure_ascii=False), insight, row["id"]),
        )
    return True


def get_leads_belum_diresearch(limit: int = 3) -> list[dict]:
    """Ambil lead yang belum punya research_insight (IS NULL) dan status bukan
    'ada_website'. Dibatasi `limit` (default 3) supaya hemat API call per run."""
    with _konek() as conn:
        rows = conn.execute(
            "SELECT data FROM leads "
            "WHERE research_insight IS NULL AND status != 'ada_website' "
            "ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
    return [json.loads(row["data"]) for row in rows]


# ── Conversion tracking (deal / closing) ──────────────────────────────────────
# Catatan arsitektur: tabel `sent` menyimpan record kanonik di JSON blob `data`.
# Field deal (tanggal_deal, nilai_deal) ditulis ke JSON blob (kanonik, ikut
# terbawa muat_sent & tidak hilang saat full-replace) SEKALIGUS di-mirror ke
# kolom tanggal_deal/nilai_deal (buat query cepat). Fungsi baca di bawah
# sengaja baca dari JSON blob (muat_sent) supaya selalu akurat.

def catat_deal(nomor_wa: str, nilai_deal: float = 0.0) -> bool:
    """Catat deal/closing — lead ini beneran jadi klien bayar.

    Set tanggal_deal + nilai_deal dan ubah status jadi 'replied'. Kolom deal
    di tabel sent otomatis dibuat lewat _migrasi_skema (aman untuk DB lama).
    Return True kalau nomor ketemu & terupdate, False kalau tidak ada.
    """
    from datetime import datetime
    tanggal = datetime.now().isoformat(timespec="seconds")
    with _konek() as conn:
        row = conn.execute(
            "SELECT id, data FROM sent WHERE nomor_wa = ? LIMIT 1", (nomor_wa,)
        ).fetchone()
        if not row:
            return False
        d = json.loads(row["data"])
        d["tanggal_deal"] = tanggal
        d["nilai_deal"]   = nilai_deal
        d["status"]       = "replied"
        conn.execute(
            "UPDATE sent SET data = ?, status = 'replied', tanggal_deal = ?, nilai_deal = ? "
            "WHERE id = ?",
            (json.dumps(d, ensure_ascii=False), tanggal, nilai_deal, row["id"]),
        )
    return True


def get_conversion_stats() -> dict:
    """Hitung conversion rate dan total revenue dari data sent (JSON blob)."""
    sent = muat_sent()
    total_sent = sum(1 for s in sent if s.get("status") in ("sent", "replied", "followup_due"))
    deals = [s for s in sent if s.get("tanggal_deal")]
    total_deal = len(deals)
    total_revenue = sum(float(s.get("nilai_deal") or 0) for s in deals)
    rate = round((total_deal / total_sent * 100), 1) if total_sent else 0.0
    return {
        "total_sent": total_sent,
        "total_deal": total_deal,
        "conversion_rate_persen": rate,
        "total_revenue": total_revenue,
    }


def get_deals(limit: int = 20) -> list[dict]:
    """Ambil daftar lead yang sudah closing (punya tanggal_deal), terbaru dulu."""
    deals = [s for s in muat_sent() if s.get("tanggal_deal")]
    deals.sort(key=lambda s: s.get("tanggal_deal") or "", reverse=True)
    return deals[:limit]


# ── Agent history (log Think-Act-Observe agent_loop.py, buat dashboard) ────────

def log_agent_aksi(run_id: str, putaran: int, tool: str, alasan: str, hasil: dict, sukses: bool) -> None:
    """Catat satu langkah Think-Act-Observe ke tabel agent_history."""
    with _konek() as conn:
        conn.execute(
            """INSERT INTO agent_history (run_id, putaran, tool, alasan, hasil, sukses)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                putaran,
                tool,
                alasan,
                json.dumps(hasil, ensure_ascii=False),
                int(bool(sukses)),
            ),
        )


def get_agent_history(limit: int = 30) -> list[dict]:
    """Ambil riwayat aksi agent_loop.py terbaru, buat ditampilkan di dashboard."""
    with _konek() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_history ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()

    hasil_list = []
    for row in rows:
        item = dict(row)
        try:
            item["hasil"] = json.loads(item["hasil"]) if isinstance(item["hasil"], str) else item["hasil"]
        except Exception:
            pass
        item["sukses"] = bool(item["sukses"])
        hasil_list.append(item)
    return hasil_list


# ── Alias bahasa Inggris (gula sintaksis, opsional dipakai) ────────────────────

get_leads = muat_leads
save_leads = simpan_leads
add_lead = tambah_lead

get_sent = muat_sent
save_sent = simpan_sent
add_sent = tambah_sent


if __name__ == "__main__":
    # Sanity check cepat: tampilkan jumlah row di tiap tabel.
    print(f"[db] {DB_PATH}")
    print(f"[db] leads : {len(muat_leads())} baris")
    print(f"[db] sent  : {len(muat_sent())} baris")
