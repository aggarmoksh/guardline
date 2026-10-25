"""Create or update the Cartesia Line relay: one webhook tool, an English and a Spanish agent, and their numbers.

Usage: python -m uv run python scripts/line_setup.py https://<tunnel>.trycloudflare.com

Idempotent. Keeps ids in D:/tmp/line_ids.json (outside the repo). Max 2 Cartesia numbers. Prints business numbers and ids only,
never keys or caller numbers. The Line agent is a relay: it sends each caller utterance to POST /api/line/turn and speaks
the `say` text; the caller id and call id come from system variables and are never visible to the Line model.
"""
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IDS = Path(os.environ.get("LINE_IDS_FILE", "D:/tmp/line_ids.json"))
API = "https://api.cartesia.ai"
VOICES = {"en": "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4", "es": "b4b8e2af-6139-466e-a93a-30c20d2e1fc5"}  # Skylar, Fernanda
MODEL = "claude-haiku-5.5"

INSTRUCTIONS = {
    "en": (
        "You are a warm, helpful phone representative at a credit union. Speak naturally, like a friendly person, in short sentences.\n"
        "For every message from the caller, call the tool guardline_turn with utterance set to the caller's exact words. "
        "The tool returns JSON with intent, must_ask, state, say_fallback, end_call, continue and sometimes next.\n"
        "Then reply out loud: briefly acknowledge what the caller just said, convey the intent in your own words, and ask must_ask "
        "(keep its meaning exactly; when must_ask is digits being read back, say those digits exactly as written).\n"
        "Never state or imply any verification, approval, outcome, account detail or action that is not in the intent. "
        "Never invent account information. Never say the caller's name or any other name, and never repeat their personal details unless must_ask is a read-back. Never say that anything is confirmed, verified or approved about the caller. Do not explain policies or say what you can or cannot do.\n"
        "For small talk, answer in one short friendly sentence and then return to must_ask. If the caller asks a question you cannot answer from the intent, answer neutrally in one short sentence that only says what you need from them, then ask must_ask.\n"
        "If you are unsure how to word it, say say_fallback exactly.\n"
        "ALWAYS speak your reply BEFORE anything else, even when continue is true. If continue is true, only after you have finished "
        "speaking, call guardline_turn again with an empty utterance and respond to its result the same way. Follow the next field if there is one.\n"
        "If end_call is true, speak your reply and then end the call.\n"
        "If the tool fails or returns an error, say exactly: I am connecting you to a team member. Then end the call."),
    "es": (
        "Eres un representante telefónico amable y servicial de una cooperativa de crédito. Habla con naturalidad, como una persona cercana, con frases cortas.\n"
        "Por cada mensaje de la persona que llama, llama a la herramienta guardline_turn con utterance igual a sus palabras exactas. "
        "La herramienta devuelve JSON con intent, must_ask, state, say_fallback, end_call, continue y a veces next.\n"
        "Luego responde en voz alta: reconoce brevemente lo que la persona acaba de decir, transmite el intent con tus propias palabras y haz la pregunta must_ask "
        "(mantén su significado exacto; si must_ask son dígitos que se repiten, di esos dígitos exactamente como están escritos).\n"
        "Nunca afirmes ni insinúes ninguna verificación, aprobación, resultado, dato de cuenta o acción que no esté en el intent. "
        "Nunca inventes información de cuenta. Nunca digas el nombre de la persona ni ningún otro nombre, y nunca repitas sus datos personales salvo que must_ask sea una repetición de dígitos. Nunca digas que algo está confirmado, verificado o aprobado sobre la persona. No expliques políticas ni lo que puedes o no puedes hacer.\n"
        "Para conversación casual, responde en una frase corta y amable y vuelve a must_ask. Si la persona hace una pregunta que no puedes responder con el intent, responde de forma neutral en una frase corta que solo diga lo que necesitas de ella, y haz la pregunta must_ask.\n"
        "Si no sabes cómo expresarlo, di say_fallback exactamente.\n"
        "SIEMPRE di tu respuesta ANTES de hacer cualquier otra cosa, incluso cuando continue es true. Si continue es true, solo después de terminar de hablar, "
        "llama otra vez a guardline_turn con utterance vacío y responde a su resultado del mismo modo. Sigue el campo next si existe.\n"
        "Si end_call es true, di tu respuesta y luego termina la llamada.\n"
        "Si la herramienta falla o devuelve un error, di exactamente: Le estoy conectando con un miembro del equipo. Luego termina la llamada."),
}

GREETING = "Hi, thank you for calling Bay Area Community Credit Union. How can I help you today?"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: line_setup.py https://<tunnel-host>")
    base = sys.argv[1].rstrip("/")
    env = dotenv_values(ROOT / ".env")
    key, token = env["CARTESIA_API_KEY"], env["LINE_PROBE_TOKEN"]
    h = {"Authorization": f"Bearer {key}", "X-API-Key": key, "Cartesia-Version": "2026-08-14", "Content-Type": "application/json"}
    ids = json.loads(IDS.read_text()) if IDS.exists() else {}
    c = httpx.Client(headers=h, timeout=60)

    def check(r, what):
        if r.status_code >= 400:
            sys.exit(f"{what} failed: HTTP {r.status_code} {r.text[:300]}")
        return r.json()

    tool = {
        "type": "webhook", "name": "guardline_turn", "pre_tool_speech": "off", "execution_mode": "immediate",
        "response_timeout_secs": 90,
        "description": "Send what the caller just said to GuardLine and get back exactly what to say next. Call it for every caller message.",
        "api_schema": {"url": base + "/api/line/turn", "method": "POST", "request_body_schema": {
            "type": "object", "required": ["utterance"], "properties": {
                "utterance": {"type": "string", "description": "The caller's exact words from their latest turn, verbatim. Empty string when continuing."},
                "conversation_id": {"type": "string", "dynamic_variable": "system__conversation_id"},
                "caller_id": {"type": "string", "dynamic_variable": "system__caller_id"},
                "called_number": {"type": "string", "dynamic_variable": "system__called_number"},
                "line_token": {"type": "string", "constant_value": token}}}}}
    tool_id = ids.get("relay_tool")
    if tool_id:
        r = c.patch(f"{API}/v1/agents/tools/{tool_id}", json=tool)
        if r.status_code >= 400:
            print(f"tool update HTTP {r.status_code}; creating a new tool")
            tool_id = None
    if not tool_id:
        tool_id = check(c.post(f"{API}/v1/agents/tools", json=tool), "tool create")["id"]
    ids["relay_tool"] = tool_id

    from guardline import speaker
    from guardline.llm import LLM
    llm = LLM()
    for lang in ("en", "es"):
        config = {
            "instructions": INSTRUCTIONS[lang],
            "initial_message": speaker.translate(llm, GREETING, lang),  # the only fixed opening line
            "language": {"primary": lang}, "model": {"id": MODEL, "max_output_tokens": 300},
            "audio": {"output": {"voice_id": VOICES[lang]}},
            "tools": [{"id": tool_id}], "system_tools": {"end_call": {"description": None, "pre_tool_speech": "off"}},
            "turn": {"inactivity_end_call_secs": 90, "inactivity_check_in_secs": None}, "timezone": "America/Los_Angeles"}
        agent_id = ids.get(f"agent_{lang}") or (ids.get("agent") if lang == "en" else None)
        if agent_id:
            check(c.patch(f"{API}/v1/agents/{agent_id}", json={"name": f"GuardLine relay ({lang})", "config": config}), f"agent {lang} update")
        else:
            agent_id = check(c.post(f"{API}/v1/agents", json={"name": f"GuardLine relay ({lang})", "config": config}), f"agent {lang} create")["id"]
        ids[f"agent_{lang}"] = agent_id

    numbers = check(c.get(f"{API}/agents/phone-numbers"), "list numbers")["data"]
    by_label = {n["label"]: n for n in numbers}
    en_number = by_label.get("guardline-probe") or (numbers[0] if numbers else None)
    if not en_number:
        en_number = check(c.post(f"{API}/agents/phone-numbers/provision", json={"label": "guardline-en"}), "provision en")
    es_number = by_label.get("guardline-es")
    if not es_number:
        if len(numbers) >= 2:
            sys.exit("two numbers already exist; refusing to provision a third")
        es_number = check(c.post(f"{API}/agents/phone-numbers/provision", json={"label": "guardline-es"}), "provision es")
    for n, lang in ((en_number, "en"), (es_number, "es")):
        check(c.patch(f"{API}/agents/phone-numbers/{n['id']}", json={"agent_id": ids[f"agent_{lang}"]}), f"assign {lang}")
    ids["number_en"], ids["number_es"] = en_number, es_number
    IDS.write_text(json.dumps(ids))

    env_path = ROOT / ".env"
    lines = [ln for ln in env_path.read_text(encoding="utf-8").splitlines() if not ln.startswith("LINE_SPANISH_NUMBER=")]
    lines.append("LINE_SPANISH_NUMBER=" + "".join(ch for ch in es_number["number"] if ch.isdigit()))
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("tool", tool_id, "| English agent", ids["agent_en"], "| Spanish agent", ids["agent_es"])
    print("English number:", en_number["number"], "| Spanish number:", es_number["number"])


if __name__ == "__main__":
    main()
