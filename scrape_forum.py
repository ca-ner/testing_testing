#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
1) Donanimhaber "2026 Otel Fırsatları" konusunu sayfa sayfa tarar.
   Her mesaj için kullanıcı adı, tarih ve mesaj içeriğini toplar ve
   desifre.json dosyasına yazar.

Kullanım:
    python scrape_forum.py                # tüm sayfaları tarar (1..TOTAL_PAGES)
    python scrape_forum.py --pages 1      # sadece 1. sayfa (test)
    python scrape_forum.py --start 1 --end 10
    python scrape_forum.py --out desifre.json

Cloudflare / anti-bot önlemleri:
    - Önce normal requests.Session ile denenir.
    - 403 / Cloudflare challenge görülürse otomatik olarak cloudscraper'a düşer.
    - Gerçekçi tarayıcı header'ları, rastgele bekleme süreleri ve yeniden
      deneme (retry + exponential backoff) uygulanır.
"""

import argparse
import json
import random
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://forum.donanimhaber.com/2026-f-p-otel-firsatlari--161803716"
TOTAL_PAGES = 571  # konudaki toplam sayfa sayısı

# Birden fazla gerçekçi User-Agent arasında dönüşümlü kullanılır.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Referer": BASE_URL,
}


def page_url(n: int) -> str:
    """1. sayfa kök URL'dir; sonraki sayfalar ...--161803716-<n> biçimindedir."""
    return BASE_URL if n <= 1 else f"{BASE_URL}-{n}"


def build_session(use_cloudscraper: bool = False):
    """Normal session ya da Cloudflare aşan cloudscraper session döndürür."""
    if use_cloudscraper:
        import cloudscraper
        sess = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
    else:
        sess = requests.Session()
    sess.headers.update(BASE_HEADERS)
    sess.headers["User-Agent"] = random.choice(USER_AGENTS)
    return sess


def looks_like_challenge(resp) -> bool:
    """Yanıtın Cloudflare/anti-bot doğrulama sayfası olup olmadığını tahmin eder."""
    if resp.status_code in (403, 429, 503):
        return True
    body = resp.text[:4000].lower()
    markers = ("just a moment", "cf-browser-verification", "cf-challenge",
               "attention required", "checking your browser")
    return any(m in body for m in markers)


def fetch_page(n: int, state: dict, max_retries: int = 4) -> str:
    """Tek bir sayfayı indirir; gerekirse cloudscraper'a düşer ve yeniden dener."""
    last_err = None
    for attempt in range(1, max_retries + 1):
        sess = state["session"]
        try:
            # her denemede header'ı biraz tazele
            sess.headers["User-Agent"] = random.choice(USER_AGENTS)
            resp = sess.get(page_url(n), timeout=40)
            if looks_like_challenge(resp):
                if not state["cloudflare"]:
                    print(f"  [!] Sayfa {n}: anti-bot tespit edildi, "
                          f"cloudscraper'a geçiliyor...")
                    state["session"] = build_session(use_cloudscraper=True)
                    state["cloudflare"] = True
                    continue
                raise requests.HTTPError(f"challenge (HTTP {resp.status_code})")
            resp.raise_for_status()
            return resp.text
        except Exception as e:  # noqa: BLE001 - ağ hatalarını topluca yakala
            last_err = e
            wait = 2 ** attempt + random.uniform(0, 1.5)
            print(f"  [!] Sayfa {n} deneme {attempt}/{max_retries} hata: {e} "
                  f"-> {wait:.1f}s bekleniyor")
            time.sleep(wait)
    raise RuntimeError(f"Sayfa {n} indirilemedi: {last_err}")


def clean_text(node) -> str:
    """Mesaj gövdesini düz metne çevirir, fazla boşlukları sadeleştirir."""
    # <br> ve blok elemanları satır sonuna çevir
    for br in node.find_all("br"):
        br.replace_with("\n")
    text = node.get_text("\n", strip=True)
    # üç ve daha fazla ardışık satır sonunu ikiye indir
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def parse_messages(html: str, page: int) -> list:
    """Bir sayfanın HTML'inden mesaj listesini çıkarır."""
    soup = BeautifulSoup(html, "lxml")
    results = []
    for art in soup.select("article.kl-icerik-satir"):
        user_el = art.select_one(".ki-kullaniciadi")
        msg_el = art.select_one(".msg")
        if not user_el or not msg_el:
            continue  # reklam / sistem satırlarını atla

        username = user_el.get_text(" ", strip=True)

        date_el = art.select_one(".ki-cevaptarihi span")
        date = date_el.get_text(strip=True) if date_el else ""

        message = clean_text(msg_el)
        if not message:
            continue

        results.append({
            "page": page,
            "username": username,
            "date": date,
            "message": message,
        })
    return results


def main():
    ap = argparse.ArgumentParser(description="Donanimhaber otel fırsatları scraper")
    ap.add_argument("--start", type=int, default=1, help="başlangıç sayfası")
    ap.add_argument("--end", type=int, default=TOTAL_PAGES, help="bitiş sayfası")
    ap.add_argument("--pages", type=int, default=None,
                    help="sadece ilk N sayfayı tara (test için kısa yol)")
    ap.add_argument("--out", default="desifre.json", help="çıktı json dosyası")
    ap.add_argument("--min-delay", type=float, default=1.0)
    ap.add_argument("--max-delay", type=float, default=3.0)
    args = ap.parse_args()

    start = args.start
    end = args.pages if args.pages else args.end
    end = min(end, TOTAL_PAGES)

    state = {"session": build_session(use_cloudscraper=False), "cloudflare": False}
    all_messages = []

    print(f"Taranıyor: sayfa {start} -> {end} ({BASE_URL})")
    for n in range(start, end + 1):
        try:
            html = fetch_page(n, state)
            msgs = parse_messages(html, n)
            all_messages.extend(msgs)
            print(f"  sayfa {n:>3}: {len(msgs)} mesaj (toplam {len(all_messages)})")
        except Exception as e:  # noqa: BLE001
            print(f"  [HATA] sayfa {n} atlandı: {e}", file=sys.stderr)
        # kibar tarama: sayfalar arasında rastgele bekle
        if n < end:
            time.sleep(random.uniform(args.min_delay, args.max_delay))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(all_messages, f, ensure_ascii=False, indent=2)

    print(f"\nBitti. {len(all_messages)} mesaj '{args.out}' dosyasına yazıldı.")


if __name__ == "__main__":
    main()
