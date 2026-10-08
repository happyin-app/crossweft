"""Flask: blueprints with url_prefix, nested blueprints, a url_prefix that
replaces the blueprint's own, converters, and an unreadable methods list."""
from flask import Blueprint, Flask

app = Flask(__name__)
api = Blueprint("api", __name__, url_prefix="/api")
child = Blueprint("child", __name__, url_prefix="/child")
other = Blueprint("other", __name__)


@app.route("/")
def index():
    return "ok"


@app.route("/login", methods=["GET", "POST"])
def login():
    return "ok"


@api.get("/orders/<int:order_id>")
def order(order_id):
    return order_id


@child.route("/items/<path:rest>", methods=("DELETE",))
def item(rest):
    return rest


@other.post("/o")
def o():
    return "ok"


@app.route("/dyn", methods=METHODS)
def dyn():
    return "ok"


api.register_blueprint(child)
app.register_blueprint(api, url_prefix="/v1")
app.register_blueprint(imported_bp, url_prefix="/ext")
app.add_url_rule("/ping", view_func=ping)
