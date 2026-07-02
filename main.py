"""
main.py
=======
Entry point orchestrator untuk AI Outreach Automation.

Alur lengkap (loop):
    merge_leads -> leads.json -> builder (Think: generate pesan)
    -> dashboard (Act: klik kirim WA, otomatis update status lewat /api/update-status)
    -> tracker followup (Observe: cek lead yang belum respons)
    -> builder lagi (generate pesan follow-up) -> ulang.

Cara pakai:
    python main.py bot              -> jalankan Telegram bot (terima command dari HP)
    python main.py get-chatid       -> helper untuk dapat TELEGRAM_CHAT_ID lo
    python main.py scan-websites    -> scan leads.json, update ada_website via Google Maps
    python main.py import             -> import semua CSV di data/raw_scrape/ ke database
    python main.py import file.csv    -> import satu file CSV spesifik
    python main.py import --dry-run   -> preview tanpa tulis ke database
    python main.py migrate-db     -> migrasi satu kali leads.json/sent.json -> outreach.db (#13)
    python main.py report         -> generate laporan PDF 7 hari terakhir
    python main.py report --hari 30 -> laporan PDF custom periode (30 hari)
    python main.py build          -> jalankan builder untuk semua lead baru/followup
    python main.py build --draft  -> generate ke status draft dulu (perlu review di dashboard)
    python main.py followup       -> tandai lead 'sent' yang sudah lama belum respons
    python main.py status         -> tampilkan ringkasan status (lewat tracker)
    python main.py serve          -> jalankan local server + dashboard di http://localhost:8000
    python main.py daily          -> jalankan satu siklus harian: followup → build → status
    python main.py                -> jalankan build lalu tampilkan ringkasan status
"""

import json
import sys
import http.server
import socketserver
from pathlib import Path

from agents import builder, tracker, db
from agents.config import PORT_DASHBOARD


def jalankan_merge():
    """Gabungkan data/priority_with_phones.json ke leads.json."""
    from agents import merge_leads
    merge_leads.main()


def jalankan_migrate_db():
    """(#13) Migrasi satu kali dari data/leads.json + data/sent.json ke data/outreach.db."""
    from agents import migrate_json_to_sqlite
    migrate_json_to_sqlite.main()


def jalankan_report():
    """Generate laporan PDF ringkasan outreach."""
    from agents import report
    report.main()


def jalankan_bot():
    """Jalankan Telegram bot — terima dan jalankan command dari HP."""
    from agents import telegram_bot
    telegram_bot.run_polling()


def dapatkan_chat_id():
    """
    Helper untuk setup Telegram: ambil chat_id dari update terbaru.
    User harus sudah kirim pesan apapun ke bot di Telegram sebelum jalankan ini.
    """
    from agents import telegram_bot
    print("[main] Mengambil chat_id dari Telegram...")
    chat_id = telegram_bot.get_chat_id()
    if chat_id:
        print(f"\n✓ CHAT_ID ditemukan: {chat_id}")
        print(f"  Tambahkan ke .env:\n  TELEGRAM_CHAT_ID={chat_id}")
    else:
        print("\n[main] Belum ada pesan ke bot.")
        print("[main] Langkah:")
        print("[main]   1. Buka Telegram → cari @ai_outreach_uwais_bot")
        print("[main]   2. Kirim pesan apapun (misal: /start)")
        print("[main]   3. Jalankan lagi: python main.py get-chatid")


def jalankan_scan_websites():
    """Scan leads.json dan update ada_website lewat Google Maps HTML."""
    from agents import cek_website
    import sys
    dry  = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    cek_website.main(dry_run=dry, force=force)


def jalankan_import():
    """Import CSV dari Instant Data Scraper ke leads.json."""
    from agents import csv_import
    from pathlib import Path as _Path
    args     = sys.argv[2:]  # argv[1] = "import"
    dry_run  = "--dry-run" in args
    paths    = [_Path(a) for a in args if not a.startswith("--")]
    csv_import.main(paths if paths else None, dry_run=dry_run)


def jalankan_build():
    """Generate pesan WA untuk semua lead baru/pending/followup di leads.json."""
    import sys
    mode_draft = "--draft" in sys.argv
    if mode_draft:
        print("[main] Mode DRAFT — pesan masuk antrian review dulu sebelum siap kirim.")
    else:
        print("[main] Menjalankan builder untuk generate pesan WA...")
    builder.main(mode_draft=mode_draft)


def jalankan_status():
    """Tampilkan ringkasan status lead lewat tracker."""
    tracker.cetak_ringkasan()


def jalankan_followup():
    """Cek lead 'sent' yang sudah lama belum respons, tandai 'followup_due'."""
    print("[main] Mengecek lead yang perlu di-follow-up...")
    tracker.cek_followup()


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    """
    Handler dashboard. Selain serve file statis (GET, bawaan SimpleHTTPRequestHandler),
    handler ini juga menerima:
    - GET  /api/data/leads dan /api/data/sent  → data langsung dari data/outreach.db
      (#13 — sejak migrasi SQLite, dashboard tidak lagi fetch data/leads.json /
      data/sent.json statis karena file itu sudah tidak diperbarui).
    - POST /api/update-status dan /api/update-pesan → dashboard bisa update status/pesan
      lead langsung (mis. saat tombol "Hubungi via WA" diklik), tanpa ketik manual
      lewat tracker.py.
    """

    def do_GET(self):
        if self.path == "/api/data/leads":
            self._kirim_json(db.muat_leads())
        elif self.path == "/api/data/sent":
            self._kirim_json(db.muat_sent())
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/update-status":
            self._handle_update_status()
        elif self.path == "/api/update-pesan":
            self._handle_update_pesan()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_update_pesan(self):
        """Simpan teks pesan yang sudah diedit dari modal dashboard."""
        try:
            panjang = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(panjang)
            payload = json.loads(body or b"{}")
            nomor_wa = payload.get("nomor_wa")
            pesan_baru = (payload.get("pesan") or "").strip()

            if not nomor_wa or not pesan_baru:
                self._kirim_json({"ok": False, "error": "nomor_wa dan pesan wajib diisi"}, 400)
                return

            sent = tracker.muat_sent()
            ditemukan = False
            for item in sent:
                if item.get("nomor_wa") == nomor_wa:
                    item["pesan"] = pesan_baru
                    ditemukan = True
                    break

            if ditemukan:
                tracker.simpan_sent(sent)
            self._kirim_json({"ok": ditemukan}, 200 if ditemukan else 404)
        except Exception as e:
            self._kirim_json({"ok": False, "error": str(e)}, 500)

    def _handle_update_status(self):
        try:
            panjang = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(panjang)
            payload = json.loads(body or b"{}")
            nomor_wa = payload.get("nomor_wa")
            status_baru = payload.get("status")

            if not nomor_wa or not status_baru:
                self._kirim_json({"ok": False, "error": "nomor_wa dan status wajib diisi"}, 400)
                return

            berhasil = tracker.update_status(nomor_wa, status_baru)
            self._kirim_json({"ok": berhasil}, 200 if berhasil else 400)
        except Exception as e:
            self._kirim_json({"ok": False, "error": str(e)}, 500)

    def _kirim_json(self, data: dict, kode: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(kode)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - signature dari parent class
        # Log lebih ringkas, biar terminal tidak penuh request file statis
        if self.path.startswith("/api/"):
            print(f"[serve] {self.address_string()} - {format % args}")


def jalankan_serve():
    """
    Jalankan HTTP server lokal supaya dashboard (dashboard/index.html) bisa
    fetch data lead/sent lewat GET /api/data/leads dan /api/data/sent
    (data/outreach.db), sekaligus update status lead lewat endpoint
    POST /api/update-status.
    """
    with socketserver.TCPServer(("", PORT_DASHBOARD), DashboardHandler) as httpd:
        print(f"[main] Dashboard tersedia di http://localhost:{PORT_DASHBOARD}/dashboard/")
        print("[main] Tekan Ctrl+C untuk berhenti.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[main] Server dihentikan.")


def jalankan_daily():
    """
    Satu siklus harian lengkap (Observe → Think → Act):
    1. followup — tandai lead 'sent' yang sudah 3+ hari belum respons
    2. build    — generate pesan baru + pesan follow-up
    3. status   — tampilkan ringkasan

    Cocok dijadwalkan via Windows Task Scheduler (sekali sehari):
        schtasks /create /tn "OutreachDaily" ^
          /tr "python C:\\path\\to\\ai-outreach\\main.py daily" ^
          /sc daily /st 08:00 /f

    Atau cukup double-click run_daily.bat yang ada di folder project.
    """
    from datetime import datetime
    print(f"\n[main] ══════ DAILY RUN — {datetime.now().strftime('%Y-%m-%d %H:%M')} ══════")
    jalankan_followup()
    jalankan_build()
    jalankan_status()
    print("[main] ══════ DAILY RUN SELESAI ══════\n")


def main():
    perintah = sys.argv[1] if len(sys.argv) > 1 else "all"

    if perintah == "bot":
        jalankan_bot()
    elif perintah == "get-chatid":
        dapatkan_chat_id()
    elif perintah == "scan-websites":
        jalankan_scan_websites()
    elif perintah == "import":
        jalankan_import()
    elif perintah == "merge":
        jalankan_merge()
    elif perintah == "migrate-db":
        jalankan_migrate_db()
    elif perintah == "report":
        jalankan_report()
    elif perintah == "build":
        jalankan_build()
    elif perintah == "followup":
        jalankan_followup()
    elif perintah == "status":
        jalankan_status()
    elif perintah == "serve":
        jalankan_serve()
    elif perintah == "daily":
        jalankan_daily()
    elif perintah == "all":
        jalankan_build()
        jalankan_status()
    else:
        print(f"[main] Perintah '{perintah}' tidak dikenali.")
        print("[main] Pilihan: bot | get-chatid | import | scan-websites | merge | migrate-db | report | build | build --draft | followup | status | serve | daily")


if __name__ == "__main__":
    main()
