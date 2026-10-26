from flask import Flask, request
from handlers.users import search_users, delete_user
from handlers.orphan import build_report
from safe.lookup import lookup_product
from services.auth import require_admin

app = Flask(__name__)


@app.route("/search")
def search():
    term = request.args.get("q")
    return search_users(term)


@app.route("/users/delete", methods=["POST"])
def delete():
    require_admin(request)
    uid = request.form.get("id")
    return delete_user(uid)


@app.route("/product")
def product():
    pid = request.args.get("pid")
    return lookup_product(pid)
