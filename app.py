import os, sqlite3, secrets, csv, io, math, tempfile
from functools import wraps
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g, send_file
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE, "stockpro.db"))
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    JSON_SORT_KEYS=False,
    MAX_CONTENT_LENGTH=100 * 1024 * 1024,
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
    CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY, reference TEXT UNIQUE NOT NULL, supplier_id INTEGER REFERENCES suppliers(id), supplier_name TEXT NOT NULL, total REAL NOT NULL, currency TEXT NOT NULL, created_at TEXT NOT NULL, user TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS purchase_items(id INTEGER PRIMARY KEY, purchase_id INTEGER NOT NULL REFERENCES purchases(id) ON DELETE CASCADE, product_id INTEGER NOT NULL REFERENCES products(id), product_name TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0), unit_cost REAL NOT NULL CHECK(unit_cost>=0));
    CREATE TABLE IF NOT EXISTS sale_payments(id INTEGER PRIMARY KEY, sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE, amount REAL NOT NULL CHECK(amount>0), method TEXT NOT NULL, note TEXT DEFAULT '', user TEXT DEFAULT '', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS purchase_payments(id INTEGER PRIMARY KEY, purchase_id INTEGER NOT NULL REFERENCES purchases(id) ON DELETE CASCADE, amount REAL NOT NULL CHECK(amount>0), method TEXT NOT NULL, note TEXT DEFAULT '', user TEXT DEFAULT '', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY, category TEXT NOT NULL, description TEXT NOT NULL, amount REAL NOT NULL CHECK(amount>0), currency TEXT NOT NULL CHECK(currency IN ('USD','CDF')), method TEXT NOT NULL, note TEXT DEFAULT '', user TEXT DEFAULT '', created_at TEXT NOT NULL);
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

def whole_number(value):
    if isinstance(value, bool):
        raise ValueError("Une quantité entière est obligatoire.")
    number = int(value)
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("Une quantité entière est obligatoire.")
    return number

def admin_required():
    if session.get("role") not in ("Administrateur", "Admin"):
        return jsonify(ok=False, message="Accès réservé à l'administrateur."), 403
    return None

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
    return render_template("index.html",stats=stats,categories=categories,alerts=alerts,movements=movements,invoice=invoice,user=session["user"],role=session.get("role","Employé"))

@app.get("/api/products")
@login_required
def products_list():
    q=request.args.get("q","").strip()
    rows=db().execute("SELECT * FROM products WHERE name LIKE ? OR sku LIKE ? OR category LIKE ? ORDER BY name", (f"%{q}%",)*3).fetchall()
    return jsonify([dict(x) for x in rows])

@app.post("/api/products")
@login_required
def product_create():
    denied = admin_required()
    if denied: return denied
    d=request.get_json(silent=True) or request.form
    try:
        quantity=whole_number(d.get("quantity",0)); minimum=whole_number(d.get("min_stock",5))
        purchase=float(d.get("purchase_price",0)); sale=float(d.get("sale_price",0)); currency=d.get("currency","USD")
        if quantity<0 or minimum<0 or not math.isfinite(purchase) or not math.isfinite(sale) or purchase<0 or sale<0 or currency not in ("USD","CDF"):
            raise ValueError("Quantités, prix ou devise invalides.")
        if not d["sku"].strip() or not d["name"].strip() or not d["category"].strip():
            raise ValueError("Référence, nom et catégorie sont obligatoires.")
        db().execute("INSERT INTO products(sku,name,category,brand,quantity,min_stock,purchase_price,sale_price,currency,supplier,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (d["sku"].strip(),d["name"].strip(),d["category"].strip(),d.get("brand",""),quantity,minimum,purchase,sale,currency,d.get("supplier",""),stamp()))
        db().commit(); return jsonify(ok=True,message="Pièce ajoutée au stock.")
    except (KeyError,ValueError,TypeError,sqlite3.IntegrityError) as e: return jsonify(ok=False,message="Vérifiez les champs et assurez-vous que la référence est unique."),400

@app.route("/api/products/<int:pid>",methods=["PUT","DELETE"])
@login_required
def product_detail(pid):
    denied = admin_required()
    if denied: return denied
    conn=db()
    row=conn.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
    if not row: return jsonify(ok=False,message="Pièce introuvable."),404
    if request.method=="DELETE":
        if conn.execute("SELECT 1 FROM sale_items WHERE product_id=?",(pid,)).fetchone() or conn.execute("SELECT 1 FROM movements WHERE product_id=?",(pid,)).fetchone():
            return jsonify(ok=False,message="Suppression impossible : cette pièce possède un historique de ventes ou de mouvements. Conservez sa fiche pour préserver la traçabilité."),409
        conn.execute("DELETE FROM products WHERE id=?",(pid,)); conn.commit(); return jsonify(ok=True)
    d=request.get_json(silent=True) or {}
    try:
        quantity=whole_number(d["quantity"]); minimum=whole_number(d["min_stock"]); purchase=float(d["purchase_price"]); sale=float(d["sale_price"]); currency=d["currency"]
        if quantity<0 or minimum<0 or not math.isfinite(purchase) or not math.isfinite(sale) or purchase<0 or sale<0 or currency not in ("USD","CDF"):
            raise ValueError("Quantités, prix ou devise invalides.")
        sku=str(d["sku"]).strip(); name=str(d["name"]).strip(); category=str(d["category"]).strip()
        if not sku or not name or not category:
            raise ValueError("Référence, nom et catégorie sont obligatoires.")
        if currency != row["currency"] and (
            conn.execute("SELECT 1 FROM sale_items WHERE product_id=? LIMIT 1",(pid,)).fetchone()
            or conn.execute("SELECT 1 FROM purchase_items WHERE product_id=? LIMIT 1",(pid,)).fetchone()
        ):
            raise ValueError("La devise ne peut pas être changée après des achats ou des ventes enregistrés.")
        conn.execute("BEGIN IMMEDIATE")
        current=conn.execute("SELECT quantity FROM products WHERE id=?",(pid,)).fetchone()
        if not current:
            conn.rollback()
            return jsonify(ok=False,message="Pièce introuvable."),404
        delta=quantity-current["quantity"]
        conn.execute("UPDATE products SET sku=?,name=?,category=?,brand=?,quantity=?,min_stock=?,purchase_price=?,sale_price=?,currency=?,supplier=? WHERE id=?",
        (sku,name,category,d.get("brand",""),quantity,minimum,purchase,sale,currency,d.get("supplier",""),pid))
        if delta:
            conn.execute("INSERT INTO movements(product_id,kind,quantity,note,user,created_at) VALUES(?,?,?,?,?,?)",
                         (pid,"Entrée" if delta>0 else "Sortie",abs(delta),"Ajustement manuel depuis la fiche article",session["user"],stamp()))
        conn.commit(); return jsonify(ok=True,message="Pièce modifiée. Tout ajustement de stock a été enregistré dans l'inventaire.")
    except (KeyError,ValueError,TypeError,sqlite3.IntegrityError):
        conn.rollback()
        return jsonify(ok=False,message="Données invalides ou référence déjà utilisée."),400

@app.route("/api/movements", methods=["GET", "POST"])
@login_required
def movement():
    if request.method == 'POST':
        denied = admin_required()
        if denied: return denied
    if request.method == "GET":
        rows = db().execute("SELECT m.*,p.sku,p.name AS product_name FROM movements m JOIN products p ON p.id=m.product_id ORDER BY m.id DESC LIMIT 250").fetchall()
        return jsonify([dict(row) for row in rows])
    d=request.get_json() or {}
    conn=db()
    try:
        pid=int(d["product_id"]); qty=whole_number(d["quantity"]); kind=d["kind"]
        if qty<=0 or kind not in ("Entrée","Sortie"): raise ValueError()
        conn.execute("BEGIN IMMEDIATE")
        p=conn.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
        if not p:
            conn.rollback()
            return jsonify(ok=False,message="Pièce introuvable."),404
        new=p["quantity"]+qty if kind=="Entrée" else p["quantity"]-qty
        if new<0:
            conn.rollback()
            return jsonify(ok=False,message="Stock insuffisant pour cette sortie."),400
        conn.execute("UPDATE products SET quantity=? WHERE id=?",(new,pid))
        conn.execute("INSERT INTO movements(product_id,kind,quantity,note,user,created_at) VALUES(?,?,?,?,?,?)",(pid,kind,qty,d.get("note",""),session["user"],stamp()))
        conn.commit(); return jsonify(ok=True,message=f"{kind} enregistrée.")
    except (KeyError,ValueError,TypeError):
        conn.rollback()
        return jsonify(ok=False,message="Quantité ou type de mouvement invalide."),400

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
    customer_id=d.get("customer_id") or None
    currency=d.get("currency","USD")
    if currency not in ("USD","CDF"): return jsonify(ok=False,message="Devise invalide : choisissez USD ou CDF."),400
    if not isinstance(items,list) or not items: return jsonify(ok=False,message="Ajoutez au moins une pièce à la vente."),400
    conn=db()
    try:
        conn.execute("BEGIN IMMEDIATE"); total=0; prepared=[]; quantities={}
        if customer_id is not None:
            customer_row=conn.execute("SELECT name FROM customers WHERE id=?",(int(customer_id),)).fetchone()
            if not customer_row: raise ValueError("Client sélectionné introuvable.")
            customer=customer_row["name"]
        for item in items:
            pid=int(item["product_id"]); qty=whole_number(item["quantity"])
            if qty<=0: raise ValueError("La quantité doit être supérieure à zéro.")
            quantities[pid]=quantities.get(pid,0)+qty
        for pid,qty in quantities.items():
            p=conn.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
            if not p or p["quantity"]<qty: raise ValueError("Stock insuffisant ou pièce invalide.")
            if p["currency"] != currency: raise ValueError("La devise de la vente doit correspondre à celle de chaque article. Conversion automatique indisponible.")
            price=float(p["sale_price"])
            if not math.isfinite(price) or price<0: raise ValueError("Le prix de vente doit être un montant valide.")
            total+=price*qty; prepared.append((p,qty,price))
        invoice="FAC-"+datetime.now().strftime("%Y%m%d%H%M%S")+"-"+secrets.token_hex(2).upper()
        conn.execute("INSERT INTO sales(invoice,customer_id,customer_name,total,currency,created_at,user) VALUES(?,?,?,?,?,?,?)",(invoice,customer_id,customer,total,currency,stamp(),session["user"]))
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
    payments = conn.execute("SELECT id, amount, method, note, user, created_at FROM sale_payments WHERE sale_id=? ORDER BY id", (sale_id,)).fetchall()
    paid = round(sum(float(payment["amount"]) for payment in payments), 2)
    total = round(float(sale["total"]), 2)
    balance = round(max(0, total - paid), 2)
    status = "Payée" if balance == 0 else ("Partiellement payée" if paid > 0 else "À crédit")
    return jsonify(sale=dict(sale), items=[dict(item) for item in items],
                   payments=[dict(payment) for payment in payments],
                   payment_summary={"paid_amount": paid, "balance": balance, "status": status})


@app.route("/api/sales/<int:sale_id>/payments", methods=["GET", "POST"])
@login_required
def sale_payments(sale_id):
    conn = db()
    sale = conn.execute("SELECT id, total FROM sales WHERE id=?", (sale_id,)).fetchone()
    if not sale:
        return jsonify(ok=False, message="Facture introuvable."), 404
    if request.method == "GET":
        rows = conn.execute("SELECT id, amount, method, note, user, created_at FROM sale_payments WHERE sale_id=? ORDER BY id", (sale_id,)).fetchall()
        paid = round(sum(float(row["amount"]) for row in rows), 2)
        total = round(float(sale["total"]), 2)
        balance = round(max(0, total-paid), 2)
        status = "Payée" if balance == 0 else ("Partiellement payée" if paid > 0 else "À crédit")
        return jsonify(payments=[dict(row) for row in rows],
                       summary={"paid_amount": paid, "balance": balance, "status": status})
    data = request.get_json(silent=True) or {}
    try:
        amount = float(data.get("amount", 0))
        method = str(data.get("method", "Espèces")).strip()
        note = str(data.get("note", "")).strip()
        if not math.isfinite(amount) or amount <= 0 or method not in ("Espèces", "Mobile Money", "Banque", "Carte"):
            raise ValueError("Montant ou mode de paiement invalide.")
        amount = round(amount, 2)
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT total FROM sales WHERE id=?", (sale_id,)).fetchone()
        paid_row = conn.execute("SELECT COALESCE(SUM(amount),0) AS paid FROM sale_payments WHERE sale_id=?", (sale_id,)).fetchone()
        paid = round(float(paid_row["paid"]), 2)
        total = round(float(current["total"]), 2)
        if amount > round(total-paid, 2) + 0.000001:
            conn.rollback()
            return jsonify(ok=False, message="Ce paiement dépasse le solde restant."), 400
        conn.execute("INSERT INTO sale_payments(sale_id,amount,method,note,user,created_at) VALUES(?,?,?,?,?,?)",
                     (sale_id, amount, method, note, session["user"], stamp()))
        conn.commit()
        new_paid = round(paid+amount, 2)
        balance = round(max(0, total-new_paid), 2)
        status = "Payée" if balance == 0 else "Partiellement payée"
        return jsonify(ok=True, message="Paiement enregistré.", paid_amount=new_paid, balance=balance, status=status), 201
    except (ValueError, TypeError, sqlite3.IntegrityError):
        conn.rollback()
        return jsonify(ok=False, message="Montant ou mode de paiement invalide."), 400


@app.get("/api/sales")
@login_required
def sales_list():
    return jsonify([dict(x) for x in db().execute("SELECT * FROM sales ORDER BY id DESC LIMIT 100")])


@app.route("/api/customers/<int:cid>", methods=["PUT","DELETE"])
@login_required
def customer_detail(cid):
    denied = admin_required()
    if denied: return denied
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

@app.route("/api/purchases", methods=["GET", "POST"])
@login_required
def purchases_api():
    if request.method == 'POST':
        denied = admin_required()
        if denied: return denied
    conn = db()
    if request.method == "GET":
        rows = conn.execute("SELECT * FROM purchases ORDER BY id DESC LIMIT 100").fetchall()
        return jsonify([dict(row) for row in rows])
    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    currency = data.get("currency", "USD")
    supplier_id = data.get("supplier_id") or None
    if currency not in ("USD", "CDF") or not isinstance(items, list) or not items:
        return jsonify(ok=False, message="Choisissez une devise et ajoutez au moins un article."), 400
    try:
        conn.execute("BEGIN IMMEDIATE")
        supplier_name = "Fournisseur divers"
        if supplier_id is not None:
            supplier = conn.execute("SELECT name FROM suppliers WHERE id=?", (int(supplier_id),)).fetchone()
            if not supplier:
                raise ValueError("Fournisseur introuvable.")
            supplier_name = supplier["name"]
        quantities = {}
        for item in items:
            pid = int(item["product_id"])
            qty = int(item["quantity"])
            cost = float(item["unit_cost"])
            if qty <= 0 or cost < 0:
                raise ValueError("Quantité et coût d'achat invalides.")
            if pid in quantities:
                quantities[pid][0] += qty
                if quantities[pid][1] != cost:
                    raise ValueError("Un même article ne peut pas avoir deux coûts différents dans un achat.")
            else:
                quantities[pid] = [qty, cost]
        prepared = []
        total = 0
        for pid, (qty, cost) in quantities.items():
            product = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            if not product:
                raise ValueError("Article introuvable.")
            if product["currency"] != currency:
                raise ValueError("La devise de l'achat doit correspondre à celle de l'article.")
            prepared.append((product, qty, cost))
            total += qty * cost
        reference = "ACH-" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(2).upper()
        conn.execute("INSERT INTO purchases(reference,supplier_id,supplier_name,total,currency,created_at,user) VALUES(?,?,?,?,?,?,?)",
                     (reference, supplier_id, supplier_name, total, currency, stamp(), session["user"]))
        purchase_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for product, qty, cost in prepared:
            conn.execute("INSERT INTO purchase_items(purchase_id,product_id,product_name,quantity,unit_cost) VALUES(?,?,?,?,?)",
                         (purchase_id, product["id"], product["name"], qty, cost))
            conn.execute("UPDATE products SET quantity=quantity+?,purchase_price=?,supplier=? WHERE id=?",
                         (qty, cost, supplier_name, product["id"]))
            conn.execute("INSERT INTO movements(product_id,kind,quantity,note,user,created_at) VALUES(?,?,?,?,?,?)",
                         (product["id"], "Entrée", qty, "Achat " + reference, session["user"], stamp()))
        conn.commit()
        return jsonify(ok=True, message="Achat enregistré et stock mis à jour.", reference=reference, total=round(total, 2)), 201
    except (ValueError, KeyError, TypeError, sqlite3.IntegrityError) as error:
        conn.rollback()
        return jsonify(ok=False, message=str(error) or "Achat invalide."), 400

@app.get("/api/purchases/<int:purchase_id>")
@login_required
def purchase_detail(purchase_id):
    conn = db()
    purchase = conn.execute("SELECT * FROM purchases WHERE id=?", (purchase_id,)).fetchone()
    if not purchase:
        return jsonify(ok=False, message="Achat introuvable."), 404
    items = conn.execute("SELECT product_name,quantity,unit_cost,quantity*unit_cost AS subtotal FROM purchase_items WHERE purchase_id=? ORDER BY id",
                         (purchase_id,)).fetchall()
    payments = conn.execute("SELECT id,amount,method,note,user,created_at FROM purchase_payments WHERE purchase_id=? ORDER BY id", (purchase_id,)).fetchall()
    paid = round(sum(float(row["amount"]) for row in payments), 2)
    total = round(float(purchase["total"]), 2)
    balance = round(max(0, total-paid), 2)
    status = "Payé" if balance == 0 else ("Partiellement payé" if paid > 0 else "À payer")
    return jsonify(purchase=dict(purchase), items=[dict(item) for item in items],
                   payments=[dict(row) for row in payments],
                   payment_summary={"paid_amount":paid,"balance":balance,"status":status})

@app.route("/api/purchases/<int:purchase_id>/payments", methods=["GET","POST"])
@login_required
def purchase_payments(purchase_id):
    conn=db()
    purchase=conn.execute("SELECT id,total FROM purchases WHERE id=?",(purchase_id,)).fetchone()
    if not purchase: return jsonify(ok=False,message="Achat introuvable."),404
    if request.method=="GET":
        rows=conn.execute("SELECT id,amount,method,note,user,created_at FROM purchase_payments WHERE purchase_id=? ORDER BY id",(purchase_id,)).fetchall()
        paid=round(sum(float(x["amount"]) for x in rows),2); total=round(float(purchase["total"]),2); balance=round(max(0,total-paid),2)
        return jsonify(payments=[dict(x) for x in rows],summary={"paid_amount":paid,"balance":balance,"status":"Payé" if balance==0 else ("Partiellement payé" if paid>0 else "À payer")})
    data=request.get_json(silent=True) or {}
    try:
        amount=float(data.get("amount",0)); method=str(data.get("method","Espèces")).strip(); note=str(data.get("note","")).strip()
        if not math.isfinite(amount) or amount<=0 or method not in ("Espèces","Mobile Money","Banque","Carte"): raise ValueError()
        amount=round(amount,2); conn.execute("BEGIN IMMEDIATE")
        paid=float(conn.execute("SELECT COALESCE(SUM(amount),0) AS paid FROM purchase_payments WHERE purchase_id=?",(purchase_id,)).fetchone()["paid"])
        total=round(float(conn.execute("SELECT total FROM purchases WHERE id=?",(purchase_id,)).fetchone()["total"]),2)
        if amount>round(total-paid,2)+0.000001:
            conn.rollback(); return jsonify(ok=False,message="Ce règlement dépasse le solde restant."),400
        conn.execute("INSERT INTO purchase_payments(purchase_id,amount,method,note,user,created_at) VALUES(?,?,?,?,?,?)",(purchase_id,amount,method,note,session["user"],stamp()))
        conn.commit(); new_paid=round(paid+amount,2); balance=round(max(0,total-new_paid),2)
        return jsonify(ok=True,message="Règlement fournisseur enregistré.",paid_amount=new_paid,balance=balance),201
    except (ValueError,TypeError,sqlite3.IntegrityError):
        conn.rollback(); return jsonify(ok=False,message="Montant ou mode de règlement invalide."),400


@app.route("/api/expenses", methods=["GET","POST"])
@login_required
def expenses_api():
    denied = admin_required()
    if denied: return denied
    conn=db()
    if request.method=="GET":
        rows=conn.execute("SELECT * FROM expenses ORDER BY id DESC LIMIT 300").fetchall()
        totals=conn.execute("SELECT currency,ROUND(SUM(amount),2) AS total,COUNT(*) AS count FROM expenses GROUP BY currency").fetchall()
        return jsonify(expenses=[dict(x) for x in rows],totals=[dict(x) for x in totals])
    data=request.get_json(silent=True) or {}
    category=str(data.get("category","")).strip(); description=str(data.get("description","")).strip()
    currency=data.get("currency","USD"); method=str(data.get("method","Espèces")).strip(); note=str(data.get("note","")).strip()
    try:
        amount=float(data.get("amount",0))
        if not category or not description or not math.isfinite(amount) or amount<=0 or currency not in ("USD","CDF") or method not in ("Espèces","Mobile Money","Banque","Carte"):
            raise ValueError()
        conn.execute("INSERT INTO expenses(category,description,amount,currency,method,note,user,created_at) VALUES(?,?,?,?,?,?,?,?)",(category,description,round(amount,2),currency,method,note,session["user"],stamp()))
        conn.commit()
        return jsonify(ok=True,message="Dépense enregistrée."),201
    except (ValueError,TypeError,sqlite3.IntegrityError):
        conn.rollback(); return jsonify(ok=False,message="Vérifiez la catégorie, la description, le montant, la devise et le mode de paiement."),400


@app.delete("/api/expenses/<int:expense_id>")
@login_required
def expense_delete(expense_id):
    denied = admin_required()
    if denied: return denied
    conn=db()
    cur=conn.execute("DELETE FROM expenses WHERE id=?",(expense_id,))
    conn.commit()
    if not cur.rowcount: return jsonify(ok=False,message="Dépense introuvable."),404
    return jsonify(ok=True,message="Dépense supprimée.")


@app.route("/api/suppliers", methods=["GET","POST"])
@login_required
def suppliers_api():
    denied = admin_required()
    if denied: return denied
    conn=db()
    if request.method=="GET": return jsonify([dict(x) for x in conn.execute("SELECT * FROM suppliers ORDER BY name")])
    d=request.get_json() or {}; name=d.get("name","").strip()
    if not name: return jsonify(ok=False,message="Le nom du fournisseur est obligatoire."),400
    conn.execute("INSERT INTO suppliers(name,phone,email,address,created_at) VALUES(?,?,?,?,?)",(name,d.get("phone",""),d.get("email",""),d.get("address",""),stamp())); conn.commit()
    return jsonify(ok=True,message="Fournisseur enregistre.")

@app.route("/api/suppliers/<int:sid>", methods=["PUT","DELETE"])
@login_required
def supplier_detail(sid):
    denied = admin_required()
    if denied: return denied
    conn=db()
    if not conn.execute("SELECT id FROM suppliers WHERE id=?",(sid,)).fetchone(): return jsonify(ok=False,message="Fournisseur introuvable."),404
    if request.method=="DELETE":
        conn.execute("DELETE FROM suppliers WHERE id=?",(sid,)); conn.commit(); return jsonify(ok=True)
    d=request.get_json() or {}; name=d.get("name","").strip()
    if not name: return jsonify(ok=False,message="Le nom est obligatoire."),400
    conn.execute("UPDATE suppliers SET name=?,phone=?,email=?,address=? WHERE id=?",(name,d.get("phone",""),d.get("email",""),d.get("address",""),sid)); conn.commit()
    return jsonify(ok=True)

@app.get("/api/finance/summary")
@login_required
def finance_summary():
    denied = admin_required()
    if denied: return denied
    conn=db()
    result={}
    for currency in ("USD","CDF"):
        sales=conn.execute("SELECT COALESCE(SUM(total),0) total,COUNT(*) count FROM sales WHERE currency=?",(currency,)).fetchone()
        collected=conn.execute("SELECT COALESCE(SUM(p.amount),0) total FROM sale_payments p JOIN sales s ON s.id=p.sale_id WHERE s.currency=?",(currency,)).fetchone()["total"]
        purchases=conn.execute("SELECT COALESCE(SUM(total),0) total,COUNT(*) count FROM purchases WHERE currency=?",(currency,)).fetchone()
        supplier_paid=conn.execute("SELECT COALESCE(SUM(p.amount),0) total FROM purchase_payments p JOIN purchases a ON a.id=p.purchase_id WHERE a.currency=?",(currency,)).fetchone()["total"]
        expenses=conn.execute("SELECT COALESCE(SUM(amount),0) total,COUNT(*) count FROM expenses WHERE currency=?",(currency,)).fetchone()
        sales_total=round(float(sales["total"]),2)
        received=round(float(collected),2)
        purchase_total=round(float(purchases["total"]),2)
        paid_suppliers=round(float(supplier_paid),2)
        expense_total=round(float(expenses["total"]),2)
        result[currency]={
            "sales_total":sales_total,"sales_count":sales["count"],
            "received":received,"receivables":round(max(0,sales_total-received),2),
            "purchases_total":purchase_total,"purchases_count":purchases["count"],
            "supplier_paid":paid_suppliers,"payables":round(max(0,purchase_total-paid_suppliers),2),
            "expenses_total":expense_total,
            "cash_balance":round(received-paid_suppliers-expense_total,2)
        }
    recent_expenses=conn.execute("SELECT category,description,amount,currency,created_at FROM expenses ORDER BY id DESC LIMIT 5").fetchall()
    return jsonify(currencies=result,recent_expenses=[dict(x) for x in recent_expenses])


@app.get("/api/backup")
@login_required
def database_backup():
    denied = admin_required()
    if denied: return denied
    with tempfile.TemporaryDirectory() as folder:
        backup_path = os.path.join(folder, "stockpro-backup.db")
        source = db()
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
        with open(backup_path, "rb") as backup_file:
            payload = io.BytesIO(backup_file.read())
    response = send_file(payload, mimetype="application/vnd.sqlite3",
                         as_attachment=True, download_name="stockpro-backup.db")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/backup/restore")
@login_required
def database_restore():
    denied = admin_required()
    if denied: return denied
    uploaded = request.files.get("backup")
    if not uploaded or not uploaded.filename:
        return jsonify(ok=False, message="Sélectionnez un fichier de sauvegarde SQLite."), 400
    temp_path = None
    source = None
    try:
        with tempfile.NamedTemporaryFile(prefix="stockpro-restore-", suffix=".db", delete=False) as temp:
            temp_path = temp.name
            uploaded.save(temp)
        source = sqlite3.connect("file:" + temp_path + "?mode=ro", uri=True)
        check = source.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise ValueError("La sauvegarde est endommagée.")
        required = {"users","products","customers","movements","sales","sale_items",
                    "suppliers","purchases","purchase_items","sale_payments",
                    "purchase_payments","expenses"}
        tables = {row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not required.issubset(tables):
            raise ValueError("Le fichier ne contient pas une base StockPro complète.")
        if source.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("La sauvegarde contient des relations invalides.")
        target = db()
        target.execute("PRAGMA foreign_keys=OFF")
        source.backup(target)
        target.commit()
        target.execute("PRAGMA foreign_keys=ON")
        return jsonify(ok=True, message="Base de données restaurée. Reconnectez-vous avec les comptes présents dans la sauvegarde.")
    except (sqlite3.DatabaseError, OSError, ValueError):
        return jsonify(ok=False, message="Restauration refusée : fichier invalide, incomplet ou endommagé."), 400
    finally:
        if source is not None: source.close()
        if temp_path and os.path.exists(temp_path): os.unlink(temp_path)


@app.get("/api/reports")
@login_required
def reports_api():
    denied = admin_required()
    if denied: return denied
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

@app.get("/api/export/products.csv")
@login_required
def export_products():
    denied = admin_required()
    if denied: return denied
    rows = db().execute("SELECT sku,name,category,brand,quantity,min_stock,purchase_price,sale_price,currency,supplier FROM products ORDER BY category,name").fetchall()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Référence", "Pièce", "Catégorie", "Marque", "Stock", "Seuil minimum", "Prix achat", "Prix vente", "Devise", "Fournisseur"])
    writer.writerows([tuple(row) for row in rows])
    response = app.response_class("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = 'attachment; filename="inventaire-stock-lubumbashi.csv"'
    return response

@app.route("/api/users", methods=["GET", "POST"])
@login_required
def users_api():
    denied = admin_required()
    if denied: return denied
    conn = db()
    if request.method == "GET":
        return jsonify([dict(row) for row in conn.execute("SELECT id,username,role FROM users ORDER BY username")])
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    role = data.get("role", "Employé")
    if len(username) < 3 or len(password) < 12 or role not in ("Administrateur", "Employé"):
        return jsonify(ok=False, message="Identifiant (3 caractères), mot de passe (12 caractères) ou rôle invalide."), 400
    try:
        conn.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                     (username, generate_password_hash(password), role))
        conn.commit()
        return jsonify(ok=True, message="Utilisateur créé.")
    except sqlite3.IntegrityError:
        return jsonify(ok=False, message="Cet identifiant existe déjà."), 409

@app.delete("/api/users/<int:user_id>")
@login_required
def user_delete(user_id):
    denied = admin_required()
    if denied: return denied
    conn = db()
    user = conn.execute("SELECT id,username FROM users WHERE id=?", (user_id,)).fetchone()
    if not user: return jsonify(ok=False, message="Utilisateur introuvable."), 404
    if user["username"] == session["user"]:
        return jsonify(ok=False, message="Vous ne pouvez pas supprimer votre propre compte."), 400
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] <= 1:
        return jsonify(ok=False, message="Le dernier compte ne peut pas être supprimé."), 400
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    return jsonify(ok=True, message="Utilisateur supprimé.")

@app.route("/health")
def health(): return jsonify(status="ok",application="StockPro Moto-Auto")

with app.app_context(): init_db()

if __name__=="__main__":
    app.run(host="127.0.0.1",port=int(os.environ.get("PORT",5000)),debug=os.environ.get("FLASK_DEBUG")=="1")
