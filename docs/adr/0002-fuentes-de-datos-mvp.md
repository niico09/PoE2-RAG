# ADR-0002: Fuentes de datos del MVP

- **Estado:** aceptada (parcial: hay fuentes candidatas pendientes de evaluación)
- **Fecha:** 2026-10-29
- **Decisión relacionada:** A1 en [architecture.md](../architecture.md#9-decisiones-abiertas)

## Contexto

El índice solo debe contener información verificable y atribuible a una versión de parche (drivers D1 y D3). Las fuentes disponibles difieren en confiabilidad, estructura y frecuencia de cambio.

## Decisión

| Fuente | URL | Uso en el MVP |
|---|---|---|
| poe2db | https://poe2db.tw/ | **Indexada.** Datos de gemas y modificadores. |
| Notas de parche oficiales | https://www.pathofexile.com/forum/view-forum/2212 | **Indexada.** Fuente de verdad de la vigencia (`Validity`). |
| poe2wiki.net | https://www.poe2wiki.net/ | **Indexada.** Explicación de mecánicas (por ejemplo, `more` vs. `increased`). |
| Foro de la comunidad | https://www.pathofexile.com/forum/view-category/poe2 | **No indexada.** Solo materia prima para el dataset de evaluación. |
| poe.ninja | https://poe.ninja/ | **Excluida.** Datos de economía y builds, volátiles; no describe reglas del juego. |

Candidatas pendientes de evaluación (no forman parte del MVP hasta un nuevo ADR):

- Exportaciones de datos minados (PyPoE, forks de RePoE), como alternativa estructurada al scraping de poe2db.

## Consecuencias

- Se necesitan tres parsers: poe2db, notas de parche y poe2wiki.net.
- poe2wiki.net está detrás de una protección anti-bots (Anubis v1.27.0): el 2026-10-29 una consulta automatizada a `/w/api.php` devolvió "Access Denied". No se elude la protección. Antes de implementar el adaptador hay que elegir una vía autorizada: pedir acceso a la API a los administradores, usar un volcado oficial de la wiki si existe o capturar snapshots manuales en `data/raw/`. Tampoco está verificada todavía la licencia del contenido, que condiciona cómo se cita.
- Las páginas de la wiki no traen versión de parche explícita. Su `Validity` se infiere de la fecha de la revisión cruzada con el `PatchCatalog`, y tienen menor prioridad que poe2db cuando los valores numéricos se contradicen.
- El foro de la comunidad no requiere un adaptador de indexación; las preguntas para el dataset de evaluación pueden extraerse a mano.
- Las respuestas nunca citan opiniones de usuarios como si fueran datos.
- Pendiente antes de implementar el adaptador de poe2db: revisar sus términos de uso. Su `robots.txt` solo bloquea `/*/marked` y `/*/markdown_history`.
- Si en el futuro se responden preguntas sobre el meta o la economía, poe.ninja entraría como consulta en vivo detrás de un puerto propio, no como documentos indexados.
