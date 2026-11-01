---
name: observatorio
description: Analiza y procesa datos electorales subnacionales y nacionales enfocados en La Rioja y Argentina. Use when users ask to analyze historical election data (from 2003 to 2023), calculate political indicators like Pedersen electoral volatility, detect survivorship biases in party coalitions, or project trends toward the 2027 national elections.
---

# Observatorio Electoral La Rioja

Skill especializado en el procesamiento, análisis estadístico, cálculo de indicadores políticos y modelado predictivo del comportamiento electoral en La Rioja y Argentina (2003–2027).

## Resumen
Esta herramienta estructura el flujo analítico para un observatorio electoral enfocado en elecciones provinciales y nacionales con impacto subnacional. Integra cálculo de volatilidad electoral (índice de Pedersen), control del sesgo de supervivencia en coaliciones y preparación de modelos predictivos hacia 2027.

## Cuándo Usar
- Análisis de series temporales de elecciones en La Rioja (Presidente, Diputados, Senadores y Gobernación).
- Comparativa de dinámicas electorales entre 2003 y 2023.
- Cálculo e interpretación de indicadores electorales (Volatilidad total de Pedersen, Número Efectivo de Partidos, polarización).
- Evaluación metodológica de alianzas que desaparecen o se fragmentan (mitigación de sesgo del superviviente).
- Preparación de variables e inferencia prospectiva para elecciones nacionales 2027.

## Flujo de Trabajo y Pasos

### 1. Ingesta y Normalización de Datos Electorales
- Requerir o parsear conjuntos de datos en formato tabular (CSV, JSON o DataFrames) con columnas mínimas: `año`, `distrito`/`seccion`, `cargo`, `agrupacion_politica`, `votos_positivos`, `porcentaje`.
- Normalizar alianzas y sellos electorales a través del tiempo para evitar rupturas artificiales de series temporales.

### 2. Cálculo de Indicadores Políticos Electorales
- **Volatilidad Electoral Total (Índice de Pedersen):**
  - Fórmula: `Volatilidad Total (VT) = Suma(|V_i,t - V_i,t-1|) / 2`
  - Donde `V_i,t` es el porcentaje de votos de la fuerza política `i` en la elección `t`, y `V_i,t-1` en la elección inmediatamente anterior consecutiva para el mismo cargo.

### 3. Mitigación Metodológica del Sesgo de Supervivencia
- No descartar fuerzas políticas menores, alianzas disueltas o sellos transitorios extintos entre 2003 y 2023.
- Asignar `0.0%` a las fuerzas ausentes en un período dado para garantizar la simetría matemática en la suma de volatilidades y transferencias.
- Evitar inferir causalidades exclusivas a partir de las fuerzas que lograron subsistir hasta 2023.

### 4. Análisis Prospectivo hacia 2027
- Comparar las elecciones intermedias y ejecutivas recientes en La Rioja frente al escenario federal.
- Evaluar escenarios de transferencia de votos entre tercios y bipartidismos históricos basándose en la volatilidad interanual.