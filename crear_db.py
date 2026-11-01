import sqlite3

conn = sqlite3.connect("observatorio.db")
cursor = conn.cursor()

cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS elecciones_circuito (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        circuito TEXT NOT NULL,
        fuerza TEXT NOT NULL,
        pct_2023 REAL NOT NULL,
        pct_2025 REAL NOT NULL,
        diferencia REAL NOT NULL
    )
"""
)

cursor.execute("DELETE FROM elecciones_circuito")

datos_capital = [
    ("Circuito 1 - Centro", "FEDERALES DEFENDAMOS LA RIOJA", 38.0, 42.0, 4.0),
    ("Circuito 1 - Centro", "LA LIBERTAD AVANZA", 22.0, 48.0, 26.0),
    ("Circuito 1 - Centro", "JUNTOS POR EL CAMBIO", 35.0, 0.0, -35.0),
    ("Circuito 2 - Zona Sur", "FEDERALES DEFENDAMOS LA RIOJA", 48.0, 51.0, 3.0),
    ("Circuito 2 - Zona Sur", "LA LIBERTAD AVANZA", 15.0, 41.0, 26.0),
    ("Circuito 2 - Zona Sur", "JUNTOS POR EL CAMBIO", 32.0, 0.0, -32.0),
    (
        "Circuito 3 - Zona Norte",
        "FEDERALES DEFENDAMOS LA RIOJA",
        45.0,
        46.5,
        1.5,
    ),
    ("Circuito 3 - Zona Norte", "LA LIBERTAD AVANZA", 18.0, 44.0, 26.0),
    ("Circuito 3 - Zona Norte", "JUNTOS POR EL CAMBIO", 33.0, 0.0, -33.0),
    (
        "Circuito 4 - Zona Oeste",
        "FEDERALES DEFENDAMOS LA RIOJA",
        42.0,
        45.0,
        3.0,
    ),
    ("Circuito 4 - Zona Oeste", "LA LIBERTAD AVANZA", 20.0, 46.0, 26.0),
    ("Circuito 4 - Zona Oeste", "JUNTOS POR EL CAMBIO", 34.0, 0.0, -34.0),
]

cursor.executemany(
    """
    INSERT INTO elecciones_circuito (circuito, fuerza, pct_2023, pct_2025, diferencia)
    VALUES (?, ?, ?, ?, ?)
""",
    datos_capital,
)

conn.commit()
conn.close()
print("✅ Base de datos 'observatorio.db' creada exitosamente.")