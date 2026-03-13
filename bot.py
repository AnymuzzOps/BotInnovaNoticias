
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
