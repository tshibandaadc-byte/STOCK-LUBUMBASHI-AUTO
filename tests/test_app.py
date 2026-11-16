import os, tempfile, unittest
from app import app, init_db

class StockProTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        app.config["TESTING"] = True
        os.environ["DATABASE_PATH"] = self.tmp.name
        import app as module
        module.DB_PATH = self.tmp.name
        init_db()
        self.client = app.test_client()
        self.client.post("/login", data={"username": "admin", "password": "Admin@12345"})

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_health(self):
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_dashboard_requires_login(self):
        client = app.test_client()
        self.assertEqual(client.get("/").status_code, 302)

    def test_products_endpoint(self):
        response = self.client.get("/api/products")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.get_json()), 1)

    def test_product_rejects_negative_price_and_invalid_currency(self):
        response = self.client.post("/api/products", json={
            "sku": "BAD-001", "name": "Produit invalide", "category": "Test",
            "quantity": 1, "min_stock": 0, "purchase_price": -2,
            "sale_price": 5, "currency": "USD"
        })
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/products", json={
            "sku": "BAD-002", "name": "Produit invalide", "category": "Test",
            "quantity": 1, "min_stock": 0, "purchase_price": 2,
            "sale_price": 5, "currency": "EUR"
        })
        self.assertEqual(response.status_code, 400)

    def test_stock_entry_and_exit(self):
        product_id = self.client.get("/api/products").get_json()[0]["id"]
        entry = self.client.post("/api/movements", json={
            "product_id": product_id, "quantity": 3, "kind": "Entrée"
        })
        self.assertEqual(entry.status_code, 200)
        exit_response = self.client.post("/api/movements", json={
            "product_id": product_id, "quantity": 2, "kind": "Sortie"
        })
        self.assertEqual(exit_response.status_code, 200)

    def test_inventory_csv_export_requires_login_and_returns_csv(self):
        response = self.client.get("/api/export/products.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.content_type)
        self.assertIn("Référence".encode("utf-8"), response.data)

    def test_inventory_ledger_lists_stock_movements(self):
        product_id = self.client.get("/api/products").get_json()[0]["id"]
        self.client.post("/api/movements", json={
            "product_id": product_id, "quantity": 4, "kind": "Entrée", "note": "Réception fournisseur"
        })
        response = self.client.get("/api/movements")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(row["note"] == "Réception fournisseur" for row in response.get_json()))

    def test_prevent_negative_stock(self):
        product = self.client.get("/api/products").get_json()[0]
        response = self.client.post("/api/movements", json={
            "product_id": product["id"], "quantity": product["quantity"] + 1,
            "kind": "Sortie"
        })
        self.assertEqual(response.status_code, 400)

    def test_sale_reduces_stock_and_creates_invoice(self):
        product = self.client.get("/api/products").get_json()[0]
        response = self.client.post("/api/sales", json={
            "customer": "Client test", "currency": product["currency"],
            "items": [{"product_id": product["id"], "quantity": 2}]
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["invoice"].startswith("FAC-"))
        updated = next(p for p in self.client.get("/api/products").get_json()
                       if p["id"] == product["id"])
        self.assertEqual(updated["quantity"], product["quantity"] - 2)

    def test_sale_rejects_duplicate_lines_that_exceed_stock(self):
        product = self.client.get("/api/products").get_json()[0]
        half = product["quantity"] // 2 + 1
        response = self.client.post("/api/sales", json={
            "customer": "Client test", "currency": product["currency"],
            "items": [
                {"product_id": product["id"], "quantity": half},
                {"product_id": product["id"], "quantity": half}
            ]
        })
        self.assertEqual(response.status_code, 400)
        unchanged = next(p for p in self.client.get("/api/products").get_json()
                         if p["id"] == product["id"])
        self.assertEqual(unchanged["quantity"], product["quantity"])

    def test_sale_links_registered_customer(self):
        customer = self.client.post("/api/customers", json={"name": "Client lié"}).get_json()
        customer_id = self.client.get("/api/customers").get_json()[0]["id"]
        product = self.client.get("/api/products").get_json()[0]
        response = self.client.post("/api/sales", json={
            "customer_id": customer_id, "currency": product["currency"],
            "items": [{"product_id": product["id"], "quantity": 1}]
        })
        self.assertEqual(response.status_code, 200)
        sale = self.client.get("/api/sales").get_json()[0]
        self.assertEqual(sale["customer_id"], customer_id)
        self.assertEqual(sale["customer_name"], "Client lié")

    def test_sale_history_and_invoice_detail(self):
        product = self.client.get("/api/products").get_json()[0]
        created = self.client.post("/api/sales", json={
            "customer": "Client facture", "currency": product["currency"],
            "items": [{"product_id": product["id"], "quantity": 1}]
        })
        self.assertEqual(created.status_code, 200)
        sales = self.client.get("/api/sales").get_json()
        self.assertEqual(len(sales), 1)
        detail = self.client.get("/api/sales/" + str(sales[0]["id"]))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["sale"]["invoice"], created.get_json()["invoice"])
        self.assertEqual(len(detail.get_json()["items"]), 1)

    def test_sale_rejects_currency_mismatch(self):
        product = self.client.get("/api/products").get_json()[0]
        other_currency = "CDF" if product["currency"] == "USD" else "USD"
        response = self.client.post("/api/sales", json={
            "customer": "Client test", "currency": other_currency,
            "items": [{"product_id": product["id"], "quantity": 1}]
        })
        self.assertEqual(response.status_code, 400)

    def test_supplier_create_and_reports(self):
        response = self.client.post("/api/suppliers", json={"name": "Fournisseur test"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.client.get("/api/suppliers").get_json()), 1)
        self.assertEqual(self.client.get("/api/reports").status_code, 200)

    def test_admin_can_create_and_list_users(self):
        response = self.client.post("/api/users", json={
            "username": "vendeur01", "password": "MotDePasseSolide123", "role": "Employé"
        })
        self.assertEqual(response.status_code, 200)
        users = self.client.get("/api/users").get_json()
        self.assertTrue(any(user["username"] == "vendeur01" for user in users))

    def test_employee_cannot_manage_users(self):
        with self.client.session_transaction() as session:
            session["role"] = "Employé"
        response = self.client.get("/api/users")
        self.assertEqual(response.status_code, 403)

    def test_user_creation_rejects_weak_password(self):
        response = self.client.post("/api/users", json={
            "username": "vendeur02", "password": "court", "role": "Employé"
        })
        self.assertEqual(response.status_code, 400)

    def test_user_cannot_delete_own_account(self):
        admin = self.client.get("/api/users").get_json()[0]
        response = self.client.delete("/api/users/" + str(admin["id"]))
        self.assertEqual(response.status_code, 400)

    def test_password_change_requires_current_password(self):
        response = self.client.post("/api/settings/password", json={
            "current_password": "incorrect", "new_password": "MotDePasseTresLong123"
        })
        self.assertEqual(response.status_code, 400)

if __name__ == "__main__":
    unittest.main()
