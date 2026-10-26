from services.db import db


def build_report(name):
    # not registered on any route
    query = "SELECT * FROM reports WHERE owner = '" + name + "'"
    return db.execute(query)
