import os
import time
import logging
import feedparser
import requests
import subprocess
from groq import Groq

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ── Configuración ─────────────────────────────────────────────────────────────
GROQ_API_KEY      = os.environ["GROQ_API_KEY"]
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_IDS = [
    chat_id for chat_id in [
        os.environ.get("TELEGRAM_CHAT_ID"),
        os.environ.get("TELEGRAM_CHAT_ID_2"),
    ] if chat_id
]

MAX_NOTICIAS_POR_CICLO = 5
MAX_ENTRIES_POR_FEED   = 10
GROQ_MODEL             = "llama-3.3-70b-versatile"
PROCESADAS_FILE        = "procesadas.txt"

# ── Fuentes RSS ───────────────────────────────────────────────────────────────
FUENTES = [
    # Tecnología e IA — tier 1
    "https://techcrunch.com/feed/",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://techcrunch.com/category/startups/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://www.wired.com/feed/rss",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://www.technologyreview.com/feed/",
    "https://venturebeat.com/feed/",
    "https://venturebeat.com/category/ai/feed/",
    # IA especializada
    "https://openai.com/blog/rss/",
    "https://huggingface.co/blog/feed.xml",
    "https://www.marktechpost.com/feed/",
    "https://syncedreview.com/feed/",
    # Automatización y robótica
    "https://spectrum.ieee.org/feeds/feed.rss",
    "https://www.roboticsandautomationnews.com/feed",
    # Startups e inversión tech
    "https://news.crunchbase.com/feed/",
    "https://sifted.eu/feed/",
    # Tecnología — medios grandes
    "https://feeds.bloomberg.com/technology/news.rss",
    "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "https://www.zdnet.com/news/rss.xml",
    "https://www.cnet.com/rss/news/",
    # Ciencia aplicada y futurismo
    "https://phys.org/rss-feed/",
    "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml",
    "https://singularityhub.com/feed/",
    "https://futurism.com/feed",
]

# ── Filtros de palabras ───────────────────────────────────────────────────────
PALABRAS_NEGATIVAS = {
    # Violencia y crimen
    "muerto", "muertos", "herido", "heridos", "asesinado", "asesinato",
    "crimen", "criminal", "homicidio", "ataque", "atentado",
    "terrorismo", "terrorista", "bomba", "masacre", "guerra",
    "detenido", "arrestado", "condenado", "preso", "robo", "asalto",
    # Desastres
    "accidente", "tragedia", "catástrofe", "desastre", "incendio",
    # Escándalos y negativos
    "escándalo", "corrupción", "fraude", "estafa",
    "colapso", "quiebra", "bancarrota", "fracaso",
    "protesta", "huelga", "disturbios",
    # Ruido tecnológico negativo
    "hackeo", "hack", "vulnerabilidad", "malware", "ransomware",
    "data breach", "ciberataque", "despidos", "layoffs",
    "demanda", "sued", "lawsuit",
}

PALABRAS_POSITIVAS = {
    # Inteligencia Artificial
    "inteligencia artificial", "artificial intelligence",
    "ia", " ai ", "gpt", "llm", "large language model",
    "machine learning", "deep learning", "neural network",
    "generative ai", "ia generativa", "modelo de lenguaje",
    "chatbot", "copilot", "gemini", "claude", "mistral", "llama",
    "openai", "anthropic", "deepmind", "hugging face",
    # Automatización y robótica
    "automatización", "automation", "robótica", "robotics",
    "robot", "autonomous", "autónomo", "automate",
    # Innovación tecnológica
    "innovación", "innovation", "breakthrough", "avance",
    "lanzamiento", "launch", "released", "unveiled", "presenta",
    "nuevo modelo", "new model", "open source", "código abierto",
    # Startups e inversión tech
    "startup", "unicornio", "unicorn", "funding", "financiamiento",
    "serie a", "serie b", "seed round", "ipo", "raises", "recauda",
    # Computación y hardware
    "chip", "semiconductor", "quantum", "computación cuántica",
    "gpu", "procesador", "nvidia", "supercomputadora", "supercomputer",
    # Software y plataformas
    "api", "plataforma", "platform", "saas", "herramienta",
    # Espacio
    "cohete", "rocket", "satélite", "satellite", "spacex", "nasa",
    # Biotecnología
    "biotech", "biotecnología", "crispr", "genoma", "genome",
}
# ── Telegram ──────────────────────────────────────────────────────────────────
def enviar_telegram(mensaje: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            r = requests.post(
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
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ElChilometroBot/1.0)"}

    for url in FUENTES:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except Exception as e:
            log.warning("Feed no disponible (%s): %s", url, e)
            continue

        for entry in feed.entries[:MAX_ENTRIES_POR_FEED]:
            titulo = getattr(entry, "title", "").strip()
            link   = getattr(entry, "link", "").strip()

            if not titulo or not link:
                continue
            if link in vistas:
                continue
            vistas.add(link)

            titulo_lower = titulo.lower()
            if any(neg in titulo_lower for neg in PALABRAS_NEGATIVAS):
                continue
            if any(pos in titulo_lower for pos in PALABRAS_POSITIVAS):
                noticias.append({"titulo": titulo, "link": link})

    log.info("Noticias candidatas: %d", len(noticias))
    return noticias

# ── Groq ──────────────────────────────────────────────────────────────────────
def _llamar_groq(prompt: str, reintentos: int = 3) -> str:
    cliente = Groq(api_key=GROQ_API_KEY)
    for intento in range(1, reintentos + 1):
        try:
            respuesta = cliente.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            return respuesta.choices[0].message.content.strip()
        except Exception as e:
            log.warning("Groq intento %d/%d fallido: %s", intento, reintentos, e)
            if intento < reintentos:
                time.sleep(2 ** intento)  # backoff exponencial
    raise RuntimeError("Groq no respondió tras varios intentos.")


def es_avance_positivo(titulo: str) -> bool:
    prompt = f'''Eres un filtro editorial estricto para un canal de tecnología e innovación.\n"
        "Evalúa si la noticia es un avance concreto en tecnología, IA, automatización o innovación.\n\n"
        "Criterios SÍ:\n"
        "- Lanzamiento de nuevos modelos de IA, herramientas o plataformas\n"
        "- Avances en automatización, robótica o computación\n"
        "- Inversiones o financiamientos en startups tech\n"
        "- Descubrimientos científicos aplicados a tecnología\n"
        "- Nuevas capacidades de software, hardware o chips\n\n"
        "Criterios NO:\n"
        "- Política, regulación o debates sin producto concreto\n"
        "- Opiniones, análisis o predicciones\n"
        "- Escándalos, hackeos, vulnerabilidades o demandas\n"
        "- Noticias de despidos o crisis en empresas tech\n"
        "- Noticias negativas, neutras o alarmistas\n\n"
        f'Noticia: "{titulo}"\n\n'
        "Responde SOLO con SÍ o NO.'''
    resultado = _llamar_groq(prompt)
    return resultado.upper().startswith("SÍ")


def generar_post(noticia: dict) -> str:
    prompt = (
        "Eres el editor de un canal de tecnología e innovación global.\n"
        "Tono: directo, informativo, sin exceso de emojis.\n\n"
        f"Noticia: {noticia['titulo']}\n"
        f"Link: {noticia['link']}\n\n"
        "Escribe un post para Twitter/X de máximo 280 caracteres:\n"
        "- Emoji tech relevante al inicio\n"
        "- El hecho concreto: qué se lanzó, descubrió o logró\n"
        "- Por qué importa para el mundo tech\n"
        "- Incluye el link\n"
        "- Fuente: [nombre del medio] al final\n"
        "- Sin hashtags\n\n"
        "Responde SOLO con el post."
    )
    return _llamar_groq(prompt)
    
# ── Persistencia ──────────────────────────────────────────────────────────────
def cargar_procesadas() -> set[str]:
    try:
        with open(PROCESADAS_FILE, "r") as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()


def guardar_procesadas(procesadas: set[str]) -> None:
    with open(PROCESADAS_FILE, "w") as f:
        f.write("\n".join(sorted(procesadas)))

    cmds = [
        ["git", "config", "user.email", "bot@elchilometro.cl"],
        ["git", "config", "user.name", "ElChilometro Bot"],
        ["git", "add", PROCESADAS_FILE],
        ["git", "commit", "-m", "chore: update procesadas"],
        ["git", "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.warning("Git '%s' falló: %s", " ".join(cmd), result.stderr)

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("ElChilometro iniciado.")
    enviar_telegram("📡 ElChilometro iniciado.")

    try:
        noticias = obtener_noticias()
    except Exception as e:
        log.error("Error obteniendo noticias: %s", e)
        enviar_telegram(f"❌ Error obteniendo noticias:\n{e}")
        return

    if not noticias:
        enviar_telegram("⚠️ Sin noticias relevantes en este ciclo.")
        return

    procesadas    = cargar_procesadas()
    noticias_nuevas = [n for n in noticias if n["link"] not in procesadas]
    log.info("Noticias nuevas: %d", len(noticias_nuevas))

    if not noticias_nuevas:
        enviar_telegram("⚠️ Sin noticias nuevas, todas ya procesadas.")
        return

    links_procesados: set[str] = set()

    for noticia in noticias_nuevas[:MAX_NOTICIAS_POR_CICLO]:
        try:
            if es_avance_positivo(noticia["titulo"]):
                post = generar_post(noticia)
                enviar_telegram(f"📢 POST SUGERIDO:\n\n{post}")
                log.info("Aprobado: %s", noticia["titulo"])
            else:
                enviar_telegram(f"❌ DESCARTADO:\n{noticia['titulo']}")
                log.info("Descartado: %s", noticia["titulo"])
        except Exception as e:
            log.error("Error procesando '%s': %s", noticia["titulo"], e)
            enviar_telegram(f"❌ Error al procesar:\n{noticia['titulo']}\n{e}")
        finally:
            links_procesados.add(noticia["link"])

    guardar_procesadas(procesadas | links_procesados)
    log.info("Ciclo completado. %d procesadas.", len(links_procesados))


if __name__ == "__main__":
    main()
