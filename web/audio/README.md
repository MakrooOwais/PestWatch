# Recorded voice prompts

Voice calls use text-to-speech by default. Where TTS is poor or unavailable for a
language, drop recordings by native speakers here and the voice handler
(`pestwatch/service/voice.py`) plays them instead:

```
web/audio/<lang>/<key>.mp3
```

| key | when it plays |
|---|---|
| `alert_HIGH`, `alert_MEDIUM`, `alert_LOW` | alert call for that risk tier (fixed wording; counts are not spoken) |
| `no_alert` | caller has no current alert |
| `menu` | "press 1 / 2 / 9" menu |
| `reply_PEST_FOUND`, `reply_NO_PEST` | after the caller presses 1 or 2 |
| `goodbye` | end of call |
| `unregistered` | unknown caller number (played in `en`) |

A file overrides TTS only for its own key and language; anything missing falls
back to `<Say>`. Files are served as static assets at `/audio/<lang>/<key>.mp3`,
so set `PESTWATCH_PUBLIC_URL` to the server's public base URL for the gateway to fetch them.
