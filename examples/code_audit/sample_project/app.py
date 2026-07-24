import sqlite3


API_KEY = "DEMO_HARDCODED_CREDENTIAL"


def find_user(name: str):
    conn = sqlite3.connect(":memory:")
    return conn.execute("SELECT * FROM users WHERE name = '" + name + "'").fetchall()
