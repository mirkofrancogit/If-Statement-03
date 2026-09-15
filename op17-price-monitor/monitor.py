#!/usr/bin/env python3
"""
Monitora il prezzo di un prodotto (box Eng One Piece OP-17) su una lista di
shop europei configurati in config/sites.yaml e invia un'email quando il
prezzo scende sotto una soglia.

Strategia di estrazione prezzo (in ordine, la prima che funziona vince):
  1. JSON-LD schema.org Product/Offer  (<script type="application/ld+json">)
  2. Meta tag Open Graph "product:price:amount" / "product:price:currency"
  3. Meta/elementi con itemprop="price" / itemprop="priceCurrency" (microdata)
  4. Fallback: regex su simbolo/currency EUR nel testo della pagina

Lo stato (ultimo prezzo visto / ultimo prezzo per cui abbiamo già avvisato)
viene salvato in state.json per evitare di rimandare la stessa email ad ogni
esecuzione.
"""
import json
import os
import re
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "sites.yaml"
STATE_PATH = BASE_DIR / "state.json"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,it;q=0.8,fr;q=0.7",
}
REQUEST_TIMEOUT = 20

EUR_SYMBOLS = {"€", "eur", "EUR"}


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def _normalize_currency(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    if raw in EUR_SYMBOLS:
        return "EUR"
    return raw.upper()


def _parse_amount(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text:
        return None
    # Gestisce "151,00" e "1.234,56" (formato EU) oltre a "151.00"
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _extract_from_jsonld(soup: BeautifulSoup):
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        candidates = data if isinstance(data, list) else [data]
        for node in candidates:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph")
            if isinstance(graph, list):
                candidates.extend(g for g in graph if isinstance(g, dict))

            offers = node.get("offers")
            if offers is None:
                continue
            offer_list = offers if isinstance(offers, list) else [offers]
            for offer in offer_list:
                if not isinstance(offer, dict):
                    continue
                price = _parse_amount(offer.get("price") or offer.get("lowPrice"))
                currency = _normalize_currency(offer.get("priceCurrency", ""))
                if price is not None:
                    return price, currency
    return None, None


def _extract_from_meta(soup: BeautifulSoup):
    amount_meta = soup.find("meta", property="product:price:amount") or soup.find(
        "meta", attrs={"itemprop": "price"}
    )
    currency_meta = soup.find("meta", property="product:price:currency") or soup.find(
        "meta", attrs={"itemprop": "priceCurrency"}
    )
    if amount_meta and amount_meta.get("content"):
        price = _parse_amount(amount_meta["content"])
        currency = _normalize_currency(currency_meta["content"]) if currency_meta and currency_meta.get("content") else ""
        if price is not None:
            return price, currency
    return None, None


def _extract_from_regex(text: str):
    match = re.search(r"(\d{1,4}(?:[.,]\d{2}))\s*€", text)
    if match:
        return _parse_amount(match.group(1)), "EUR"
    match = re.search(r"€\s*(\d{1,4}(?:[.,]\d{2}))", text)
    if match:
        return _parse_amount(match.group(1)), "EUR"
    return None, None


def extract_price(html: str):
    soup = BeautifulSoup(html, "lxml")

    for extractor in (_extract_from_jsonld, _extract_from_meta):
        price, currency = extractor(soup)
        if price is not None:
            return price, currency

    return _extract_from_regex(soup.get_text(" "))


def fetch_price(url: str):
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return extract_price(resp.text)


def send_email_alert(subject: str, body: str) -> None:
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
    alert_to = os.environ.get("ALERT_TO", gmail_user)

    if not gmail_user or not gmail_password:
        print("GMAIL_USER / GMAIL_APP_PASSWORD non configurati: salto invio email.", file=sys.stderr)
        print(f"--- Avrei inviato ---\n{subject}\n{body}\n---------------------")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = alert_to
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(gmail_user, gmail_password)
        smtp.send_message(msg)


def main() -> int:
    config = load_config()
    product = config["product"]
    threshold = float(config["threshold_eur"])
    sites = config["sites"]

    state = load_state()
    had_error = False

    for site in sites:
        name = site["name"]
        url = site["url"]
        key = url

        try:
            price, currency = fetch_price(url)
        except Exception as exc:  # noqa: BLE001 - un sito che fallisce non deve bloccare gli altri
            print(f"[{name}] ERRORE nel controllo prezzo: {exc}", file=sys.stderr)
            had_error = True
            continue

        if price is None:
            print(f"[{name}] Prezzo non trovato nella pagina ({url}).", file=sys.stderr)
            had_error = True
            continue

        if currency and currency != "EUR":
            print(f"[{name}] Prezzo trovato in {currency} ({price}), non EUR: salto confronto soglia.")
            continue

        print(f"[{name}] Prezzo attuale: {price:.2f} EUR (soglia: {threshold:.2f} EUR)")

        site_state = state.get(key, {})
        site_state["last_price"] = price
        site_state["last_checked_name"] = name

        already_alerted_price = site_state.get("last_alert_price")
        should_alert = price <= threshold and (
            already_alerted_price is None or price < already_alerted_price
        )

        if should_alert:
            subject = f"[OP17 Alert] {name}: {price:.2f} EUR (sotto {threshold:.2f} EUR)"
            body = (
                f"{product}\n\n"
                f"Negozio: {name}\n"
                f"Prezzo trovato: {price:.2f} EUR\n"
                f"Soglia impostata: {threshold:.2f} EUR\n\n"
                f"Link: {url}\n"
            )
            try:
                send_email_alert(subject, body)
                site_state["last_alert_price"] = price
                print(f"[{name}] ALERT inviato ({price:.2f} EUR).")
            except Exception as exc:  # noqa: BLE001
                print(f"[{name}] ERRORE invio email: {exc}", file=sys.stderr)
                had_error = True

        state[key] = site_state

    save_state(state)
    if had_error:
        print("Uno o più siti hanno avuto problemi in questo giro (vedi log sopra).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
