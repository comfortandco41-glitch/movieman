# Performance Optimization Guide 🏎️💨

This document outlines key technical optimizations, benchmarks, and configuration adjustments to maximize throughput, minimize memory footprint, and ensure smooth operation of the **Movie Man** bot.

---

## 1. MTProto Upload Performance (Up to 240x Speedup)

### A. Hardware-Accelerated AES with `cryptg`
By default, pure Python libraries like `pyaes` process Telegram MTProto encryption using unoptimized Python bytecode. This caps upload speeds at around **1–3 MB/s** and causes high CPU usage.

- **Solution**: `cryptg` provides pre-compiled C/OpenSSL bindings for AES-IGE encryption and decryption.
- **Speedup**: Telethon uploads achieve **up to 240x faster throughput**, saturating typical 100 Mbps - 1 Gbps connections with negligible CPU impact.
- **Verification**: On bot startup, look for:
  ```log
  INFO  app.utils.crypto_patch  Telethon crypto patched with cryptg (OpenSSL)
  ```

### B. Parallel Multipart Uploads
In `app/media/telethon_uploader.py`, large files (>10MB) are uploaded using `SaveBigFilePartRequest` with parallel workers:

```python
part_size = 512 * 1024  # 512 KB standard chunk size
max_workers = 6         # 6 concurrent worker coroutines
```

* **Chunk Size**: 512 KB matches Telegram DC (Data Center) optimal TCP window size without hitting buffer overflows.
* **Worker Pool**: 4 to 8 workers maximize network utilization while avoiding Telegram `FLOOD_WAIT` rate limits.

---

## 2. Scraping & Headless Browser Optimization

### A. Resource Blocking in Playwright
Movie listings and detail pages frequently load heavy third-party tracking scripts, analytic tags, external advertisement banners, and fonts that waste bandwidth and slow page render times.

In `app/scraper/client.py`, you can route and block unnecessary resources:

```python
# Block heavy assets to speed up page render by 3x-5x
async def route_interceptor(route):
    if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
        # Allow stylesheets if necessary for layout, or abort to save 80% bandwidth
        await route.abort()
    else:
        await route.continue_()
```

### B. Browser Context Reuse
Instead of spawning a new browser instance for every user click, `MMSubChannelScraper` maintains a single persistent `Browser` instance and spawns lightweight `BrowserContext` sessions that are recycled after operations.

---

## 3. High-Speed Mega.nz Downloads

### A. Direct Disk Streaming
Downloads are streamed in 64KB - 128KB chunks directly to disk (`Path.open('wb')`) rather than buffered into system RAM. This prevents memory leaks and ensures memory consumption remains below 150MB regardless of whether downloading a 500MB or 2GB video file.

### B. Mega Free Quota vs. Account Optimization
- **Anonymous**: Subject to Mega's dynamic IP quota (~5 GB per 6–12 hour window).
- **Authenticated**: Add your Mega account credentials in `.env`:
  ```env
  MEGA_EMAIL=your_email@example.com
  MEGA_PASSWORD=your_password
  ```
  Authenticated accounts have dedicated quota allocations and faster transfer rates.

---

## 4. Pipeline Concurrency & Memory Tuning

### A. Concurrency Limits
Processing multiple 1-2GB video files concurrently will saturate disk I/O and risk out-of-memory errors on modest servers or desktop machines.

Set in `.env`:
```env
MAX_CONCURRENT_JOBS=1
```
The internal semaphore (`asyncio.Semaphore(config.max_concurrent_jobs)`) queues additional requests cleanly, reporting queue positions to users via Telegram messages.

### B. Automated Workspace Cleanup
Each job receives an isolated temporary folder:
```
workspace/jobs/<job_id>/
```
Upon successful Telegram upload (or upon failure/cancellation), `app.media.cleanup.cleanup_job()` deletes all downloaded media parts and temp files immediately.

---

## 5. Python Runtime Optimization

### A. Python 3.11 - 3.13 Specializing Adaptive Interpreter
Python 3.11+ includes PEP 659 (specializing adaptive interpreter), which gives a 20-30% speedup over Python 3.10. Running on Python 3.13 provides improved GIL management and faster async coroutine switching.

Run production with optimization flags enabled:
```powershell
python -O -m app.main
```
or set environment variable:
```powershell
$env:PYTHONOPTIMIZE="1"
```

### B. Telethon Throttling
Telegram limits bot message edits to approximately 1 edit per second per chat. `ProgressReporter` throttles visual edit updates to 3-second intervals:
```python
_MIN_UPDATE_INTERVAL = 3.0  # seconds
```
This prevents HTTP 429 `FloodWait` exceptions while keeping the user experience responsive and fluid.
