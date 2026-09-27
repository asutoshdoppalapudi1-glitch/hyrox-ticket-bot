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
import time
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

FIND_MY_RACE_URL = "https://hyrox.com/find-my-race/"
STATE_FILE = Path(__file__).parent / "state.json"

# Full US state names + abbreviations, used to detect USA races from each
# individual event page's address text (the find-my-race listing cards
# don't include country/state info, only a short city code).
US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee",
    "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming",
}
US_MARKERS = {"usa", "united states"} | US_STATE_NAMES

ON_SALE_PHRASES = {"buy tickets", "buy ticket"}
NOT_ON_SALE_PHRASES = {"find out more", "date coming soon"}


def is_usa_page_text(page_text: str) -> bool:
    t = page_text.lower()
    return any(marker in t for marker in US_MARKERS)


def fetch_races():
    resp = requests.get(FIND_MY_RACE_URL, timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (compatible; HyroxTicketWatcher/1.0)"
    })
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    races = []
    for link in soup.find_all("a", href=re.compile(r"/event/")):
        text = link.get_text(strip=True)
        if text.lower() in ON_SALE_PHRASES or text.lower() in NOT_ON_SALE_PHRASES:
            card = link.find_parent(["div", "article", "li"])
            title_tag = card.find(["h2", "h3"]) if card else None
            title = title_tag.get_text(strip=True) if title_tag else None
            url = link.get("href")
            if title and url:
                races.append({
                    "title": title,
                    "url": url,
                    "status": "on_sale" if text.lower() in ON_SALE_PHRASES else "not_on_sale",
                })

    seen = {}
    for r in races:
        seen[r["url"]] = r
    return list(seen.values())


def fetch_is_usa(url: str) -> bool:
    """Fetch a single event's own page and check its address text for a
    USA state/country marker. Only called once per race, then cached."""
    try:
        resp = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (compatible; HyroxTicketWatcher/1.0)"
        })
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Could not fetch {url} to check country: {e}", file=sys.stderr)
        return False
    return is_usa_page_text(resp.text)


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
    print(f"Found {len(races)} total races on the page.")

    state = load_state()
    changed = False
    usa_count = 0

    for race in races:
        key = race["url"]
        prev_entry = state.get(key, {})
        is_new_race = key not in state
        prev_status = prev_entry.get("status")
        cur_status = race["status"]

        # Determine (and cache) whether this is a USA race. Only fetches
        # the individual event page the first time we see this URL.
        if "is_usa" in prev_entry:
            is_usa = prev_entry["is_usa"]
        else:
            is_usa = fetch_is_usa(key)
            time.sleep(0.5)  # be polite to hyrox.com's servers

        if is_usa:
            usa_count += 1

        if is_usa:
            if is_new_race:
                print(f"New USA race detected: {race['title']}")
                status_word = "already on sale" if cur_status == "on_sale" else "not on sale yet"
                send_whatsapp(
                    f"🆕 A new HYROX USA race just appeared: {race['title']} "
                    f"(tickets {status_word}). {key}"
                )
            elif cur_status == "on_sale" and prev_status != "on_sale":
                print(f"Ticket release detected: {race['title']}")
                send_whatsapp(
                    f"🎉 HYROX tickets are now on sale for {race['title']}! "
                    f"Grab yours here: {key}"
                )

        if prev_status != cur_status or is_new_race or "is_usa" not in prev_entry:
            changed = True
        state[key] = {"title": race["title"], "status": cur_status, "is_usa": is_usa}

    print(f"{usa_count} of those are USA races.")

    if changed:
        save_state(state)
        print("State updated.")
    else:
        print("No status changes.")


if __name__ == "__main__":
    main()
