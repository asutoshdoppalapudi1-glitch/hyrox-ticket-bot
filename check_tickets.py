#!/usr/bin/env python3
"""
HYROX USA Ticket Watcher
-------------------------
Scrapes https://hyrox.com/find-my-race/ , filters it down to USA races,
compares each race's ticket status against the last known status
(stored in state.json), and sends a WhatsApp message via the official
WhatsApp Cloud API whenever a race's status changes to "on sale"
(covers both first release and restocks after a sell-out).

Environment variables required (set as GitHub Actions secrets):
    WHATSAPP_TOKEN            Permanent or temporary Meta access token
    WHATSAPP_PHONE_NUMBER_ID  The "Phone number ID" from Meta dev console
    WHATSAPP_TO               Recipient's WhatsApp number, digits only,
                               with country code, e.g. 15551234567
    WHATSAPP_TEMPLATE_NAME    Name of the approved message template to use
                               (see README.md for why a template is needed)
    WHATSAPP_TEMPLATE_LANG    Template language code, e.g. "en_US" (optional,
                               defaults to en_US)
"""

import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

FIND_MY_RACE_URL = "https://hyrox.com/find-my-race/"
STATE_FILE = Path(__file__).parent / "state.json"

# Maintain this list as new USA races get announced on hyrox.com.
# Matching is done case-insensitively against the event title.
USA_CITY_KEYWORDS = [
    "anaheim", "atlanta", "boston", "chicago", "dallas", "denver",
    "houston", "las vegas", "miami beach", "nashville", "new york",
    "phoenix", "portland", "san diego", "tampa",
]

ON_SALE_PHRASES = {"buy tickets", "buy ticket"}
NOT_ON_SALE_PHRASES = {"find out more", "date coming soon"}


def is_usa_race(title: str) -> bool:
    t = title.lower()
    # Exclude Youngstars (kids) races unless you want those tracked too.
    return any(city in t for city in USA_CITY_KEYWORDS)


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
            # Walk up to the enclosing card to find the title + url.
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


def send_whatsapp_template(title, url):
    token = os.environ["WHATSAPP_TOKEN"]
    phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    to = os.environ["WHATSAPP_TO"]
    template_name = os.environ["WHATSAPP_TEMPLATE_NAME"]
    lang = os.environ.get("WHATSAPP_TEMPLATE_LANG", "en_US")

    api_url = f"https://graph.facebook.com/v21.0/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": lang},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": title},
                        {"type": "text", "text": url},
                    ],
                }
            ],
        },
    }
    resp = requests.post(
        api_url,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"WhatsApp send failed for '{title}': {resp.status_code} {resp.text}", file=sys.stderr)
    else:
        print(f"WhatsApp alert sent for '{title}'")


def main():
    races = fetch_races()
    usa_races = [r for r in races if is_usa_race(r["title"])]

    if not usa_races:
        print("No USA races found on the page — site structure may have changed.")
        return

    state = load_state()
    changed = False

    for race in usa_races:
        key = race["url"]
        prev_status = state.get(key, {}).get("status")
        cur_status = race["status"]

        if cur_status == "on_sale" and prev_status != "on_sale":
            print(f"Ticket release detected: {race['title']}")
            send_whatsapp_template(race["title"], race["url"])

        if prev_status != cur_status:
            changed = True
        state[key] = {"title": race["title"], "status": cur_status}

    if changed:
        save_state(state)
        print("State updated.")
    else:
        print("No status changes.")


if __name__ == "__main__":
    main()
