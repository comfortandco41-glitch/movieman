# Movie Man 🎬

High-performance Telegram bot pipeline that automates scraping movies from [mmsubchannel.com](https://mmsubchannel.com), bypassing ad/redirect shorteners, downloading high-quality movie files from Mega.nz, and uploading them directly to dedicated Telegram channels with full MTProto 2GB support.

---

## ⚡ Key Features

- **Multi-Source Scraping**: Full support for both **[HomieTV](https://www.homietv.com)** (sub-second REST API) and **[MMSubChannel](https://mmsubchannel.com)** (Playwright headless Chromium).
- **Interactive Server & Quality Selection**: When releases have multiple download servers (MegaUp, Usersdrive, Yoteshin, Telegram, 1080p, 720p), select your preferred server right from Telegram inline buttons.
- **Intelligent Ad Shortener Bypass**: Automated redirect resolution follows intermediate ad links and countdown pages directly to original download endpoints.
- **Multi-Protocol Download Engine**: High-speed Mega.nz chunk downloader (`mega.py`) + async HTTP streaming downloader (`httpx`) + Telegram direct channel/bot cloner.
- **Telegram MTProto 2GB Uploads**: Custom parallel multipart uploader with OpenSSL hardware acceleration (`cryptg`) capable of transferring full 1080p/4K releases up to 2GB.
- **Instant Search**: Search movie catalogs across sources with `/search <keyword>`.
- **Modern Python 3.11 - 3.13+ Support**: Built-in runtime compatibility layer (`app.utils.compat`) supporting modern async runtimes.

---

## 🚀 Quick Start

### 1. Requirements
- Windows 10/11 or Linux
- Python 3.11, 3.12, or 3.13
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Telegram API ID & Hash (from [my.telegram.org](https://my.telegram.org))

### 2. Installation

Clone or open the directory and run:

```bat
d:\Tools\Movie Man\run_bot.bat
```
*(The startup script automatically creates `.venv`, installs all dependencies, and downloads Playwright browser binaries).*

Alternatively, install manually:

```powershell
# Create and activate virtual environment
py -3.13 -m venv .venv
.venv\Scripts\activate

# Install dependencies and browser
pip install -r requirements.txt
playwright install chromium
```

### 3. Configuration (.env)

Edit `.env` in the root folder:

```env
TELEGRAM_BOT_TOKEN=8622162960:AAGNw0Pbk3rypG-xvS27OlG7o1QmxJgoUaY
TELEGRAM_CHANNEL_ID=-1004385468716

# Telethon MTProto (for up to 2GB uploads)
TELEGRAM_API_ID=35371287
TELEGRAM_API_HASH=ac878175b44bad178f99586814a8dc4b
TELETHON_SESSION=telethon.session

# Concurrency and storage
MAX_CONCURRENT_JOBS=1
WORKSPACE_DIR=./workspace
LOG_LEVEL=INFO
```

### 4. Run the Bot

```powershell
python -m app.main
```

---

## 🤖 Bot Commands

| Command | Description |
| :--- | :--- |
| `/start` | Welcome message and interactive main menu |
| `/latest` | Browse latest movies with source selector (HomieTV vs MMSubChannel) |
| `/search <query>` | Search movie titles across HomieTV and MMSubChannel |
| `/fetch <url>` | Instantly process any HomieTV, MMSubChannel, Mega, or Telegram URL |
| `/cancel <id>` | Terminate an active downloading or uploading job and clean workspace |

---

## 📁 Repository Structure

```
Movie Man/
├── app/
│   ├── bot/            # Telegram UI (handlers, callbacks, keyboards, progress bars)
│   ├── media/          # MTProto multipart uploader & workspace cleanup
│   ├── mega/           # Mega.nz downloading engine
│   ├── pipeline/       # Pipeline stages & async execution manager
│   ├── resolver/       # Ad-shortener bypass & redirect follower
│   ├── scraper/        # Playwright scraper for mmsubchannel.com
│   ├── utils/          # DNS, logging, crypto patches, Python 3.13 compat
│   ├── config.py       # Pydantic v2 validated settings
│   └── main.py         # Application entry point & dependency injection
├── .env                # Secret environment variables
├── requirements.txt    # Project dependencies
├── run_bot.bat         # One-click Windows runner
├── ARCHITECTURE.md     # Architectural deep-dive & design patterns
├── PERFORMANCE.md      # Speed, memory, and network optimization guide
└── TROUBLESHOOTING.md  # Common issues and solutions
```
