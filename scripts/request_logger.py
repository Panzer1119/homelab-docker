from flask import Flask, request

app = Flask(__name__)


@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"])
def catch_all(path):
    print("\n--- REQUEST ---")
    print(f"{request.method} /{path}")
    print("Headers:", dict(request.headers))
    print("Query:", request.args.to_dict(flat=False))
    print("Form:", request.form.to_dict(flat=False))
    print("JSON:", request.get_json(silent=True))
    print("Body:", request.get_data(as_text=True))
    print("----------------\n")

    return "OK\n", 200


app.run(host="0.0.0.0", port=8080)
