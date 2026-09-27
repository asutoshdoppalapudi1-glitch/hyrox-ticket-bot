# HYROX USA Ticket Watcher → WhatsApp

Checks `hyrox.com/find-my-race/` on a schedule, tracks USA races, and sends
you a WhatsApp message the moment a race's status changes to **"Buy
Tickets"** — covering first ticket releases and any restock after a
sell-out.

## How it works
- `check_tickets.py` scrapes the page, filters to USA cities, and compares
  each race's status to `state.json` (its memory of the last check).
- A GitHub Actions workflow (`.github/workflows/check-tickets.yml`) runs the
  script every 15 minutes for free and commits the updated `state.json` back
  to the repo so it remembers state between runs.
- On a status change to "on sale," it sends a WhatsApp message using the
  official **WhatsApp Cloud API**.

## 1. Set up the WhatsApp Cloud API
1. Go to [developers.facebook.com](https://developers.facebook.com/) → create
   an app → add the **WhatsApp** product.
2. In the WhatsApp → API Setup panel you'll get a **temporary access token**
   and a **Phone Number ID**. For a bot that runs unattended, generate a
   **permanent token** instead (System User + permanent token in Meta
   Business Suite) — the temporary one expires in 24 hours.
3. Under **API Setup → To**, add your own WhatsApp number as a test
   recipient (required while your app is in development mode) and verify it
   with the code WhatsApp sends you.
4. **Important — message templates:** Meta requires any *business-initiated*
   WhatsApp message (i.e., one you didn't send in reply to a message the
   user sent you in the last 24 hours) to use a pre-approved **message
   template**. Go to Meta Business Suite → WhatsApp Manager → Message
   Templates → Create Template, and make one like:
   - Name: `ticket_alert`
   - Category: Utility
   - Body: `🎉 HYROX tickets are now on sale for {{1}}! Grab yours here: {{2}}`

   Submit it — approval is usually within minutes to a few hours. This
   repo's script is already wired to fill in `{{1}}` = race title and
   `{{2}}` = ticket URL.

## 2. Push this folder to a GitHub repo
```bash
cd hyrox-ticket-bot
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<you>/hyrox-ticket-bot.git
git push -u origin main
```

## 3. Add repo secrets
In your GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add:

| Secret | Value |
|---|---|
| `WHATSAPP_TOKEN` | Your permanent (or temporary) access token |
| `WHATSAPP_PHONE_NUMBER_ID` | From the API Setup panel |
| `WHATSAPP_TO` | Your number, digits only with country code, e.g. `15551234567` |
| `WHATSAPP_TEMPLATE_NAME` | e.g. `ticket_alert` |
| `WHATSAPP_TEMPLATE_LANG` | e.g. `en_US` (must match the template's approved language) |

## 4. Test it
Go to the **Actions** tab → "HYROX USA Ticket Watcher" → **Run workflow**
to trigger it manually. Check the run's logs to confirm it found the USA
races and, if any changed status, sent a WhatsApp message.

Once it's green, it will run automatically every 15 minutes.

## Customizing
- **Change which USA races are tracked:** edit `USA_CITY_KEYWORDS` in
  `check_tickets.py`. New races get added to hyrox.com regularly, so check
  back occasionally and add new city names.
- **Change frequency:** edit the `cron` line in the workflow file (careful
  going below ~10 minutes — be a good citizen toward hyrox.com's servers).
- **Track other countries:** swap out `USA_CITY_KEYWORDS` for whatever
  cities you care about.
