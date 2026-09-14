import httpx
res = httpx.get("https://www.homietv.com/api/tv-shows/love-on-the-menu-ykiuckme", timeout=20.0).json()["data"]
for s in res.get("seasons", []):
    for ep in s.get("episodes", [])[:3]:
        ep_num = ep.get("episode_number")
        print(f"=== Episode {ep_num} ===")
        for l in ep.get("tvshow_download_links", []):
            print(f"  {l.get('server_name')}: {l.get('url')} ({l.get('quality')} {l.get('size')})")
