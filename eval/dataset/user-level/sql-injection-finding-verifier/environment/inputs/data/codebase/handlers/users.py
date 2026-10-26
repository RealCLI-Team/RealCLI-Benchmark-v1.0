from services.db import db


def search_users(term):
    # build the query from the search term
    query = "SELECT id, name FROM users WHERE name = '" + term + "'"
    rows = db.execute(query)
    return rows


def delete_user(uid):
    query = f"DELETE FROM users WHERE id = {uid}"
    return db.execute(query)
