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

    def test_password_change_requires_current_password(self):
        response = self.client.post("/api/settings/password", json={
            "current_password": "incorrect", "new_password": "MotDePasseTresLong123"
        })
        self.assertEqual(response.status_code, 400)

if __name__ == "__main__":
    unittest.main()
