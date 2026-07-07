import sqlite3


API_KEY = "sk-demo-hardcoded-secret"


def find_user(name: str):
    conn = sqlite3.connect(":memory:")
    return conn.execute("SELECT * FROM users WHERE name = '" + name + "'").fetchall()
