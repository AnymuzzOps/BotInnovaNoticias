import logging
import os
import re
import subprocess
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import requests
from groq import Groq

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


# ── Configuración ─────────────────────────────────────────────────────────────
def _require_env(nombre: str) -> str:
    valor = os.environ.get(nombre)
    if not valor:
        raise RuntimeError(f"Falta variable de entorno requerida: {nombre}")
    return valor


GROQ_API_KEY = _require_env("GROQ_API_KEY")
TELEGRAM_TOKEN = _require_env("TELEGRAM_TOKEN")
TELEGRAM_CHAT_IDS = [
    chat_id for chat_id in [
        os.environ.get("TELEGRAM_CHAT_ID"),
        os.environ.get("TELEGRAM_CHAT_ID_2"),
    ] if chat_id
]

if not TELEGRAM_CHAT_IDS:
    raise RuntimeError("Debes definir al menos TELEGRAM_CHAT_ID para enviar mensajes")

MAX_NOTICIAS_POR_CICLO = 10
MAX_ENTRIES_POR_FEED = 10
GROQ_MODEL = "llama-3.3-70b-versatile"
PROCESADAS_FILE = "procesadas.txt"
TRACKING_QUERY_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "mc_cid", "mc_eid", "igshid",
}

# ── Fuentes RSS ───────────────────────────────────────────────────────────────
FUENTES = [
    "https://techcrunch.com/feed/",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://techcrunch.com/category/startups/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://www.wired.com/feed/rss",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://www.technologyreview.com/feed/",
    "https://venturebeat.com/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://openai.com/blog/rss/",
    "https://huggingface.co/blog/feed.xml",
    "https://www.marktechpost.com/feed/",
    "https://syncedreview.com/feed/",
    "https://spectrum.ieee.org/feeds/feed.rss",
    "https://www.roboticsandautomationnews.com/feed",
    "https://news.crunchbase.com/feed/",
    "https://sifted.eu/feed/",
    "https://feeds.bloomberg.com/technology/news.rss",
    "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "https://www.zdnet.com/news/rss.xml",
    "https://www.cnet.com/rss/news/",
    "https://phys.org/rss-feed/",
    "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml",
    "https://singularityhub.com/feed/",
    "https://futurism.com/feed",
]

# ── Filtros editoriales tech-only ────────────────────────────────────────────
PALABRAS_TECNOLOGIA = {
    "inteligencia artificial", "artificial intelligence", "ia", "ai", "gpt", "llm",
    "machine learning", "deep learning", "neural", "modelo", "copilot", "chatbot",
    "automatización", "automation", "robot", "robótica", "autonomous",
    "software", "saas", "api", "plataforma", "platform", "open source",
    "startup", "funding", "financiamiento", "serie a", "serie b", "seed", "ipo",
    "chip", "semiconductor", "gpu", "nvidia", "procesador", "hardware",
    "cloud", "nube", "data center", "quantum", "computación cuántica",
    "biotech", "biotecnología", "genoma", "crispr", "spacex", "nasa", "satélite",
}

PALABRAS_DESCARTE_TEC = {
    "fútbol", "deportes", "partido", "liga", "campeonato", "gol",
    "política", "elección", "gobierno", "presidente", "parlamento", "congreso",
    "farándula", "celebridad", "reality", "espectáculo",
    "crimen", "asesinato", "homicidio", "asalto", "detenido", "guerra", "atentado",
    "tragedia", "accidente", "incendio", "terremoto", "inundación",
}

PALABRAS_CORTAS = {"ia", "ai", "gpu", "api", "ipo", "llm"}

# ── Clientes compartidos ──────────────────────────────────────────────────────
HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; ElChilometroBot/1.2)"})
GROQ_CLIENT = Groq(api_key=GROQ_API_KEY)


# ── Utilidades ────────────────────────────────────────────────────────────────
def normalizar_link(link: str) -> str:
    """Elimina parámetros de tracking para mejorar deduplicación."""
    parsed = urlparse(link)
    clean_query = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in TRACKING_QUERY_PARAMS
    ]
    clean = parsed._replace(query=urlencode(clean_query), fragment="")
    return urlunparse(clean).strip()


def _nombre_fuente(link: str) -> str:
    host = urlparse(link).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "fuente-desconocida"


def _contiene_keyword(texto: str, keyword: str) -> bool:
    kw = keyword.strip().lower()
    if not kw:
        return False

    # Evita falsos positivos por subcadenas (ej. "ai" en "hair").
    if kw in PALABRAS_CORTAS or len(kw) <= 3:
        patron = rf"\b{re.escape(kw)}\b"
        return re.search(patron, texto) is not None
    return kw in texto


def _coincide_alguna(texto: str, keywords: set[str]) -> bool:
    return any(_contiene_keyword(texto, kw) for kw in keywords)


# ── Telegram ──────────────────────────────────────────────────────────────────
def enviar_telegram(mensaje: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            r = HTTP_SESSION.post(
                url,
                data={"chat_id": chat_id, "text": mensaje},
                timeout=15
            )
            r.raise_for_status()
        except requests.RequestException as e:
            log.error("Error enviando Telegram a %s: %s", chat_id, e)


# ── RSS ───────────────────────────────────────────────────────────────────────
def obtener_noticias() -> list[dict]:
    noticias: list[dict] = []
    vistas: set[str] = set()

    for url in FUENTES:
        try:
            response = HTTP_SESSION.get(url, timeout=10)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except Exception as e:
            log.warning("Feed no disponible (%s): %s", url, e)
            continue

        for entry in feed.entries[:MAX_ENTRIES_POR_FEED]:
            titulo = getattr(entry, "title", "").strip()
            link = normalizar_link(getattr(entry, "link", "").strip())

            if not titulo or not link or link in vistas:
                continue

            vistas.add(link)
            titulo_lower = titulo.lower()

            if _coincide_alguna(titulo_lower, PALABRAS_DESCARTE_TEC):
                continue
            if _coincide_alguna(titulo_lower, PALABRAS_TECNOLOGIA):
                noticias.append({"titulo": titulo, "link": link, "fuente": _nombre_fuente(link)})

    log.info("Noticias candidatas tech: %d", len(noticias))
    return noticias


# ── Groq ──────────────────────────────────────────────────────────────────────
def _llamar_groq(prompt: str, reintentos: int = 3) -> str:
    for intento in range(1, reintentos + 1):
        try:
            respuesta = GROQ_CLIENT.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            return respuesta.choices[0].message.content.strip()
        except Exception as e:
            log.warning("Groq intento %d/%d fallido: %s", intento, reintentos, e)
            if intento < reintentos:
                time.sleep(2 ** intento)
    raise RuntimeError("Groq no respondió tras varios intentos.")


def es_avance_positivo(titulo: str) -> bool:
    prompt = f"""Eres editor de un canal de noticias tecnológicas.
Evalúa el titular y responde si representa una noticia de tecnología NUEVA y RELEVANTE.

Aprueba (SÍ):
- Lanzamientos concretos de IA, software, hardware, chips, automatización o robótica
- Anuncios de producto/capacidad con impacto técnico real
- Financiamiento o hitos de startups tech con dato verificable

Rechaza (NO):
- Opinión, humo, política, marketing sin producto
- Sucesos generales no tecnológicos
- Nota negativa/sensacionalista sin avance técnico

Titular: "{titulo}"

Responde SOLO con SÍ o NO."""
    resultado = _llamar_groq(prompt)
    return resultado.upper().startswith("SÍ")




def traducir_titulo_es(titulo: str) -> str:
    prompt = (
        "Traduce este titular al español neutro (LatAm), manteniendo nombres propios y términos técnicos.\n"
        "Devuelve SOLO el titular traducido, sin comillas ni explicaciones.\n\n"
        f"Titular: {titulo}"
    )
    traducido = _llamar_groq(prompt)
    return traducido.strip() or titulo

def generar_post(noticia: dict) -> str:
    prompt = (
        "Eres un analista de noticias tecnológicas.\n"
        "Escribe un comentario breve (1-2 frases) explicando qué pasó y por qué importa.\n"
        "Sin hashtags, sin tono de marketing, sin inventar datos.\n\n"
        f"Titular: {noticia['titulo']}\n"
        f"Fuente: {noticia['fuente']}\n"
        f"Link: {noticia['link']}\n\n"
        "Responde SOLO con el comentario."
    )
    return _llamar_groq(prompt)


# ── Persistencia ──────────────────────────────────────────────────────────────
def cargar_procesadas() -> set[str]:
    try:
        with open(PROCESADAS_FILE, "r", encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()


def guardar_procesadas(procesadas: set[str]) -> None:
    with open(PROCESADAS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(procesadas)))

    cmds = [
        ["git", "config", "user.email", "bot@elchilometro.cl"],
        ["git", "config", "user.name", "ElChilometro Bot"],
        ["git", "add", PROCESADAS_FILE],
        ["git", "diff", "--cached", "--quiet"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and cmd[:3] != ["git", "diff", "--cached"]:
            log.warning("Git '%s' falló: %s", " ".join(cmd), result.stderr)
            return
        if cmd[:3] == ["git", "diff", "--cached"]:
            if result.returncode == 0:
                log.info("Sin cambios en %s; no se realiza commit.", PROCESADAS_FILE)
                return
            break

    for cmd in (["git", "commit", "-m", "chore: update procesadas"], ["git", "push"]):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.warning("Git '%s' falló: %s", " ".join(cmd), result.stderr)


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("Bot de noticias tecnológicas analizando fuentes.")
    enviar_telegram("🤖 Bot de noticias tecnológicas: analizando fuentes...")

    try:
        noticias = obtener_noticias()
    except Exception as e:
        log.error("Error obteniendo noticias: %s", e)
        enviar_telegram(f"❌ Error obteniendo noticias:\n{e}")
        return

    if not noticias:
        enviar_telegram("⚠️ Sin noticias tecnológicas relevantes en este ciclo.")
        return

    procesadas = cargar_procesadas()
    noticias_nuevas = [n for n in noticias if n["link"] not in procesadas]
    log.info("Noticias nuevas: %d", len(noticias_nuevas))

    if not noticias_nuevas:
        enviar_telegram("⚠️ Sin noticias nuevas, todas ya procesadas.")
        return

    links_procesados: set[str] = set()
    aprobadas = 0

    for noticia in noticias_nuevas:
        if aprobadas >= MAX_NOTICIAS_POR_CICLO:
            break
        try:
            if es_avance_positivo(noticia["titulo"]):
                titulo_es = traducir_titulo_es(noticia["titulo"])
                comentario = generar_post(noticia)
                mensaje = (
                    f"📰 {titulo_es}\n\n"
                    f"Fuente: {noticia['fuente']}\n\n"
                    f"Comentario bot: {comentario}\n\n"
                    f"{noticia['link']}"
                )
                enviar_telegram(mensaje)
                aprobadas += 1
                log.info("Aprobado: %s", noticia["titulo"])
            else:
                log.info("Descartado (sin notificar): %s", noticia["titulo"])
        except Exception as e:
            log.error("Error procesando '%s': %s", noticia["titulo"], e)
            enviar_telegram(f"❌ Error al procesar:\n{noticia['titulo']}\n{e}")
        finally:
            links_procesados.add(noticia["link"])

    if aprobadas == 0:
        enviar_telegram("⚠️ No se encontraron titulares tech suficientemente sólidos en este ciclo.")

    log.info("Aprobadas enviadas: %d", aprobadas)

    guardar_procesadas(procesadas | links_procesados)
    log.info("Ciclo completado. %d procesadas.", len(links_procesados))


if __name__ == "__main__":
    main()
