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
    data     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sent (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    nomor_wa TEXT,
    nama     TEXT,
    status   TEXT,
    data     TEXT NOT NULL
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
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _muat_tabel(tabel: str) -> list[dict]:
    with _konek() as conn:
        rows = conn.execute(f"SELECT data FROM {tabel} ORDER BY id").fetchall()
    return [json.loads(row["data"]) for row in rows]


def _simpan_tabel(tabel: str, data: list[dict]) -> None:
    """Replace-all isi tabel dengan `data` — mirror perilaku simpan_json()
    versi lama (tulis ulang seluruh file tiap kali ada perubahan)."""
    with _konek() as conn:
        conn.execute(f"DELETE FROM {tabel}")
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
