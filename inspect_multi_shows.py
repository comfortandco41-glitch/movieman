import httpx

res = httpx.get("https://www.homietv.com/api/tv-shows", timeout=20.0).json()
shows = res.get("data", [])[:4]
for s in shows:
    slug = s.get("slug")
    print(f"\nShow: {s.get('title')} ({slug})")
    detail = httpx.get(f"https://www.homietv.com/api/tv-shows/{slug}", timeout=20.0).json().get("data", {})
    seasons = detail.get("seasons", [])
    for season in seasons:
        eps = season.get("episodes", [])
        print(f"  Season {season.get('name')}: {len(eps)} eps")
        if eps:
            first_ep = eps[0]
            print(f"    Ep 1 links: {[(l.get('server_name'), l.get('url')) for l in first_ep.get('tvshow_download_links', [])]}")
            if len(eps) > 1:
                last_ep = eps[-1]
                print(f"    Ep {last_ep.get('episode_number')} links: {[(l.get('server_name'), l.get('url')) for l in last_ep.get('tvshow_download_links', [])]}")
