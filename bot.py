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
    os.environ["TELEGRAM_CHAT_ID"],
    os.environ["TELEGRAM_CHAT_ID_2"],
]

MAX_NOTICIAS_POR_CICLO = 5
MAX_ENTRIES_POR_FEED   = 10
GROQ_MODEL             = "llama-3.3-70b-versatile"
PROCESADAS_FILE        = "procesadas.txt"

# ── Fuentes RSS ───────────────────────────────────────────────────────────────
FUENTES = [
    # Chile — noticias generales
    "https://feeds.emol.com/emol/nacional",
    "https://feeds.emol.com/emol/economia",
    "https://www.cooperativa.cl/noticias/rss/",
    "https://www.latercera.com/arc/outboundfeeds/rss/?outputType=xml",
    "https://www.elmostrador.cl/feed/",
    "https://www.df.cl/feed",
    "https://www.pulso.cl/feed/",
    "https://www.ex-ante.cl/feed/",
    "https://www.biobiochile.cl/lista/categoria/nacional/feed/",
    "https://www.cnnchile.com/feed/",
    "https://radio.uchile.cl/feed/",
    "https://www.24horas.cl/rss/ultimas-noticias",
    # Economía y negocios global
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.bloomberg.com/technology/news.rss",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.reuters.com/reuters/technologyNews",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    "https://www.economist.com/finance-and-economics/rss.xml",
    "https://www.economist.com/business/rss.xml",
    "https://www.wsj.com/xml/rss/3_7014.xml",
    # Tecnología e innovación
    "https://techcrunch.com/feed/",
    "https://www.wired.com/feed/rss",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://www.technologyreview.com/feed/",
    "https://venturebeat.com/feed/",
    "https://www.theverge.com/rss/index.xml",
    # Ciencia y medio ambiente
    "https://www.sciencedaily.com/rss/top/science.xml",
    "https://www.nature.com/nature.rss",
    "https://phys.org/rss-feed/",
    # Energía y minería
    "https://www.mining.com/feed/",
    "https://oilprice.com/rss/main",
    "https://www.mineria.cl/feed/",
    "https://www.mch.cl/feed/",
    # Latinoamérica e internacional
    "https://en.mercopress.com/rss/chile",
    "https://en.mercopress.com/rss/economy",
    "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/america/portada",
    "https://www.americaeconomia.com/rss.xml",
    "https://feeds.reuters.com/reuters/latinamerica",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://feeds.bbci.co.uk/news/world/latin_america/rss.xml",
    # Startups e inversión
    "https://news.crunchbase.com/feed/",
    "https://techcrunch.com/category/startups/feed/",
]

# ── Filtros de palabras ───────────────────────────────────────────────────────
PALABRAS_NEGATIVAS = {
    "muerto", "muertos", "herido", "heridos", "asesinado", "asesinato",
    "crimen", "criminal", "homicidio", "femicidio", "violación",
    "ataque", "atentado", "terrorismo", "terrorista", "bomba",
    "masacre", "genocidio", "guerra", "conflicto armado",
    "detenido", "imputado", "arrestado", "condenado", "preso",
    "robo", "asalto", "narcotráfico", "cartel",
    "accidente", "tragedia", "catástrofe", "desastre",
    "incendio", "terremoto", "inundación", "tsunami",
    "escándalo", "corrupción", "fraude", "estafa", "colusión",
    "crisis", "colapso", "quiebra", "bancarrota",
    "caída", "baja", "retroceso", "fracaso", "rechazo",
    "protesta", "huelga", "manifestación", "disturbios",
}

PALABRAS_POSITIVAS = {
    "inversión", "inversiones", "millones", "billones", "acuerdo",
    "contrato", "exportación", "crecimiento", "récord", "expansión",
    "apertura", "lanzamiento", "alianza", "fusión", "adquisición",
    "financiamiento", "fondo", "startup", "unicornio",
    "innovación", "inteligencia artificial", "ia", "robótica",
    "automatización", "digitalización", "blockchain", "satélite",
    "cohete", "misión espacial", "descubrimiento", "patente",
    "energía renovable", "solar", "eólica", "hidrógeno verde",
    "litio", "cobre", "planta solar", "descarbonización",
    "sostenible", "sustentable", "electromovilidad",
    "inauguró", "inauguración", "proyecto", "infraestructura",
    "conectividad", "puerto", "aeropuerto", "histórico", "hito",
    "vacuna", "tratamiento", "avance médico", "ensayo clínico",
    "hallazgo", "exploración",
    "acuerdo comercial", "exportaciones", "g20", "ocde", "cumbre",
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
    prompt = f'''Eres un filtro editorial estricto para un canal de noticias de avances y progreso global.
Evalúa si la siguiente noticia representa un avance concreto y positivo.

Criterios SÍ:
- Inversiones, acuerdos comerciales, nuevos proyectos
- Innovación, tecnología, energía, infraestructura
- Logros científicos, récords económicos, descubrimientos
- Acuerdos internacionales con impacto real

Criterios NO:
- Política sin impacto concreto
- Declaraciones, opiniones, discursos
- Conflictos, violencia, escándalos
- Noticias negativas, neutras o alarmistas

Noticia: "{titulo}"

Responde SOLO con SÍ o NO.'''
    resultado = _llamar_groq(prompt)
    return resultado.upper().startswith("SÍ")


def generar_post(noticia: dict) -> str:
    prompt = f"""Eres el editor de un canal de noticias de avances globales.
Tono: formal, informativo, sin exceso de emojis.

Noticia: {noticia['titulo']}
Link: {noticia['link']}

Escribe un post para Twitter/X de máximo 280 caracteres:
- Emoji relevante al inicio
- El hecho concreto en una línea
- Por qué importa a nivel global o para Chile
- Incluye el link
- Fuente: [nombre del medio] al final
- Sin hashtags

Responde SOLO con el post."""
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
