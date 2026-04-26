"""
migrate_db_v2.py — Migration pour la table notifications
Lance : python migrate_db_v2.py
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "egg_agent.db")
print(f"📂 DB: {DB_PATH}")

conn = sqlite3.connect(DB_PATH)
cur  = conn.cursor()

def col_exists(table, col):
    cur.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())

def add_col(table, col, typ, default="NULL"):
    if col_exists(table, col):
        print(f"  ⏭️  {table}.{col} existe déjà")
    else:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ} DEFAULT {default}")
        conn.commit()
        print(f"  ✅ Ajouté {table}.{col}")

print("\n── notifications ────────────────────────────────")
# Créer la table si elle n'existe pas du tout
cur.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        message    TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
""")
conn.commit()

# Ajouter toutes les colonnes manquantes
add_col("notifications", "partner",     "TEXT")
add_col("notifications", "event_type",  "TEXT")
add_col("notifications", "order_id",    "TEXT")
add_col("notifications", "lot_id",      "TEXT")
add_col("notifications", "grade",       "TEXT")
add_col("notifications", "payload",     "TEXT")
add_col("notifications", "delivered",   "INTEGER", "0")
add_col("notifications", "title",       "TEXT")
add_col("notifications", "severity",    "TEXT",    "'info'")
add_col("notifications", "tool_source", "TEXT")
add_col("notifications", "is_read",     "INTEGER", "0")

print("\n── Vérification ─────────────────────────────────")
cur.execute("PRAGMA table_info(notifications)")
cols = [r[1] for r in cur.fetchall()]
print(f"  notifications cols: {', '.join(cols)}")

conn.close()
print("\n🎉 Migration v2 terminée — relancez python test_pipeline.py")