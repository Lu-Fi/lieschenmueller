"""Herkunft einer Rufnummer ohne Internet: Ort (Vorwahl), Art (Festnetz/Mobil/...) und urspruenglicher Mobilfunkanbieter.
Daten der Bibliothek python-phonenumbers (libphonenumber). Mobilnummern haben keinen Ort, und der Anbieter ist nur der
urspruengliche (Rufnummernmitnahme moeglich); einen Namen liefert das nicht."""
import functools

import phonenumbers
from phonenumbers import PhoneNumberType as T
from phonenumbers import carrier, geocoder

KIND = {T.FIXED_LINE: "Festnetz", T.MOBILE: "Mobil", T.FIXED_LINE_OR_MOBILE: "", T.TOLL_FREE: "gebührenfrei", T.PREMIUM_RATE: "Mehrwertdienst",
        T.SHARED_COST: "Servicenummer", T.VOIP: "VoIP", T.PERSONAL_NUMBER: "persönliche Rufnummer", T.UAN: "Unternehmensnummer"}
CARRIER = {"Eplus": "O2", "E-Plus": "O2", "Telefonica": "O2", "Telefónica": "O2"}


@functools.lru_cache(maxsize=2048)
def label(number):
    """z. B. 'Köln', 'Mobil · Vodafone', 'Frankreich · Paris', 'Mehrwertdienst' oder '' (intern, ungueltig, nichts bekannt)."""
    number = (number or "").strip()
    digits = "".join(c for c in number if c.isdigit())
    if len(digits) < 6 or number.startswith("*"):
        return ""
    try:
        n = phonenumbers.parse(number if number.startswith("+") else digits, "DE")
        if not phonenumbers.is_possible_number(n):
            return ""
        kind = KIND.get(phonenumbers.number_type(n), "")
        place = geocoder.description_for_number(n, "de")
        foreign = n.country_code != 49
        country = (geocoder.country_name_for_number(n, "de") or "") if foreign else ""
        if place in ("Deutschland", "Germany") or place == country:
            place = ""
        if kind == "Mobil":
            prov = carrier.name_for_number(n, "de")
            parts = [country, kind, CARRIER.get(prov, prov)]
        elif kind == "Festnetz" or not kind:
            parts = [country, place or ("Festnetz" if kind else "")]
        else:
            parts = [country, place, kind]
        return " · ".join(dict.fromkeys(p for p in parts if p))
    except Exception:
        return ""
