# Ágora Electoral

Aplicación Streamlit del **Observatorio Electoral de La Rioja** para ingerir
escrutinios, comparar elecciones, calcular volatilidad de Pedersen y generar un
dictamen politológico asistido por IA.

Versión de aplicación: **2.1.0** · Python **3.12+** · Streamlit **1.64+**.

---

## 1. Qué resuelve

- Carga de planillas `.csv`, `.xls`, `.xlsx` y `.xlsm` con validación de
  columnas, tipos y formato.
- Normalización de tres familias de entrada:
  1. planilla larga propia (`anio`, `distrito`, `circuito`, `cargo`, `fuerza`,
     `votos_positivos`, `porcentaje`);
  2. planilla ancha con columnas `pct_2023`, `pct_2025`, etc.;
  3. CSV oficial del CNE con mesas, `votos_tipo` y `votos_cantidad`.
- Persistencia en SQLite y reconstrucción automática desde
  `data/seed_resultados.csv` cuando la base está vacía.
- Comparación entre dos elecciones del mismo cargo, con agregación por fuerza y
  desagregado territorial.
- **Índice de Volatilidad Total de Pedersen**, con mitigación del sesgo de
  supervivencia: las fuerzas nuevas o extintas reciben `0,0%` en el período en
  que no compiten.
- Supervivencia partidaria y Número Efectivo de Partidos (NEP).
- Tabla de **homologación de partidos y alianzas** para evitar comparar como
  distintas a la misma fuerza por cambios de nombre, tildes o denominación.
- Dictamen híbrido: **Groq** como proveedor principal, **Gemini** como respaldo
  opcional, **Ollama** solo en local y **motor de reglas** como fallback
  determinista.
- Informes que separan datos comprobados, indicadores calculados,
  interpretación politológica, prospectiva y nota metodológica.
- Descargas CSV del conjunto comparado, desglose territorial, detalle Pedersen,
  homologación y dictamen en Markdown.

---

## 2. Estructura

```text
app.py                         Entrada delgada: setup_page() + main()
agora/
  config.py                    Rutas, secretos, entorno, SQLite, paleta
  auth.py                      Acceso público, hash y sesión administrativa
  utils.py                     Formato, normalización y sanitización
  lectura_planillas.py         Detección de hojas, encabezados y metadatos
  ingest.py                    Validación, auditoría de fuentes y SQLite
  metrics.py                   Agregación, Pedersen, supervivencia, NEP
  homologacion.py              Tabla de equivalencias y aplicación por año
  ai.py                        Prompt, Groq, Gemini, Ollama y reglas
  header.py                    Cabecera sticky compatible con Streamlit
  ui.py                        Sidebar, vistas, pestañas, diálogo y descargas
  assets/header.{css,html,js}  Cabecera institucional
data/
  seed_resultados.csv          Semilla verificable (2023)
  homologacion_partidos.csv    Tabla inicial de homologación
tests/test_core.py             Pruebas de ingesta, métricas, homologación, IA
```

Los módulos históricos `analizador.py`, `ingesta_electoral.py` y `respuesta.py`
se conservan sin modificaciones como referencia y compatibilidad.

---

## 3. Ejecución local

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

También se incluye el lanzador `./streamlit` (usa `.venv` y el puerto 8501).

La app arranca con la semilla de 2023. Para comparar, ingresá como
administradora desde **Acceso administrador** y cargá una segunda planilla del
mismo cargo y año distinto desde **Browse files / Examinar**. La app inspecciona
todas las hojas, muestra la vista previa original y la normalizada, y sólo
importa cuando pulsás **Validar e importar planillas**.

### Variables de entorno útiles

| Variable | Uso |
| --- | --- |
| `ADMIN_PASSWORD_HASH` | Hash PBKDF2 de la contraseña de administradora. Obligatorio para cargar o homologar datos. |
| `GROQ_API_KEY` | Clave del proveedor principal. |
| `GROQ_MODEL` | Modelo Groq; por defecto `openai/gpt-oss-120b`. |
| `GROQ_API_BASE` | Endpoint compatible OpenAI; por defecto la API de Groq. |
| `GEMINI_API_KEY` | Respaldo opcional en la nube. |
| `GEMINI_MODEL` | Modelo Gemini; por defecto `gemini-2.5-flash`. |
| `AGORA_ENTORNO` | `produccion`, `local`, `dev` o `test`. |
| `OLLAMA_ENABLED` | `true` para habilitar Ollama fuera de un entorno local. |
| `OLLAMA_HOST` | Por defecto `http://localhost:11434`. |
| `OLLAMA_MODEL` | Por defecto `gemma3:1b`. |
| `AGORA_DB_PATH` | Ruta SQLite persistente (volumen de Render/Railway). |
| `AGORA_SEED` | `0` desactiva la carga de la semilla. |
| `AGORA_OFFLINE` | `1` desactiva las claves de nube para pruebas sin red. |

En Streamlit Community Cloud, las mismas claves se cargan en **Secrets**. El
archivo `.streamlit/secrets.toml.example` sirve como plantilla; nunca subas
`.streamlit/secrets.toml` al repositorio.

```toml
ADMIN_PASSWORD_HASH = "pbkdf2_sha256$600000$..."
GROQ_API_KEY = "gsk_..."
GROQ_MODEL = "openai/gpt-oss-120b"
AGORA_ENTORNO = "produccion"
```

### Acceso público y administración

La app tiene dos modalidades en la misma sesión de Streamlit:

- **Visitante público:** entra sin registrarse y puede consultar resultados,
  gráficos, indicadores, metodología y descargar los CSV públicos. No ve el
  formulario de carga ni el editor de homologación.
- **Administradora:** abre **Acceso administrador** en el panel lateral,
  ingresa la contraseña y habilita la carga, validación, importación local y
  edición de homologación. El botón **Cerrar sesión** vuelve al modo público.

La autorización no depende de ocultar botones. `guardar_fuentes_en_db()` y
`guardar_homologacion()` comprueban la sesión en Python y lanzan
`PermissionError` si no hay una administradora autenticada. La semilla inicial
del sistema usa rutas internas separadas y sólo se ejecuta cuando la base está
vacía.

#### Generar el hash de la contraseña

Desde la raíz del proyecto, con el entorno virtual activado:

```bash
python -m agora.auth
```

El comando pide la contraseña dos veces sin mostrarla y escribe en la terminal
un valor con este formato:

```text
pbkdf2_sha256$600000$<sal>$<hash>
```

El hash se imprime sólo en tu terminal para que lo copies a Secrets; no lo
compartas ni lo publiques. No copies la contraseña a ningún archivo. Guardá
únicamente ese hash como
`ADMIN_PASSWORD_HASH` en `.streamlit/secrets.toml` (local) o en **Secrets** de
Streamlit Cloud (producción). Si el hash no está configurado, el botón de
acceso aparece deshabilitado y la consulta pública sigue funcionando.

#### Configurar en Streamlit Community Cloud

1. Abrí la app en Streamlit Cloud y entrá en **Settings → Secrets**.
2. Agregá o actualizá la clave `ADMIN_PASSWORD_HASH` con el hash generado.
3. Guardá los cambios y reiniciá la app si Streamlit no lo hace solo.
4. Verificá que **Acceso administrador** abra el formulario y que el ingreso
   correcto muestre **Cerrar sesión** y la sección de carga.

La contraseña real y el hash son secretos: no los incluyas en GitHub, en
capturas, en issues ni en la conversación. `.streamlit/secrets.toml` está
ignorado por Git. En SQLite los cambios siguen siendo efímeros en el hosting
gratuito; la semilla y la homologación versionadas se reconstruyen desde
`data/` al reiniciar.

### Orden de motores

1. **Groq** si `GROQ_API_KEY` está configurada.
2. **Gemini** si existe `GEMINI_API_KEY` o `GOOGLE_API_KEY`.
3. **Ollama** solo si `AGORA_ENTORNO=local`/`dev`/`test` o
   `OLLAMA_ENABLED=true`. En producción no se intenta conectar.
4. **Motor de reglas** siempre disponible. Nunca deja la vista sin informe.

Los errores de red o de cuota no se muestran como fallo fatal: se registran en
el diagnóstico del motor y se continúa con el siguiente proveedor.

---

## 4. Formatos de entrada

### 4.1 Planilla larga propia

```csv
anio,distrito,circuito,cargo,fuerza,votos_positivos,porcentaje
2023,Capital,0001A,PRESIDENTE/A,LA LIBERTAD AVANZA,1234,45.2
2023,Capital,0001A,PRESIDENTE/A,UNIÓN POR LA PATRIA,900,33.0
```

Reglas:

- `anio` puede venir en la planilla o deducirse del nombre del archivo
  (`resultados_2023.csv`) o de la hoja de Excel (`2023`).
- Si falta `porcentaje`, se calcula con los votos positivos del mismo año,
  distrito, circuito y cargo.
- Si `votos_positivos` falta pero hay porcentaje, se usa el porcentaje.
- Se aceptan coma o punto y coma como separador, y decimales con coma o punto.
- Se rechazan votos negativos, porcentajes fuera de `[0, 100]`, fuerzas vacías
  y claves duplicadas dentro de una misma planilla.

### 4.2 Planilla ancha

```csv
fuerza,circuito,pct_2023,pct_2025
LA LIBERTAD AVANZA,0001A,45.2,48.1
UNIÓN POR LA PATRIA,0001A,33.0,29.4
```

Las columnas `pct_AÑO` se convierten a filas. El año también puede estar en el
nombre del archivo o de la hoja.

### 4.3 Planillas oficiales con metadatos

El importador reconoce planillas de escrutinio donde los datos no empiezan en
la primera fila. Inspecciona todas las hojas, busca el encabezado real (por
ejemplo, la fila 15 de `zona_DN_DIST_12.xls`), lee metadatos como
`FECHA ELECCION`, `Cargo:` y `Distrito:`, y detecta la instancia electoral
(PASO, generales, provinciales o municipales).

También admite encabezados distribuidos en dos o más filas consecutivas y
columnas con variantes como *Agrupación Política*, *Partido*, *Alianza*,
*Lista*, *Votos*, *Circuito*, *Mesa*, *Departamento* o *Categoría*.

Las filas de totales, subtotales, votos nulos, blancos, recurridos e
impugnados se excluyen de la base positiva, pero quedan preservadas en el
archivo original. Los porcentajes generales o válidos no se usan como cuota
positiva cuando existe una columna afirmativa/positiva o votos positivos.

### 4.3 CSV oficial del CNE

Se reconocen `votos_tipo`, `votos_cantidad`, `agrupacion_nombre`,
`cargo_nombre`, `seccion_nombre`, `circuito_id`, etc. La app:

- conserva solo `votos_tipo = POSITIVO`;
- agrega las mesas por año, distrito, circuito, cargo y fuerza;
- agrega el tipo de elección al cargo (`PRESIDENTE/A — GENERAL`,
  `DIPUTADOS/AS NACIONALES — PASO`, etc.);
- calcula el porcentaje dentro de cada grupo.

### 4.5 Corrección manual y auditoría

Si la detección automática no alcanza, la sección de carga permite:

- elegir la hoja y la fila de encabezado;
- mapear agrupación, votos, porcentaje, año, cargo, distrito, circuito y mesa;
- completar año, categoría y territorio cuando faltan en los metadatos;
- revisar las filas aceptadas y excluidas antes de confirmar.

Cada importación guarda en SQLite el archivo original completo, su huella
SHA-256, el formato, el tamaño y un resumen JSON con hojas, encabezados,
mapeos y exclusiones. La tabla `planillas_fuentes` permite auditar qué se
importó y reconstruir el origen. La pestaña **Metodología y fuentes** muestra
ese registro.

### 4.6 Navegación del observatorio

La aplicación organiza el trabajo en una sección de carga y ocho pestañas:
**Inicio**, **Resultados electorales**, **Análisis territorial**,
**Volatilidad de Pedersen**, **Sistema de partidos**, **Prospectiva 2027**,
**Informes IA** y **Metodología y fuentes**. Las pestañas que requieren dos
elecciones muestran un estado explicativo cuando no hay comparación válida; no
inventan indicadores ni rellenan faltantes con cero.

---

## 5. Metodología

### 5.1 Agregación

La participación por fuerza se calcula como **cuota de votos positivos** cuando
hay votos disponibles. Si faltan votos, se usa el promedio simple de los
porcentajes de los circuitos y la interfaz lo advierte. La comparación conserva
`votos_base` y `votos_actual` para poder auditar la ponderación.

### 5.2 Pedersen

```text
VT = Σ |V(i,t) − V(i,t−1)| / 2
```

Con mitigación activada (por defecto), una fuerza sin datos en uno de los dos
períodos recibe `0,0%` en ese período. Esto evita el sesgo de supervivencia y
contabiliza entradas y salidas del sistema de partidos. Si se desactiva, la
interfaz marca el índice como parcial y advierte que no se infieren ausencias.

El detalle clasifica cada fuerza como **Sobreviviente**, **Nueva**, **Extinta**
o **Sin votos**, e informa su aporte absoluto a Pedersen.

### 5.3 Supervivencia y NEP

- Sobreviviente: participación mayor que cero en ambos períodos.
- La tasa de supervivencia se calcula sobre las fuerzas con presencia en el año
  base; se informa además el peso de las sobrevivientes en el voto base.
- La descomposición de Pedersen separa el componente de fuerzas presentes en
  ambos períodos del componente de nuevas/extintas. No demuestra transferencia
  individual de votos.
- NEP = `1 / Σ p_i²` sobre la participación agregada.

### 5.4 Homologación

La tabla `homologacion_fuerzas` se guarda en SQLite y se siembra desde
`data/homologacion_partidos.csv`. Cada regla tiene:

- `fuerza_origen`: denominación tal como aparece en la planilla;
- `fuerza_homologada`: nombre canónico;
- `tipo`: Partido, Alianza, Coalición, Independiente u Otro;
- `anio_desde` / `anio_hasta`: opcionales, para reglas específicas por año;
- `notas`.

Las reglas con año explícito tienen prioridad sobre las generales. La
normalización de tildes, mayúsculas, guiones y espacios se usa para encontrar
coincidencias, pero el nombre canónico se conserva. La aplicación se hace en
tiempo de consulta: los datos crudos no se reescriben.

Desde la barra lateral o la pestaña **Homologación** se puede editar, agregar
identidades para fuerzas faltantes, importar un CSV o descargar la tabla.

---

## 6. Dictamen politológico

El prompt exige cinco secciones:

1. **Datos comprobados** — solo lo que está en la base y en la tabla
   homologada.
2. **Indicadores calculados** — Pedersen, variación, NEP, supervivencia y
   desagregado territorial.
3. **Interpretación politológica** — lectura sobria, sin inventar causas.
4. **Prospectiva 2027 (escenario)** — condicional, no pronóstico.
5. **Nota metodológica** — límites de Pedersen, cobertura y homologación.

El motor de reglas produce la misma estructura sin red. Groq y Gemini reciben
los datos ya calculados; no se les pide calcular Pedersen ni inventar cifras.
La interfaz muestra el motor usado, el tiempo y el diagnóstico de intentos.

---

## 7. Cabecera institucional

La cabecera se inyecta con `st.markdown` para el HTML/CSS y con `st.iframe`
(`height=1`) para ejecutar el JavaScript en un iframe del mismo origen. El
script observa el scroll del documento padre y aplica `position: sticky; top:
0; z-index: 999`, la compactación al desplazar y el reacomodo del logo sobre el
dock. Los contenedores intermedios de Streamlit usan `display: contents` para
que el sticky tenga como bloque contenedor toda la página.

Paleta: azules eléctrico/federal, cabecera blanca, texto grafito y cinta azul
profundo. Sin amarillo. El logo usa un SVG del Congreso de la Nación.

---

## 8. Despliegue

### Streamlit Community Cloud

1. Subí el repositorio a GitHub.
2. Elegí `app.py` como archivo principal.
3. Cargá `ADMIN_PASSWORD_HASH` y `GROQ_API_KEY` en **Secrets**.
4. La base SQLite es efímera; al reiniciar se reconstruye desde
   `data/seed_resultados.csv` y `data/homologacion_partidos.csv`.
5. La contraseña administrativa nunca se guarda en el repositorio: sólo su
   hash en Secrets.

### Render / Railway

- `Procfile`: `web: streamlit run app.py --server.port=$PORT ...`
- `runtime.txt`: `python-3.12`.
- Para persistencia real, montá un volumen y definí
  `AGORA_DB_PATH=/ruta/al/volumen/observatorio.db`.
- Sin volumen, el comportamiento es el mismo que en Streamlit Cloud: base
  efímera y reconstrucción desde `data/`.

### Docker

```bash
docker build -t agora-electoral .
docker run --rm -p 8501:8501 -e GROQ_API_KEY=... agora-electoral
```

El `Dockerfile` incluye un healthcheck en `/_stcore/health`.

---

## 9. Pruebas

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Cobertura actual de la suite:

- ingesta CSV largo, ancho, Excel, CNE y errores de validación;
- planilla oficial `zona_DN_DIST_12.xls` con encabezado en fila 15, metadatos
  2021, cinco fuerzas y exclusión de totales/no positivos;
- encabezados en dos filas, varias hojas/categorías, mapeo manual y auditoría
  de la fuente original;
- cálculo de porcentajes, agregación por votos y por promedio;
- Pedersen con nuevas/extintas, supervivencia y NEP;
- comparación con fuerza nueva y conservación de votos;
- homologación por año, tildes, guardado/carga y fuerzas faltantes;
- hash PBKDF2, comparación segura y rechazo de escrituras administrativas
  cuando la sesión es pública;
- login correcto, login incorrecto, cierre de sesión y controles ocultos para
  visitantes en AppTest;
- prompt, secciones del informe, Groq primero, Ollama local y fallback a reglas;
- higiene de rutas absolutas y orden de `setup_page()`.

Además se ejecutaron verificaciones de integración:

- `AppTest` de Streamlit con una elección, comparación 2023→2025, acceso
  público, login correcto/incorrecto y cierre de sesión;
- carga real del XLS oficial en navegador: 5 filas, 5 agrupaciones, 1 circuito,
  178.151 votos positivos y fuente original almacenada;
- navegador headless (Chrome CDP): carga de dos planillas, importación,
  comparación, Pedersen y dictamen; botones de descarga renderizados;
- navegador headless (Firefox/Playwright): ocho pestañas sin excepciones, sin
  desborde horizontal en escritorio ni móvil, cabecera `70/38/16 px`, versión
  compacta `42/25 px` y logo móvil `52 px`;
- cabecera sticky: `position: sticky`, `top: 0`, compactación, logo centrado
  sobre el dock, sin solapamiento y función de limpieza;
- servidor mock compatible con la API de Groq para verificar el camino de red,
  el `Authorization`, el prompt y la respuesta;
- instalación limpia en un directorio nuevo, con `venv` recién creado y
  `pip install -r requirements.txt`: suite automatizada OK, arranque aislado,
  healthcheck `200`, semilla reconstruida con **1.190 filas** y **9 reglas** de
  homologación.

**Groq real verificado:** con la clave configurada en `.streamlit/secrets.toml`
se consultó el catálogo de modelos de la cuenta y se generó el dictamen desde
la pestaña **Informes IA** con `openai/gpt-oss-120b`. La respuesta incluyó las
secciones obligatorias y el motor informado fue `Groq · openai/gpt-oss-120b`
(≈3,1 s). El modelo anterior `llama-3.1-8b-instant` ya no está disponible en la
cuenta y devolvía HTTP 404; por eso se actualizó el valor por defecto.

---

## 10. Límites y criterios

- Pedersen mide desplazamiento neto agregado; no prueba transferencias
  individuales ni intención de voto.
- La calidad de la comparación depende de la homologación. Una alianza que
  cambió de composición puede requerir reglas por año y una nota explicativa.
- Una ausencia de registro no equivale automáticamente a extinción; por eso la
  mitigación es explícita y se puede desactivar para auditar.
- Las proyecciones 2027 son escenarios de trabajo, no pronósticos.
- En hosting gratuito sin volumen, la base SQLite no persiste entre reinicios;
  la semilla y la tabla de homologación sí están versionadas en `data/`.

---

## 11. Estado de la entrega

- Procesamiento electoral, comparación, Pedersen, supervivencia, NEP y
  homologación: verificados.
- Carga de XLS, XLSX y CSV, vista previa, mapeo manual y descarga de
  resultados: verificadas con pruebas automatizadas y navegador.
- Dictamen con Groq: verificado con clave real, modelo
  `openai/gpt-oss-120b` y fallback determinista cuando falta la clave o la
  respuesta no respeta la estructura.
- Arranque limpio: verificado con `venv` nuevo y dependencias instaladas desde
  `requirements.txt`; **43 pruebas automatizadas OK** (incluye acceso público,
  login, logout y rechazo de escrituras administrativas) y semilla
  reconstruida.
- Navegación final: ocho pestañas y sección de carga verificadas en Chrome
  headless y Firefox/Playwright, sin excepciones, con datos de semilla y con
  comparación 2023→2025.
