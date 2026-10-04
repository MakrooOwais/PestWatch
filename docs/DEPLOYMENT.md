# Deploying the PestWatch service

`server.py` is a single stdlib-only process: the field-app API, farmer channel webhooks
(USSD, SMS, voice), outbound alert dispatch, and static hosting for the dashboard and field app.
Live data is stored in SQLite.

```bash
PESTWATCH_ADMIN_KEY=change-me python3 server.py --host 0.0.0.0 --port 8000 --db /var/lib/pestwatch/pestwatch.db
```

Put it behind a TLS-terminating reverse proxy (nginx, Caddy) and point the gateway callbacks at
`https://<host>/gateway/...`. The demo server still freezes a simulated season just before the
first alert and scans live reports on top of it. A real deployment would replace that background
with real reports only.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `PESTWATCH_DB` | `data/pestwatch.db` | SQLite file (`--db` overrides) |
| `PESTWATCH_ADMIN_KEY` | generated | Admin API key. If unset, a random key is generated, printed once, and stored next to the DB in `admin_key` with mode 0600 |
| `PESTWATCH_GATEWAY` | `console` | `console` (log only) or `africastalking` |
| `AT_USERNAME`, `AT_API_KEY` | – | Africa's Talking credentials (`AT_USERNAME=sandbox` uses the sandbox) |
| `AT_SENDER_ID` | – | Optional alphanumeric sender ID / short code for SMS |
| `AT_VOICE_NUMBER` | – | Your AT voice number, required for outbound alert calls |
| `PESTWATCH_PUBLIC_URL` | from `Host` header | Public base URL used in voice XML (`callbackUrl`, recorded prompts) |
| `PESTWATCH_RATE_IP` | `10,60` | Token bucket per client IP for POSTs: `rate_per_second,burst` |
| `PESTWATCH_RATE_FARM` | `1,30` | Token bucket per farm / phone number |

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/farms` | open | farm list for the field app (no phone numbers) |
| POST | `/api/observations` | open, rate-limited | photo results from the field app; triggers dispatch |
| GET | `/api/status?farm=` | open | area status + this farm's alert in its language/channel |
| POST | `/api/inbound` | open, rate-limited | free-text farmer reply, any supported language |
| POST | `/api/profile` | `language`: open; `channel`/`consent`/`subscribed`/`phone`: admin | |
| GET | `/api/i18n` | open | field-app strings |
| GET | `/api/public/risk` | open | grid-level GeoJSON, see Privacy |
| POST | `/gateway/ussd` | gateway | `sessionId, serviceCode, phoneNumber, text` → `CON …` / `END …` |
| POST | `/gateway/sms` | gateway | `from, to, text, id, date` → parsed, reply sent via gateway |
| POST | `/gateway/voice` | gateway | `isActive, sessionId, callerNumber, dtmfDigits` → voice XML |
| POST | `/api/reset` | admin | wipe live data and profile changes |
| POST | `/api/admin/dispatch` | admin | send pending alerts now (also runs after every report/reply) |
| GET | `/api/admin/deliveries` | admin | delivery log |
| GET | `/api/export/geojson` | admin | exact farm locations, profile and risk |
| GET | `/api/export/csv` | admin | live observations |

Admin requests send `X-API-Key: <key>`. The key is compared in constant time.

Gateway webhooks are unauthenticated in this PoC. In production, restrict them to the
gateway's IP ranges at the proxy, or use a secret path segment.

## Africa's Talking setup

1. Create an app (start with the sandbox, `AT_USERNAME=sandbox`).
2. USSD: create a service code and set its callback to `https://<host>/gateway/ussd`.
3. SMS: set the incoming-messages callback to `https://<host>/gateway/sms`.
4. Voice: set the number's callback to `https://<host>/gateway/voice`.
5. Run with `PESTWATCH_GATEWAY=africastalking`, plus `AT_API_KEY` and, for calls, `AT_VOICE_NUMBER`.

**Format notes.**
- Outbound URLs and the `apiKey` header follow AT's official Python SDK:
  - SMS: `api[.sandbox].africastalking.com/version1/messaging`
  - Voice: `voice[.sandbox].africastalking.com/call`
- The webhook field names above, and the voice XML elements (`GetDigits`, `Say`, `Play`), are the commonly documented ones. The AT docs site could not be fetched while building this (it was behind a bot check), so confirm them against the current docs in the sandbox before going live.

**USSD.** The menu runs in the caller's profile language:
- 1 = found leaf rust, 2 = none found, 3 = area status, 4 = change language, 0 = help.
- Every screen stays under the 182-character USSD limit; this is tested in every language.
- Unknown numbers get a polite `END` asking them to register with their extension officer. Self-registration is deliberately not offered, because linking a phone to a farm should be verified.

**Voice.**
- Alert calls read the TTS script, or play recorded prompts from `web/audio/<lang>/<key>.mp3` when present (see `web/audio/README.md`).
- Keypad 1/2 records an inspection result, and 9 repeats the message.
- AT's TTS voices may not cover every language, which is what recorded prompts are for.

**Dispatch.**
- Each farm gets at most one alert per risk level, and only escalations re-alert.
- Unsubscribed farmers (reply STOP) get nothing.
- Every delivery is logged with channel, language, encoding, segment count, gateway message ID and status.
- App-channel alerts are stored for the field app to fetch.
- Gateway failures are logged and retried on the next dispatch.

## Privacy

- **Coarse locations only.** Shared outputs (`/api/public/risk`) never include farm IDs, phone numbers or exact coordinates. Points are snapped to the centre of a ~1 km grid cell.
- **Small cells are suppressed.** Cells with fewer than 3 consenting farms are not published.
- **Consent.** Farmers with `consent=false` are excluded from shared datasets, but still receive alerts.
- **Phone numbers.** They are never returned by farmer-facing endpoints, only stored server-side.
- **Exact data.** Exact farm data is available only through the admin export endpoints.

## Languages

Catalogs live in `pestwatch/i18n/catalogs/<code>.json`. To start a new language:

```bash
python3 scripts/new_language.py luy "Luhya" "Oluluhya"
```

This writes a skeleton with English values, empty intent keyword lists and
`meta.review_status = "untranslated"`. **Untranslated catalogs are never served:** requests for
that language fall back to English, and the language doesn't appear in the field app or USSD menus.

When it's ready:
1. Have native speakers and an extension officer translate and review it.
2. Fill in the reply keywords farmers actually use.
3. Set `review_status` to `"draft"` (pilot) or `"reviewed"`.
4. Run `python3 -m unittest discover -s tests`. The tests enforce identical keys and placeholders, one-segment SMS, and USSD screen limits.

## Operations

- **Backups.** Copy the SQLite file, or use `sqlite3 pestwatch.db ".backup out.db"`. It runs in WAL mode.
- **Logs.** Requests to `/api/*` and `/gateway/*` are logged to stderr; error details go to stderr and never to clients.
- **Response headers.** Responses carry `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. API responses also carry `Content-Security-Policy: default-src 'none'` and `Cache-Control: no-store`. No CORS headers are sent, so browsers allow same-origin use only.
- **Request limits.** Bodies are capped at 256 KB and free-text fields at 480 characters.
