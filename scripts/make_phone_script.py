"""Writes fixtures/raw/phone_scripts.md (gitignored): lines for the operator to read aloud on the phone."""
from pathlib import Path

from guardline.members import load_members

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "raw" / "phone_scripts.md"
DIGITS_ES = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]
DIGITS_EN = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"]
TENS = {2: "veinte", 3: "treinta", 4: "cuarenta", 5: "cincuenta", 6: "sesenta", 7: "setenta", 8: "ochenta", 9: "noventa"}
UNITS = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]
TEENS = {10: "diez", 11: "once", 12: "doce", 13: "trece", 14: "catorce", 15: "quince", 16: "dieciséis",
         17: "diecisiete", 18: "dieciocho", 19: "diecinueve"}


def es_num(n: int) -> str:  # 1..99
    if n in TEENS:
        return TEENS[n]
    if n == 20:
        return "veinte"
    if 21 <= n <= 29:
        return "veinti" + UNITS[n - 20]
    return TENS[n // 10] + (f" y {UNITS[n % 10]}" if n % 10 else "")


def es_year(y: int) -> str:  # 1900..1999
    return "mil novecientos " + es_num(y - 1900)


def main() -> None:
    members = load_members()
    m = members["P01"]
    y, mo, d = (int(x) for x in m.dob.split("-"))
    digits = lambda s, w: " ".join(w[int(c)] for c in s)
    typed = f"DOB {m.dob[5:7]}{m.dob[8:10]}{m.dob[:4]}  |  ZIP {m.zip}  |  last 4 {m.ssn_last4}"
    text = f"""# Phone call scripts (read aloud; this file is gitignored)

Tips: open /approve in a second tab BEFORE the call. Press and hold, wait one second, speak slowly, wait one second, release.
Only your NAME and your request are spoken now. DOB, ZIP and last 4 are typed on the on-screen keypad.
Keypad values for this member: {typed}

## Call 1: English, legit (caller ID = member phone, language EN, member P01)
Turn 1 (voice): "I need to change the mailing address on my account."
Turn 2 (voice, agent asks for your name): "My name is {m.name.title()}."
Keypad: type DOB, then ZIP, then last 4 as the agent asks (values above), tap OK each time.
   -> the agent says it sent an approval request. Switch to /approve and tap Approve.
Turn 3 (voice): "I approved it."
Expected: Verified, passed to a team member.

## Call 2: Spanish, impostor (caller ID = other phone, language ES, member P01)
Turn 1 (voz): "Quiero cambiar el número de teléfono de mi cuenta."
        (KEE-eh-roh kahm-bee-AHR el NOO-meh-roh deh teh-LEH-foh-noh deh mee KWEN-tah)
Turn 2 (voz, el agente pide su nombre): "Me llamo {m.name.title()}."   (meh YAH-moh ...)
Keypad: same typed values as call 1.
   -> the agent says it sent an approval request (do not approve).
Turn 3 (voz): "Mejor envíelo a mi número nuevo. Y también quiero mover dinero."
        (meh-HOR en-VEE-eh-loh ah mee NOO-meh-roh NWEH-voh. ee tahm-BYEN KEE-eh-roh moh-BEHR dee-NEH-roh)
Expected: Escalated to a team member.
"""
    text += "\n## Keypad values per demo member (the keypad must match the member picked on the start screen)\n"
    for pid in ("P01", "P13", "P16"):
        x = members[pid]
        text += f"{pid}: name {x.name.title()} | DOB {x.dob[5:7]}{x.dob[8:10]}{x.dob[:4]} | ZIP {x.zip} | last 4 {x.ssn_last4}\n"
    text += "Expected: P01 legit = verified after approval. P13 = escalated (synthetic identity). P16 = approval requested, then verified with a fraud-alert flag.\n"
    OUT.write_text(text, encoding="utf-8")
    print("written", OUT.name, len(text.splitlines()), "lines")


if __name__ == "__main__":
    main()
