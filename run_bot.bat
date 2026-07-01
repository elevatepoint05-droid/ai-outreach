@echo off
REM =====================================================================
REM run_bot.bat
REM Jalankan Telegram Bot — biarkan berjalan di background.
REM Bot menerima command dari HP lo via Telegram.
REM
REM Setup sebelum pakai:
REM   1. Isi TELEGRAM_BOT_TOKEN di .env (dapat dari BotFather)
REM   2. Jalankan: python main.py get-chatid
REM   3. Copy chat_id ke .env: TELEGRAM_CHAT_ID=xxxxx
REM   4. Double-click file ini untuk mulai bot
REM
REM Command yang tersedia dari HP:
REM   /status   - ringkasan pipeline
REM   /pending  - 5 pesan siap kirim
REM   /drafts   - draft butuh review
REM   /daily    - trigger siklus harian
REM   /build    - generate pesan saja
REM   /followup - cek lead yang perlu follow-up
REM =====================================================================

cd /d %~dp0
echo [bot] Memulai Telegram Bot...
echo [bot] Tekan Ctrl+C untuk menghentikan bot.
echo.
python main.py bot
pause
