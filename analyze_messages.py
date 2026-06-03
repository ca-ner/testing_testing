#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2) desifre.json içindeki her mesajı, lokal Ollama üzerinde çalışan Qwen
   modeline (varsayılan: qwen2.5:14b) gönderir ve şu bilgileri çıkarır:
       1) bahsedilen otel(ler)
       2) (varsa) fiyat
       3) yorumun kısa özeti
   Sonuçları yorum.json dosyasına yazar.

Ön koşul:
    - Ollama kurulu ve çalışıyor olmalı (varsayılan adres: http://localhost:11434).
    - Model indirilmiş olmalı:  ollama pull qwen2.5:14b
    - Sunucuyu başlatmak için (gerekirse):  ollama serve

Kullanım:
    python analyze_messages.py
    python analyze_messages.py --limit 5            # ilk 5 mesajla test
    python analyze_messages.py --model qwen2.5:14b
    python analyze_messages.py --base-url http://localhost:11434
"""

import argparse
import json
import re
import sys
import time

import requests

SYSTEM_PROMPT = (
    "Sen bir tatil forumu yorumlarını analiz eden bir asistansın. "
    "Sana bir forum mesajı verilecek. Mesajdan SADECE şu bilgileri çıkar ve "
    "yalnızca geçerli JSON döndür:\n"
    '{\n'
    '  "otel": "mesajda bahsedilen otel adı/adları, yoksa null",\n'
    '  "fiyat": "mesajda geçen fiyat bilgisi (ör. \'gecelik 70 bin TL\'), yoksa null",\n'
    '  "ozet": "yorumun tek cümlelik Türkçe özeti"\n'
    '}\n'
    "Kurallar: Sadece mesajda açıkça yazan bilgiyi kullan, uydurma. "
    "Otel adı yoksa otel için null yaz. Fiyat yoksa fiyat için null yaz. "
    "Çıktın yalnızca JSON olsun, başka açıklama ekleme."
)

USER_TEMPLATE = "Forum mesajı:\n\"\"\"\n{message}\n\"\"\""


def call_llm(base_url: str, model: str, message: str,
             temperature: float = 0.1, timeout: int = 180) -> dict:
    """Lokal Ollama'nın /api/chat ucuna istek atar (JSON modu açık)."""
    url = base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(message=message)},
        ],
        "stream": False,
        # Ollama'nın yapısal çıktı modu: yanıtı geçerli JSON'a zorlar.
        "format": "json",
        "options": {
            "temperature": temperature,
            "num_predict": 400,
        },
    }
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    content = data["message"]["content"]
    return parse_json_block(content)


def parse_json_block(text: str) -> dict:
    """Model çıktısından JSON nesnesini güvenli biçimde ayıklar."""
    text = text.strip()
    # ```json ... ``` bloklarını temizle
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # metin içindeki ilk { ... } bloğunu yakalamayı dene
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    # ayrıştırılamazsa ham metni özete koy
    return {"otel": None, "fiyat": None, "ozet": text[:300]}


def norm(v):
    """'null', 'yok', boş gibi değerleri None'a indirger."""
    if v is None:
        return None
    s = str(v).strip()
    if s.lower() in ("", "null", "none", "yok", "belirtilmemiş", "n/a"):
        return None
    return s


def main():
    ap = argparse.ArgumentParser(description="Ollama Qwen ile mesaj analizi")
    ap.add_argument("--in", dest="infile", default="desifre.json")
    ap.add_argument("--out", default="yorum.json")
    ap.add_argument("--base-url", default="http://localhost:11434",
                    help="Ollama API adresi")
    ap.add_argument("--model", default="qwen2.5:14b",
                    help="Ollama'da yüklü model adı (ör. qwen2.5:14b)")
    ap.add_argument("--limit", type=int, default=None,
                    help="sadece ilk N mesajı işle (test için)")
    ap.add_argument("--temperature", type=float, default=0.1)
    args = ap.parse_args()

    try:
        with open(args.infile, encoding="utf-8") as f:
            messages = json.load(f)
    except FileNotFoundError:
        print(f"[HATA] '{args.infile}' bulunamadı. Önce scrape_forum.py çalıştırın.",
              file=sys.stderr)
        sys.exit(1)

    if args.limit:
        messages = messages[: args.limit]

    # Her mesaja kalıcı bir kimlik ver: scrape_forum.py 'id' atar; eski
    # dosyalarda yoksa 'message_id' ya da içerikten türetilen bir anahtarı kullan.
    def key_of(rec):
        if rec.get("id") is not None:
            return f"id:{rec['id']}"
        if rec.get("message_id") is not None:
            return f"mid:{rec['message_id']}"
        return "h:" + str(hash(rec.get("message", "")))

    # Yarım kalmış analizi sürdür: var olan yorum.json'daki işlenmiş kayıtları
    # koru, aynı mesajı bir daha modele gönderme.
    results = []
    done = set()
    try:
        with open(args.out, encoding="utf-8") as f:
            results = json.load(f)
        done = {key_of(r) for r in results}
        if done:
            print(f"Mevcut '{args.out}' bulundu: {len(done)} kayıt zaten "
                  f"analiz edilmiş, atlanacak.")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    todo = [m for m in messages if key_of(m) not in done]
    print(f"{len(todo)} yeni mesaj analiz edilecek "
          f"({len(messages) - len(todo)} atlandı). "
          f"Model: {args.model} @ {args.base_url}")

    errors = 0
    skipped_no_hotel = 0
    for i, m in enumerate(todo, 1):
        text = m.get("message", "")
        try:
            analysis = call_llm(args.base_url, args.model, text,
                                temperature=args.temperature)
        except Exception as e:  # noqa: BLE001
            # Başarısız analizi KAYDETME: 'done' sayılmasın ki sonraki
            # çalıştırmada bu mesaj yeniden denensin.
            errors += 1
            print(f"  [HATA] id={m.get('id')} analiz edilemedi (tekrar "
                  f"denenecek): {e}", file=sys.stderr)
            continue

        otel = norm(analysis.get("otel"))
        if otel is None:
            # Otel adı geçmiyor / tanınmadı: bu kayıt hiç oluşturulmaz.
            skipped_no_hotel += 1
            print(f"  [{i}/{len(todo)}] id={m.get('id')} otel yok -> atlandı")
            continue

        results.append({
            "id": m.get("id"),
            "message_id": m.get("message_id"),
            "username": m.get("username"),
            "date": m.get("date"),
            "page": m.get("page"),
            "otel": otel,
            "fiyat": norm(analysis.get("fiyat")),
            "ozet": norm(analysis.get("ozet")),
            "mesaj": text,
        })
        print(f"  [{i}/{len(todo)}] id={m.get('id')} "
              f"otel={results[-1]['otel']!r} fiyat={results[-1]['fiyat']!r}")

        # her 10 mesajda bir ara kayıt (uzun çalışmalarda veri kaybını önler)
        if i % 10 == 0:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    msg = (f"\nBitti. Otel adı geçen {len(results)} kayıt '{args.out}' "
           f"dosyasında.")
    if skipped_no_hotel:
        msg += f" {skipped_no_hotel} mesaj otel içermediği için yazılmadı."
    if errors:
        msg += (f" {errors} mesaj hata aldı, kaydedilmedi; scripti tekrar "
                f"çalıştırınca otomatik denenecek.")
    print(msg)


if __name__ == "__main__":
    main()
