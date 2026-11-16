import os, sqlite3, secrets
from functools import wraps
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE, "stockpro.db"))
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    JSON_SORT_KEYS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
)

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(_=None):
    conn = g.pop("db", None)
    if conn: conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'Administrateur');
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL, brand TEXT DEFAULT '', quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0), min_stock INTEGER NOT NULL DEFAULT 5 CHECK(min_stock>=0), purchase_price REAL NOT NULL DEFAULT 0, sale_price REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'USD', supplier TEXT DEFAULT '', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT DEFAULT '', address TEXT DEFAULT '', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id), kind TEXT NOT NULL CHECK(kind IN ('Entrée','Sortie')), quantity INTEGER NOT NULL, note TEXT DEFAULT '', user TEXT DEFAULT '', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY, invoice TEXT UNIQUE NOT NULL, customer_id INTEGER REFERENCES customers(id), customer_name TEXT NOT NULL, total REAL NOT NULL, currency TEXT NOT NULL, created_at TEXT NOT NULL, user TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS sale_items(id INTEGER PRIMARY KEY, sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE, product_id INTEGER NOT NULL REFERENCES products(id), product_name TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price REAL NOT NULL);\n    CREATE TABLE IF NOT EXISTS suppliers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT DEFAULT '', email TEXT DEFAULT '', address TEXT DEFAULT '', created_at TEXT NOT NULL);
    """)
    if not conn.execute("SELECT id FROM users LIMIT 1").fetchone():
        conn.execute("INSERT INTO users(username,password_hash) VALUES(?,?)", ("admin", generate_password_hash(os.environ.get("ADMIN_PASSWORD","Admin@12345"))))
    if not conn.execute("SELECT id FROM products LIMIT 1").fetchone():
        now=datetime.now().isoformat(timespec="seconds")
        demo=[("MOTO-001","Pneu moto","Pneus","",35,5,18,28,"USD"),("VELO-001","Frein vélo","Freins","",22,5,4,8,"USD"),("AUTO-001","Batterie 12V","Batteries","",8,3,45,65,"USD"),("AUTO-002","Filtre à huile","Filtres","",14,5,6,10,"USD"),("MOTO-002","Moteur moto","Moteurs","",3,2,180,240,"USD"),("AUTO-003","Plaquettes de frein","Freins","",17,5,12,20,"USD")]
        conn.executemany("INSERT INTO products(sku,name,category,brand,quantity,min_stock,purchase_price,sale_price,currency,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",[(a,b,c,d,e,f,g,h,i,now) for a,b,c,d,e,f,g,h,i in demo])
    conn.commit(); conn.close()

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if "user" not in session: return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped

def stamp(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        user=db().execute("SELECT * FROM users WHERE username=?",(request.form.get("username","").strip(),)).fetchone()
        if user and check_password_hash(user["password_hash"],request.form.get("password","")):
            session.clear(); session["user"]=user["username"]; session["role"]=user["role"]
            session.permanent = True
            return redirect(url_for("dashboard"))
        flash("Identifiants incorrects. Réessayez.","error")
    return render_template("login.html")

@app.post("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    conn=db()
    stats={
      "products":conn.execute("SELECT COUNT(*) n FROM products").fetchone()["n"],
      "quantity":conn.execute("SELECT COALESCE(SUM(quantity),0) n FROM products").fetchone()["n"],
      "entries":conn.execute("SELECT COALESCE(SUM(quantity),0) n FROM movements WHERE kind='Entrée' AND substr(created_at,1,7)=?",(datetime.now().strftime("%Y-%m"),)).fetchone()["n"],
      "exits":conn.execute("SELECT COALESCE(SUM(quantity),0) n FROM movements WHERE kind='Sortie' AND substr(created_at,1,7)=?",(datetime.now().strftime("%Y-%m"),)).fetchone()["n"],
      "alerts":conn.execute("SELECT COUNT(*) n FROM products WHERE quantity<=min_stock").fetchone()["n"],
      "sales":conn.execute("SELECT COALESCE(SUM(total),0) n FROM sales WHERE substr(created_at,1,7)=?",(datetime.now().strftime("%Y-%m"),)).fetchone()["n"],
      "customers":conn.execute("SELECT COUNT(*) n FROM customers").fetchone()["n"]
    }
    categories=conn.execute("SELECT category,COUNT(*) count,COALESCE(SUM(quantity),0) quantity FROM products GROUP BY category ORDER BY category").fetchall()
    alerts=conn.execute("SELECT * FROM products WHERE quantity<=min_stock ORDER BY quantity ASC LIMIT 8").fetchall()
    movements=conn.execute("SELECT m.*,p.name product FROM movements m JOIN products p ON p.id=m.product_id ORDER BY m.id DESC LIMIT 8").fetchall()
    invoice=conn.execute("SELECT * FROM sales ORDER BY id DESC LIMIT 1").fetchone()
    return render_template("index.html",stats=stats,categories=categories,alerts=alerts,movements=movements,invoice=invoice,user=session["user"])

@app.get("/api/products")
@login_required
def products_list():
    q=request.args.get("q","").strip()
    rows=db().execute("SELECT * FROM products WHERE name LIKE ? OR sku LIKE ? OR category LIKE ? ORDER BY name", (f"%{q}%",)*3).fetchall()
    return jsonify([dict(x) for x in rows])

@app.post("/api/products")
@login_required
def product_create():
    d=request.get_json(silent=True) or request.form
    try:
        quantity=int(d.get("quantity",0)); minimum=int(d.get("min_stock",5))
        purchase=float(d.get("purchase_price",0)); sale=float(d.get("sale_price",0)); currency=d.get("currency","USD")
        if quantity<0 or minimum<0 or purchase<0 or sale<0 or currency not in ("USD","CDF"):
            raise ValueError("Quantités, prix ou devise invalides.")
        if not d["sku"].strip() or not d["name"].strip() or not d["category"].strip():
            raise ValueError("Référence, nom et catégorie sont obligatoires.")
        db().execute("INSERT INTO products(sku,name,category,brand,quantity,min_stock,purchase_price,sale_price,currency,supplier,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (d["sku"].strip(),d["name"].strip(),d["category"].strip(),d.get("brand",""),quantity,minimum,purchase,sale,currency,d.get("supplier",""),stamp()))
        db().commit(); return jsonify(ok=True,message="Pièce ajoutée au stock.")
    except (KeyError,ValueError,sqlite3.IntegrityError) as e: return jsonify(ok=False,message="Vérifiez les champs et assurez-vous que la référence est unique."),400

@app.route("/api/products/<int:pid>",methods=["PUT","DELETE"])
@login_required
def product_detail(pid):
    conn=db()
    row=conn.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
    if not row: return jsonify(ok=False,message="Pièce introuvable."),404
    if request.method=="DELETE":
        if conn.execute("SELECT 1 FROM sale_items WHERE product_id=?",(pid,)).fetchone(): return jsonify(ok=False,message="Suppression impossible : cette pièce figure dans une vente. Mettez son stock à zéro."),409
        conn.execute("DELETE FROM products WHERE id=?",(pid,)); conn.commit(); return jsonify(ok=True)
    d=request.get_json() or {}
    try:
        quantity=int(d["quantity"]); minimum=int(d["min_stock"]); purchase=float(d["purchase_price"]); sale=float(d["sale_price"]); currency=d["currency"]
        if quantity<0 or minimum<0 or purchase<0 or sale<0 or currency not in ("USD","CDF"):
            raise ValueError("Quantités, prix ou devise invalides.")
        if not d["sku"].strip() or not d["name"].strip() or not d["category"].strip():
            raise ValueError("Référence, nom et catégorie sont obligatoires.")
        conn.execute("UPDATE products SET sku=?,name=?,category=?,brand=?,quantity=?,min_stock=?,purchase_price=?,sale_price=?,currency=?,supplier=? WHERE id=?",
        (d["sku"].strip(),d["name"].strip(),d["category"].strip(),d.get("brand",""),quantity,minimum,purchase,sale,currency,d.get("supplier",""),pid))
        conn.commit(); return jsonify(ok=True,message="Pièce modifiée.")
    except (KeyError,ValueError,sqlite3.IntegrityError): return jsonify(ok=False,message="Données invalides ou référence déjà utilisée."),400

@app.post("/api/movements")
@login_required
def movement():
    d=request.get_json() or {}
    try:
        pid=int(d["product_id"]); qty=int(d["quantity"]); kind=d["kind"]
        if qty<=0 or kind not in ("Entrée","Sortie"): raise ValueError()
        conn=db(); p=conn.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
        if not p: return jsonify(ok=False,message="Pièce introuvable."),404
        new=p["quantity"]+qty if kind=="Entrée" else p["quantity"]-qty
        if new<0: return jsonify(ok=False,message="Stock insuffisant pour cette sortie."),400
        conn.execute("UPDATE products SET quantity=? WHERE id=?",(new,pid))
        conn.execute("INSERT INTO movements(product_id,kind,quantity,note,user,created_at) VALUES(?,?,?,?,?,?)",(pid,kind,qty,d.get("note",""),session["user"],stamp()))
        conn.commit(); return jsonify(ok=True,message=f"{kind} enregistrée.")
    except (KeyError,ValueError): return jsonify(ok=False,message="Quantité ou type de mouvement invalide."),400

@app.get("/api/customers")
@login_required
def customers_list():
    return jsonify([dict(x) for x in db().execute("SELECT * FROM customers ORDER BY name")])

@app.post("/api/customers")
@login_required
def customer_create():
    d=request.get_json() or {}
    name=d.get("name","").strip()
    if not name: return jsonify(ok=False,message="Le nom du client est obligatoire."),400
    db().execute("INSERT INTO customers(name,phone,address,created_at) VALUES(?,?,?,?)",(name,d.get("phone",""),d.get("address",""),stamp())); db().commit()
    return jsonify(ok=True,message="Client enregistré.")

@app.get("/api/alerts")
@login_required
def alerts_list():
    return jsonify([dict(x) for x in db().execute("SELECT * FROM products WHERE quantity<=min_stock ORDER BY quantity")])

@app.post("/api/sales")
@login_required
def sale_create():
    d=request.get_json() or {}; items=d.get("items",[]); customer=(d.get("customer") or "Client comptoir").strip()
    currency=d.get("currency","USD")
    if currency not in ("USD","CDF"): return jsonify(ok=False,message="Devise invalide : choisissez USD ou CDF."),400
    if not isinstance(items,list) or not items: return jsonify(ok=False,message="Ajoutez au moins une pièce à la vente."),400
    conn=db()
    try:
        conn.execute("BEGIN IMMEDIATE"); total=0; prepared=[]; quantities={}
        for item in items:
            pid=int(item["product_id"]); qty=int(item["quantity"])
            if qty<=0: raise ValueError("La quantité doit être supérieure à zéro.")
            quantities[pid]=quantities.get(pid,0)+qty
        for pid,qty in quantities.items():
            p=conn.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
            if not p or p["quantity"]<qty: raise ValueError("Stock insuffisant ou pièce invalide.")
            if p["currency"] != currency: raise ValueError("La devise de la vente doit correspondre à celle de chaque article. Conversion automatique indisponible.")
            price=float(p["sale_price"])
            if price<0: raise ValueError("Le prix de vente ne peut pas être négatif.")
            total+=price*qty; prepared.append((p,qty,price))
        invoice="FAC-"+datetime.now().strftime("%Y%m%d%H%M%S")+"-"+secrets.token_hex(2).upper()
        conn.execute("INSERT INTO sales(invoice,customer_name,total,currency,created_at,user) VALUES(?,?,?,?,?,?)",(invoice,customer,total,d.get("currency","USD"),stamp(),session["user"]))
        sid=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for p,qty,price in prepared:
            conn.execute("INSERT INTO sale_items(sale_id,product_id,product_name,quantity,unit_price) VALUES(?,?,?,?,?)",(sid,p["id"],p["name"],qty,price))
            conn.execute("UPDATE products SET quantity=quantity-? WHERE id=?",(qty,p["id"]))
            conn.execute("INSERT INTO movements(product_id,kind,quantity,note,user,created_at) VALUES(?,?,?,?,?,?)",(p["id"],"Sortie",qty,f"Vente {invoice}",session["user"],stamp()))
        conn.commit(); return jsonify(ok=True,message="Vente enregistrée.",invoice=invoice,total=round(total,2))
    except (ValueError,KeyError,TypeError,sqlite3.IntegrityError) as e:
        conn.rollback(); return jsonify(ok=False,message=str(e) or "Vente invalide."),400

@app.get("/api/sales/<int:sale_id>")
@login_required
def sale_detail(sale_id):
    conn = db()
    sale = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    if not sale:
        return jsonify(ok=False, message="Facture introuvable."), 404
    items = conn.execute(
        "SELECT product_name, quantity, unit_price, quantity*unit_price AS subtotal "
        "FROM sale_items WHERE sale_id=? ORDER BY id", (sale_id,)
    ).fetchall()
    return jsonify(sale=dict(sale), items=[dict(item) for item in items])


@app.get("/api/sales")
@login_required
def sales_list():
    return jsonify([dict(x) for x in db().execute("SELECT * FROM sales ORDER BY id DESC LIMIT 100")])


@app.route("/api/customers/<int:cid>", methods=["PUT","DELETE"])
@login_required
def customer_detail(cid):
    conn=db()
    if not conn.execute("SELECT id FROM customers WHERE id=?",(cid,)).fetchone():
        return jsonify(ok=False,message="Client introuvable."),404
    if request.method=="DELETE":
        if conn.execute("SELECT 1 FROM sales WHERE customer_id=?",(cid,)).fetchone():
            return jsonify(ok=False,message="Client lie a une vente; historique protege."),409
        conn.execute("DELETE FROM customers WHERE id=?",(cid,)); conn.commit()
        return jsonify(ok=True)
    d=request.get_json() or {}; name=d.get("name","").strip()
    if not name: return jsonify(ok=False,message="Le nom est obligatoire."),400
    conn.execute("UPDATE customers SET name=?,phone=?,address=? WHERE id=?",(name,d.get("phone",""),d.get("address",""),cid)); conn.commit()
    return jsonify(ok=True)

@app.route("/api/suppliers", methods=["GET","POST"])
@login_required
def suppliers_api():
    conn=db()
    if request.method=="GET": return jsonify([dict(x) for x in conn.execute("SELECT * FROM suppliers ORDER BY name")])
    d=request.get_json() or {}; name=d.get("name","").strip()
    if not name: return jsonify(ok=False,message="Le nom du fournisseur est obligatoire."),400
    conn.execute("INSERT INTO suppliers(name,phone,email,address,created_at) VALUES(?,?,?,?,?)",(name,d.get("phone",""),d.get("email",""),d.get("address",""),stamp())); conn.commit()
    return jsonify(ok=True,message="Fournisseur enregistre.")

@app.route("/api/suppliers/<int:sid>", methods=["PUT","DELETE"])
@login_required
def supplier_detail(sid):
    conn=db()
    if not conn.execute("SELECT id FROM suppliers WHERE id=?",(sid,)).fetchone(): return jsonify(ok=False,message="Fournisseur introuvable."),404
    if request.method=="DELETE":
        conn.execute("DELETE FROM suppliers WHERE id=?",(sid,)); conn.commit(); return jsonify(ok=True)
    d=request.get_json() or {}; name=d.get("name","").strip()
    if not name: return jsonify(ok=False,message="Le nom est obligatoire."),400
    conn.execute("UPDATE suppliers SET name=?,phone=?,email=?,address=? WHERE id=?",(name,d.get("phone",""),d.get("email",""),d.get("address",""),sid)); conn.commit()
    return jsonify(ok=True)

@app.get("/api/reports")
@login_required
def reports_api():
    conn=db()
    monthly=conn.execute("SELECT substr(created_at,1,7) month,currency,ROUND(SUM(total),2) total,COUNT(*) count FROM sales GROUP BY month,currency ORDER BY month DESC LIMIT 24").fetchall()
    categories=conn.execute("SELECT category,COUNT(*) products,SUM(quantity) quantity FROM products GROUP BY category ORDER BY quantity DESC").fetchall()
    movements=conn.execute("SELECT kind,COUNT(*) operations,SUM(quantity) quantity FROM movements GROUP BY kind").fetchall()
    return jsonify(monthly=[dict(x) for x in monthly],categories=[dict(x) for x in categories],movements=[dict(x) for x in movements])

@app.post("/api/settings/password")
@login_required
def change_password():
    d=request.get_json() or {}; old=d.get("current_password",""); new=d.get("new_password","")
    if len(new)<12: return jsonify(ok=False,message="Le nouveau mot de passe doit contenir au moins 12 caracteres."),400
    conn=db(); user=conn.execute("SELECT * FROM users WHERE username=?",(session["user"],)).fetchone()
    if not user or not check_password_hash(user["password_hash"],old): return jsonify(ok=False,message="Mot de passe actuel incorrect."),400
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",(generate_password_hash(new),user["id"])); conn.commit()
    return jsonify(ok=True,message="Mot de passe modifie.")

@app.route("/health")
def health(): return jsonify(status="ok",application="StockPro Moto-Auto")

with app.app_context(): init_db()

if __name__=="__main__":
    app.run(host="127.0.0.1",port=int(os.environ.get("PORT",5000)),debug=os.environ.get("FLASK_DEBUG")=="1")
