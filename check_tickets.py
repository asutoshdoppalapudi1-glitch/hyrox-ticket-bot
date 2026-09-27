#!/usr/bin/env python3
"""
HYROX USA Ticket Watcher
-------------------------
Scrapes https://hyrox.com/find-my-race/ , filters it down to USA races,
compares each race's ticket status against the last known status
(stored in state.json), and sends a WhatsApp message via CallMeBot
whenever a race's status changes to "on sale" (covers both first
release and restocks after a sell-out).

Environment variables required (set as GitHub Actions secrets):
    CALLMEBOT_PHONE   Your WhatsApp number, digits only with country code,
                      e.g. 15551234567
    CALLMEBOT_APIKEY  The API key CallMeBot sent you on WhatsApp after
                      you activated it (see README.md)
"""

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

FIND_MY_RACE_URL = "https://hyrox.com/find-my-race/"
STATE_FILE = Path(__file__).parent / "state.json"

# US state/territory abbreviations, used to auto-detect USA races from each
# card's address text (e.g. "Tampa, FL, USA") instead of a hand-maintained
# city list. This means brand-new USA races get picked up automatically,
# with no code edits needed when hyrox.com adds one.
US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
# Matches ", XX" (comma + optional space + two uppercase letters) so it only
# fires on address-style text, not on stray lowercase English words like
# "in" or "or" that would otherwise false-positive-match state codes.
US_STATE_PATTERN = re.compile(
    r",\s*(" + "|".join(sorted(US_STATE_CODES)) + r")\b"
)

ON_SALE_PHRASES = {"buy tickets", "buy ticket"}
NOT_ON_SALE_PHRASES = {"find out more", "date coming soon"}


def is_usa_race(card_text: str) -> bool:
    if "usa" in card_text.lower() or "united states" in card_text.lower():
        return True
    return bool(US_STATE_PATTERN.search(card_text))


def fetch_races():
    resp = requests.get(FIND_MY_RACE_URL, timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (compatible; HyroxTicketWatcher/1.0)"
    })
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    races = []
    # Each event card has an <h2> (or similar) title link to /event/... and
    # a nearby CTA link whose text is either "Buy Tickets" or "Find out more".
    for link in soup.find_all("a", href=re.compile(r"/event/")):
        text = link.get_text(strip=True)
        if text.lower() in ON_SALE_PHRASES or text.lower() in NOT_ON_SALE_PHRASES:
            # Walk up to the enclosing card to find the title + url + address.
            card = link.find_parent(["div", "article", "li"])
            title_tag = card.find(["h2", "h3"]) if card else None
            title = title_tag.get_text(strip=True) if title_tag else None
            url = link.get("href")
            card_text = card.get_text(" ", strip=True) if card else ""
            if title and url:
                races.append({
                    "title": title,
                    "url": url,
                    "status": "on_sale" if text.lower() in ON_SALE_PHRASES else "not_on_sale",
                    "is_usa": is_usa_race(card_text),
                })

    # De-duplicate (site sometimes repeats a card for filter variants)
    seen = {}
    for r in races:
        seen[r["url"]] = r
    return list(seen.values())


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def send_whatsapp(message):
    phone = os.environ["CALLMEBOT_PHONE"]
    apikey = os.environ["CALLMEBOT_APIKEY"]

    api_url = (
        "https://api.callmebot.com/whatsapp.php"
        f"?phone={phone}&text={quote(message)}&apikey={apikey}"
    )
    resp = requests.get(api_url, timeout=30)
    if resp.status_code >= 300:
        print(f"WhatsApp send failed: {resp.status_code} {resp.text}", file=sys.stderr)
    else:
        print("WhatsApp alert sent")


def main():
    races = fetch_races()
    usa_races = [r for r in races if r["is_usa"]]
    print(f"Found {len(races)} total races, {len(usa_races)} in the USA.")

    if not usa_races:
        print("No USA races found on the page — site structure may have changed.")
        return

    state = load_state()
    changed = False

    for race in usa_races:
        key = race["url"]
        is_new_race = key not in state
        prev_status = state.get(key, {}).get("status")
        cur_status = race["status"]

        if is_new_race:
            print(f"New USA race detected: {race['title']}")
            status_word = "already on sale" if cur_status == "on_sale" else "not on sale yet"
            send_whatsapp(
                f"🆕 A new HYROX USA race just appeared: {race['title']} "
                f"(tickets {status_word}). {race['url']}"
            )
        elif cur_status == "on_sale" and prev_status != "on_sale":
            print(f"Ticket release detected: {race['title']}")
            send_whatsapp(
                f"🎉 HYROX tickets are now on sale for {race['title']}! "
                f"Grab yours here: {race['url']}"
            )

        if prev_status != cur_status or is_new_race:
            changed = True
        state[key] = {"title": race["title"], "status": cur_status}

    if changed:
        save_state(state)
        print("State updated.")
    else:
        print("No status changes.")


if __name__ == "__main__":
    main()
