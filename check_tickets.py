#!/usr/bin/env python3
"""
HYROX USA Ticket Watcher
-------------------------
Scrapes https://hyrox.com/find-my-race/ , works out which races are in the
USA (by reading each event page's "Event Location" address, cached forever
in state.json), and sends a WhatsApp message via CallMeBot when:
  * a brand-new USA race appears on the site, or
  * a USA race's tickets go on sale (first release or a restock).

Environment variables required (set as GitHub Actions secrets):
    CALLMEBOT_PHONE   Your WhatsApp number, digits only with country code
    CALLMEBOT_APIKEY  The API key CallMeBot sent you on WhatsApp
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup

FIND_MY_RACE_URL = "https://hyrox.com/find-my-race/"
STATE_FILE = Path(__file__).parent / "state.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HyroxTicketWatcher/1.0)"}

# If a single run wants to send more than this many alerts, send ONE summary
# message instead. Protects you from a flood if anything ever goes wrong.
MAX_INDIVIDUAL_ALERTS = 3

ON_SALE_PHRASES = {"buy tickets", "buy ticket"}
NOT_ON_SALE_PHRASES = {"find out more", "date coming soon"}

US_STATE_NAMES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
]
US_STATE_CODES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
]
# "Dallas, Texas 75202"  or  "Atlanta, GA 30313"  (state + 5-digit ZIP)
US_ADDRESS_RE = re.compile(
    r"(?:,\s*(?:" + "|".join(US_STATE_CODES) + r")\s+\d{5}\b)"
    r"|(?:\b(?:" + "|".join(US_STATE_NAMES) + r")\s+\d{5}\b)",
)
# The ticket shop HYROX uses for US races.
US_TICKET_HOSTS = {"usa.hyrox.com"}


def fetch_races():
    resp = requests.get(FIND_MY_RACE_URL, timeout=30, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    races = []
    for link in soup.find_all("a", href=re.compile(r"/event/")):
        text = link.get_text(strip=True).lower()
        if text in ON_SALE_PHRASES or text in NOT_ON_SALE_PHRASES:
            card = link.find_parent(["div", "article", "li"])
            title_tag = card.find(["h2", "h3"]) if card else None
            title = title_tag.get_text(strip=True) if title_tag else None
            url = link.get("href")
            if title and url:
                races.append({
                    "title": title,
                    "url": url,
                    "status": "on_sale" if text in ON_SALE_PHRASES else "not_on_sale",
                })

    seen = {}
    for r in races:
        seen[r["url"]] = r
    return list(seen.values())


def classify_usa(html: str) -> bool:
    """True if this event page is for a race in the USA.

    Looks ONLY at the page's 'Event Location:' line and at ticket-shop
    links -- never at the site-wide menu/footer, which mentions the United
    States on every single page."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    m = re.search(r"Event Location:\s*([^\n|]+)", text)
    location = m.group(1) if m else ""
    if location and (US_ADDRESS_RE.search(location)
                     or "united states" in location.lower()
                     or re.search(r"\bUSA\b", location)):
        return True

    for a in soup.find_all("a", href=True):
        if urlparse(a["href"]).netloc.lower() in US_TICKET_HOSTS:
            return True
    return False


def fetch_is_usa(url: str):
    """Returns True/False, or None if the page couldn't be fetched (in which
    case we simply try again next run instead of caching a wrong answer)."""
    try:
        resp = requests.get(url, timeout=30, headers=HEADERS)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Could not fetch {url}: {e}", file=sys.stderr)
        return None
    return classify_usa(resp.text)


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
    try:
        resp = requests.get(api_url, timeout=30)
    except requests.RequestException as e:
        print(f"WhatsApp send failed: {e}", file=sys.stderr)
        return
    if resp.status_code >= 300:
        print(f"WhatsApp send failed: {resp.status_code} {resp.text}", file=sys.stderr)
    else:
        print("WhatsApp alert sent")


def main():
    races = fetch_races()
    print(f"Found {len(races)} total races on the page.")

    state = load_state()
    first_ever_run = not state
    changed = False
    usa_count = 0
    alerts = []  # collected, then sent with a flood guard

    for race in races:
        key = race["url"]
        prev = state.get(key)
        is_new_race = prev is None
        prev_status = prev.get("status") if prev else None
        cur_status = race["status"]

        # "usa" is only trusted if it was computed by this version of the
        # script; otherwise (missing) we work it out from the event page.
        if prev is not None and "usa" in prev:
            is_usa = prev["usa"]
        else:
            is_usa = fetch_is_usa(key)
            time.sleep(0.5)  # be polite to hyrox.com
            if is_usa is None:
                continue  # couldn't check; retry next run, keep old state

        if is_usa:
            usa_count += 1
            if is_new_race and not first_ever_run:
                word = "already on sale" if cur_status == "on_sale" else "not on sale yet"
                alerts.append(
                    f"🆕 New HYROX USA race added: {race['title']} "
                    f"(tickets {word}). {key}"
                )
            elif not is_new_race and cur_status == "on_sale" and prev_status != "on_sale":
                alerts.append(
                    f"🎉 HYROX tickets are now on sale for {race['title']}! "
                    f"Grab yours here: {key}"
                )

        new_entry = {"title": race["title"], "status": cur_status, "usa": is_usa}
        if prev != new_entry:
            changed = True
        state[key] = new_entry

    print(f"{usa_count} of those are USA races.")

    if len(alerts) > MAX_INDIVIDUAL_ALERTS:
        print(f"{len(alerts)} alerts queued - sending one summary instead.")
        send_whatsapp(
            f"HYROX bot: {len(alerts)} USA updates at once. First few:\n"
            + "\n".join(a.split(" http")[0] for a in alerts[:5])
            + "\nCheck https://hyrox.com/find-my-race/"
        )
    else:
        for a in alerts:
            print(a.split(" http")[0])
            send_whatsapp(a)

    if changed:
        save_state(state)
        print("State updated.")
    else:
        print("No changes.")


if __name__ == "__main__":
    main()
