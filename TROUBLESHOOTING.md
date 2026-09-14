# Troubleshooting & FAQ Guide 🛠️

This document addresses common questions, error messages, and operational troubleshooting steps for **Movie Man**.

---

## 1. Resolved: `AttributeError: module 'asyncio' has no attribute 'coroutine'`

### Cause:
Python 3.11 deprecated and Python 3.13 completely removed `@asyncio.coroutine`. The `mega.py` dependency pinned `tenacity 5.1.5` which referenced this legacy decorator in `tenacity._asyncio`.

### Solution:
The built-in compatibility layer in [`app/utils/compat.py`](file:///d:/Tools/Movie%20Man/app/utils/compat.py) automatically injects `types.coroutine` as a fallback at runtime before any third-party libraries load:
```python
if not hasattr(asyncio, "coroutine"):
    asyncio.coroutine = types.coroutine
```
This is fully automated and requires no manual patch.

---

## 2. Telethon / MTProto Issues

### `ChatWriteForbiddenError` or `ChannelPrivateError`
* **Cause**: The bot or user session does not have permission to post messages into the target channel.
* **Solution**:
  1. Open your target Telegram Channel (`movebyeme` / `-1004385468716`).
  2. Go to **Channel Settings** > **Administrators** > **Add Administrator**.
  3. Search for your bot (`@movsend_bot`) and grant **Post Messages** permission.
  4. Also ensure your Telethon user account (associated with `telethon.session`) is a member or admin of the channel.

### `SessionPasswordNeededError`
* **Cause**: Two-step verification is enabled on your Telegram account and the session expired.
* **Solution**: Run `python login_telethon.py` in the terminal to re-authenticate with your 2FA password.

---

## 3. Playwright Browser Issues

### `Executable doesn't exist at ...`
* **Cause**: Playwright's Chromium browser binary is not downloaded.
* **Solution**: Run:
  ```powershell
  playwright install chromium
  ```

### High CPU or Memory Usage
* **Cause**: Background browser instances not terminating.
* **Solution**:
  Ensure `BROWSER_HEADLESS=true` in `.env`. The bot automatically cleans up browser contexts after scraping completes.

---

## 4. Mega Download Issues

### `Bandwidth limit exceeded` / `Download timeout`
* **Cause**: Mega free IP quota (approx. 5GB per day) exhausted.
* **Solution**:
  Configure a registered Mega account in `.env`:
  ```env
  MEGA_EMAIL=your_email@domain.com
  MEGA_PASSWORD=your_password
  ```

---

## 5. Checking Service Health

To verify all components in one quick command:
```powershell
python -c "
import asyncio
from app.config import load_config
from app.media.telethon_uploader import TelethonUploader

c = load_config()
u = TelethonUploader(c.telegram_api_id, c.telegram_api_hash, c.telethon_session)
print('Config:', c.safe_repr())
print('Telethon Auth:', asyncio.run(u.is_authorized()))
"
```
