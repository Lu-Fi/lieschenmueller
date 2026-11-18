import os

DEFAULT = os.environ.get("GEMINI_MODEL", "gemini-3.8-live")

# Preise in USD je 1 Mio. Tokens, bezahlte Stufe
# (https://ai.google.dev/gemini-api/docs/pricing, Stand 17.10.2026).
_P38 = {"in_text": 0.75, "in_audio": 3.00, "out_text": 4.50, "out_audio": 12.00}

MODELS = [
    {"id": "gemini-3.8-live", "label": "Gemini 3.8 Live", "status": "Stabil", "price": _P38,
     "info": "Standardwahl für Telefon-Assistenten: niedrige Latenz, Sprache-zu-Sprache, "
             "Funktionsaufrufe und Transkript. Antwortet schnell, ohne lange Denkpausen."},
    {"id": "gemini-3.8-live-extended-thinking", "label": "Gemini 3.8 Live – erweitertes Denken",
     "status": "Stabil", "price": _P38,
     "info": "Wie 3.8 Live, denkt aber im Hintergrund mehr nach. Besser bei Aufgaben mit "
             "Logik, Regeln oder Rückfragen; kann etwas langsamer antworten und mehr Tokens verbrauchen."},
    {"id": "gemini-3.1-flash-live-preview", "label": "Gemini 3.1 Flash Live (Vorgänger)",
     "status": "Veraltet (Legacy-Preview)", "price": _P38,
     "info": "Älteres Live-Modell zum Vergleich. Google empfiehlt den Wechsel auf 3.8 Live; "
             "ein Abschaltdatum ist nicht genannt."},
    {"id": "gemini-2.5-flash-native-audio-preview-12-2025", "label": "Gemini 2.5 Flash Native Audio",
     "status": "Preview, nur für Bestandsnutzer",
     "price": {"in_text": 0.50, "in_audio": 3.00, "out_text": 2.00, "out_audio": 12.00},
     "info": "Ältere Familie mit Fokus auf natürliche Sprachausgabe (Betonung, Tempo, Stimmung). "
             "Etwas günstiger bei Text. Laut Google nur für Nutzer, die 2.5-Modelle schon verwendet haben."},
]
BY_ID = {m["id"]: m for m in MODELS}


def resolve(model_id):
    return model_id if model_id in BY_ID else DEFAULT


# Live-Modelle, die bei Vertex AI im Standort eu laufen (geprueft); die anderen gibt es dort nicht
EU_LIVE = ("gemini-3.8-live",)


def live_model(model_id, eu_only=False):
    """Live-Modell des Agenten; bei Vertex fuer die Telefon-KI nur ein Modell, das in der EU laeuft (sonst 3.8 Live)."""
    m = resolve(model_id)
    return "gemini-3.8-live" if eu_only and m not in EU_LIVE else m


def price(model_id):
    return BY_ID.get(resolve(model_id), BY_ID["gemini-3.8-live"])["price"]


# Nachtranskription von Gespraechen (generateContent, kein Live-Modell); Preise USD je 1 Mio. Tokens
# Ausfuehrliche Zusammenfassung (alle besprochenen Punkte mit Ergebnissen): staerkeres Textmodell als die Kurzauswertung.
# Der Preis ist eine Schaetzung (Textpreise der 3.8-Familie); einstellbar mit dem Eintrag detail_model in den Einstellungen (meta).
DETAIL = {"id": "gemini-3.8-flash", "price": {"in_text": 0.75, "in_audio": 0.0, "out_text": 4.50, "out_audio": 0.0}}

# Waehlbare Modelle fuer die ausfuehrliche Zusammenfassung (Einstellungen -> KI); die erste Zeile ist der Standard
DETAIL_CHOICES = [
    ("gemini-3.8-flash", "Gemini 3.8 Flash – ausgewogen (Standard)"),
    ("gemini-3.5-flash", "Gemini 3.5 Flash"),
    ("gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite – günstig, weniger ausführlich"),
]

TRANSCRIBE = {"id": "gemini-3.1-flash-lite",
              "price": {"in_text": 0.25, "in_audio": 0.50, "out_text": 1.50, "out_audio": 0.0}}


# Waehlbare Modelle fuer das Transkript (Mitschrift von Aufnahmen); die erste Zeile ist der Standard. Preise je 1 Mio. Tokens, fuer die neueren Modelle geschaetzt.
TRANSCRIBE_CHOICES = [
    ("gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite – günstig (Standard)", TRANSCRIBE["price"]),
    ("gemini-3.8-flash", "Gemini 3.8 Flash – etwa doppelt so teuer", {"in_text": 0.75, "in_audio": 1.0, "out_text": 4.5, "out_audio": 0.0}),
    ("gemini-3.5-flash", "Gemini 3.5 Flash – ähnlich teuer wie 3.8 Flash", {"in_text": 0.75, "in_audio": 1.0, "out_text": 4.5, "out_audio": 0.0}),
]


def text_price(model_id):
    """Preise eines Textmodells (Transkript/Auswertung); unbekannt = Preise von DETAIL (3.8 Flash)."""
    for i, _, price in TRANSCRIBE_CHOICES:
        if i == model_id:
            return price
    return DETAIL["price"]


def transcribe_model(chosen=""):
    """(Modell-ID, Preise) fuers Transkript; unbekannte/leere Auswahl = Standard."""
    for i, _, price in TRANSCRIBE_CHOICES:
        if i == chosen:
            return i, price
    return TRANSCRIBE["id"], TRANSCRIBE["price"]
