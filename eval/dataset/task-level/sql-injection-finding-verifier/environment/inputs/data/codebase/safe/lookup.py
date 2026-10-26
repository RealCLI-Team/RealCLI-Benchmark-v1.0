from services.db import db


def lookup_product(pid):
    query = "SELECT * FROM products WHERE id = ?"
    return db.execute(query, (pid,))
