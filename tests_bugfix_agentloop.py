"""
tests_bugfix_agentloop.py
=========================
Test untuk bugfix /agentloop:
  1. field "ada_progress" muncul benar di return value _tool_build_pesan
  2. builder.main(kirim_notif=False) -> notif.kirim() TIDAK terpanggil
  3. builder.main(kirim_notif=True, default) -> behavior lama utuh (notif terpanggil)
  4. regression: /build (telegram) & `python main.py build` tetap kirim notif

Semua Groq API + Telegram di-mock, jadi tidak ada biaya kuota / kirim beneran.
Jalankan: python tests_bugfix_agentloop.py
"""

import sys
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from agents import builder, tool_registry, notif  # noqa: E402

PASS, FAIL = [], []


def ok(name):
    PASS.append(name)
    print(f"  ✅ {name}")


def bad(name, detail=""):
    FAIL.append(name)
    print(f"  ❌ {name} {('- ' + detail) if detail else ''}")


def _fake_lead():
    return {"nama": "Klinik A", "nomor_wa": "6281", "kota": "Bandung", "kategori": "klinik"}


# ------------------------------------------------------------------
# Helper: jalankan builder.main() dengan semua dependensi eksternal di-mock.
# Return: mock notif.kirim, supaya test bisa assert dipanggil / tidak.
# ------------------------------------------------------------------
def run_builder_main(kirim_notif_kwarg):
    """
    kirim_notif_kwarg: dict kwargs untuk builder.main (mis. {} atau {'kirim_notif': False}).
    Mensimulasikan 1 lead baru sukses dibuatkan pesan, supaya blok notif tereksekusi.
    """
    with mock.patch.object(builder, "os") as m_os, \
         mock.patch.object(builder, "db") as m_db, \
         mock.patch.object(builder, "backup") as m_backup, \
         mock.patch.object(builder, "Groq") as m_groq, \
         mock.patch.object(builder, "buat_pesan", return_value=("Halo pak", 0)), \
         mock.patch.object(notif, "kirim") as m_kirim:

        m_os.getenv.return_value = "FAKE_KEY"        # GROQ_API_KEY ada
        m_db.muat_leads.return_value = [_fake_lead()]
        m_db.muat_sent.return_value = []             # belum ada sent -> lead baru
        m_db.simpan_sent.return_value = None

        builder.main(**kirim_notif_kwarg)
        return m_kirim


# ------------------------------------------------------------------
# TEST 1 — field "ada_progress" muncul benar
# ------------------------------------------------------------------
def test_ada_progress():
    print("\n[1] Field 'ada_progress' di _tool_build_pesan")

    # Kasus A: pending BERUBAH (5 -> 6) => ada_progress True
    with mock.patch.object(tool_registry, "_hitung_pending_dan_draft", side_effect=[5, 6]), \
         mock.patch("agents.builder.main") as m_main:
        hasil = tool_registry._tool_build_pesan()
        if "ada_progress" not in hasil:
            bad("field 'ada_progress' ada di return", f"keys={list(hasil.keys())}")
        elif hasil["ada_progress"] is True and hasil["pending_sebelum"] == 5 and hasil["pending_sesudah"] == 6:
            ok("pending berubah (5->6) => ada_progress True")
        else:
            bad("pending berubah => ada_progress True", str(hasil))
        # bonus: pastikan builder.main dipanggil dengan kirim_notif=False
        _, kwargs = m_main.call_args
        if kwargs.get("kirim_notif") is False:
            ok("_tool_build_pesan memanggil builder.main(kirim_notif=False)")
        else:
            bad("_tool_build_pesan memanggil builder.main(kirim_notif=False)", str(kwargs))

    # Kasus B: pending TETAP (5 -> 5) => ada_progress False
    with mock.patch.object(tool_registry, "_hitung_pending_dan_draft", side_effect=[5, 5]), \
         mock.patch("agents.builder.main"):
        hasil = tool_registry._tool_build_pesan()
        if hasil.get("ada_progress") is False:
            ok("pending tetap (5->5) => ada_progress False")
        else:
            bad("pending tetap => ada_progress False", str(hasil))


# ------------------------------------------------------------------
# TEST 2 — kirim_notif=False => notif.kirim TIDAK terpanggil
# ------------------------------------------------------------------
def test_kirim_notif_false():
    print("\n[2] builder.main(kirim_notif=False) -> notif.kirim TIDAK dipanggil")
    m_kirim = run_builder_main({"kirim_notif": False})
    if m_kirim.call_count == 0:
        ok("notif.kirim() tidak dipanggil sama sekali (call_count=0)")
    else:
        bad("notif.kirim() tidak dipanggil", f"call_count={m_kirim.call_count}")


# ------------------------------------------------------------------
# TEST 3 — kirim_notif=True (default) => behavior lama utuh
# ------------------------------------------------------------------
def test_kirim_notif_true_default():
    print("\n[3] builder.main() default -> notif.kirim TETAP dipanggil (behavior lama)")
    m_kirim = run_builder_main({})   # tanpa kwarg = default True
    if m_kirim.call_count == 1:
        ok("default: notif.kirim() dipanggil 1x (behavior lama utuh)")
    else:
        bad("default: notif.kirim() dipanggil 1x", f"call_count={m_kirim.call_count}")

    # eksplisit True juga
    m_kirim2 = run_builder_main({"kirim_notif": True})
    if m_kirim2.call_count == 1:
        ok("eksplisit kirim_notif=True: notif.kirim() dipanggil 1x")
    else:
        bad("eksplisit kirim_notif=True: notif.kirim() dipanggil 1x", f"call_count={m_kirim2.call_count}")


# ------------------------------------------------------------------
# TEST 4 — regression: /build (telegram) & CLI `python main.py build`
#           memanggil builder.main TANPA kirim_notif=False (=> notif tetap kirim)
# ------------------------------------------------------------------
def test_regression_build_paths():
    print("\n[4] Regression: /build & CLI pakai default (notif tetap kirim)")

    # 4a. CLI path: main.py memanggil builder.main(mode_draft=...)
    main_src = (BASE / "main.py").read_text(encoding="utf-8")
    if "builder.main(mode_draft=mode_draft)" in main_src and "kirim_notif" not in main_src:
        ok("main.py build -> builder.main(mode_draft=...) tanpa kirim_notif (default True)")
    else:
        bad("main.py build tetap default", "cek pemanggilan builder.main di main.py")

    # 4b. orchestrator path
    orch_src = (BASE / "agents" / "orchestrator.py").read_text(encoding="utf-8")
    if "builder.main(mode_draft=cfg.DRAFT_MODE_DEFAULT)" in orch_src and "kirim_notif" not in orch_src:
        ok("orchestrator -> builder.main(...) tanpa kirim_notif (default True)")
    else:
        bad("orchestrator tetap default", "cek pemanggilan builder.main di orchestrator.py")

    # 4c. Hanya tool_registry yang boleh pakai kirim_notif=False
    tr_src = (BASE / "agents" / "tool_registry.py").read_text(encoding="utf-8")
    if "kirim_notif=False" in tr_src:
        ok("tool_registry (agent loop) satu-satunya yang pakai kirim_notif=False")
    else:
        bad("tool_registry pakai kirim_notif=False", "tidak ditemukan")

    # 4d. Fungsional: simulasi /build (default) -> notif terpanggil
    m_kirim = run_builder_main({})
    if m_kirim.call_count == 1:
        ok("simulasi /build (default) -> notif.kirim() terpanggil seperti biasa")
    else:
        bad("simulasi /build -> notif terpanggil", f"call_count={m_kirim.call_count}")


if __name__ == "__main__":
    test_ada_progress()
    test_kirim_notif_false()
    test_kirim_notif_true_default()
    test_regression_build_paths()

    print("\n" + "=" * 55)
    print(f"HASIL: {len(PASS)} passed, {len(FAIL)} failed")
    print("=" * 55)
    sys.exit(1 if FAIL else 0)
