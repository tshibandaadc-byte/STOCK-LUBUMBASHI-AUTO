import os, tempfile, unittest, io, subprocess, sys
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

    def test_security_headers_are_present(self):
        response = self.client.get("/health")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(response.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")

    def test_production_refuses_missing_secret_key(self):
        env = os.environ.copy()
        env.pop("SECRET_KEY", None)
        env["APP_ENV"] = "production"
        result = subprocess.run(
            [sys.executable, "-c", "import app"],
            env=env, capture_output=True, text=True, timeout=15
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SECRET_KEY", result.stderr)

    def test_health(self):
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_admin_can_download_and_restore_database_backup(self):
        original = self.client.get("/api/products").get_json()
        backup = self.client.get("/api/backup")
        self.assertEqual(backup.status_code, 200)
        self.assertIn("stockpro-backup.db", backup.headers.get("Content-Disposition", ""))
        self.assertTrue(backup.data.startswith(b"SQLite format 3"))
        self.client.delete("/api/products/" + str(original[0]["id"]))
        self.assertEqual(len(self.client.get("/api/products").get_json()), len(original) - 1)
        restored = self.client.post("/api/backup/restore", data={
            "backup": (io.BytesIO(backup.data), "stockpro-backup.db")
        }, content_type="multipart/form-data")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(len(self.client.get("/api/products").get_json()), len(original))

    def test_restore_rejects_invalid_file_without_changing_database(self):
        before = len(self.client.get("/api/products").get_json())
        response = self.client.post("/api/backup/restore", data={
            "backup": (io.BytesIO(b"not a database"), "bad.db")
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.client.get("/api/products").get_json()), before)

    def test_employee_cannot_backup_or_restore_database(self):
        with self.client.session_transaction() as session:
            session["role"] = "Employé"
        self.assertEqual(self.client.get("/api/backup").status_code, 403)
        response = self.client.post("/api/backup/restore", data={
            "backup": (io.BytesIO(b"fake"), "backup.db")
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 403)

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

    def test_product_update_persists_valid_changes(self):
        product = self.client.get("/api/products").get_json()[0]
        response = self.client.put("/api/products/" + str(product["id"]), json={
            "sku": product["sku"], "name": "Pièce modifiée", "category": product["category"],
            "brand": "Test", "quantity": product["quantity"], "min_stock": product["min_stock"],
            "purchase_price": product["purchase_price"], "sale_price": product["sale_price"],
            "currency": product["currency"], "supplier": "Fournisseur test"
        })
        self.assertEqual(response.status_code, 200)
        updated = next(p for p in self.client.get("/api/products").get_json() if p["id"] == product["id"])
        self.assertEqual(updated["name"], "Pièce modifiée")

    def test_manual_product_stock_adjustment_is_audited(self):
        product = self.client.get("/api/products").get_json()[0]
        new_quantity = product["quantity"] + 6
        response = self.client.put("/api/products/" + str(product["id"]), json={
            "sku": product["sku"], "name": product["name"], "category": product["category"],
            "brand": product["brand"], "quantity": new_quantity, "min_stock": product["min_stock"],
            "purchase_price": product["purchase_price"], "sale_price": product["sale_price"],
            "currency": product["currency"], "supplier": product["supplier"]
        })
        self.assertEqual(response.status_code, 200)
        movements = self.client.get("/api/movements").get_json()
        self.assertTrue(any(m["note"] == "Ajustement manuel depuis la fiche article" and m["quantity"] == 6 for m in movements))
        deleted = self.client.delete("/api/products/" + str(product["id"]))
        self.assertEqual(deleted.status_code, 409)

    def test_customer_update_and_protected_delete(self):
        self.client.post("/api/customers", json={"name": "Client initial"})
        customer = self.client.get("/api/customers").get_json()[0]
        updated = self.client.put("/api/customers/" + str(customer["id"]), json={
            "name": "Client modifié", "phone": "+243000000", "address": "Lubumbashi"
        })
        self.assertEqual(updated.status_code, 200)
        product = self.client.get("/api/products").get_json()[0]
        self.client.post("/api/sales", json={
            "customer_id": customer["id"], "currency": product["currency"],
            "items": [{"product_id": product["id"], "quantity": 1}]
        })
        deleted = self.client.delete("/api/customers/" + str(customer["id"]))
        self.assertEqual(deleted.status_code, 409)

    def test_supplier_update_and_delete(self):
        self.client.post("/api/suppliers", json={"name": "Fournisseur initial"})
        supplier = self.client.get("/api/suppliers").get_json()[0]
        updated = self.client.put("/api/suppliers/" + str(supplier["id"]), json={
            "name": "Fournisseur modifié", "phone": "+243000000",
            "email": "test@example.com", "address": "Lubumbashi"
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.client.get("/api/suppliers").get_json()[0]["name"], "Fournisseur modifié")
        deleted = self.client.delete("/api/suppliers/" + str(supplier["id"]))
        self.assertEqual(deleted.status_code, 200)

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

    def test_stock_quantities_must_be_whole_numbers(self):
        product = self.client.get("/api/products").get_json()[0]
        response = self.client.post("/api/movements", json={
            "product_id": product["id"], "quantity": 1.5, "kind": "Entrée"
        })
        self.assertEqual(response.status_code, 400)
        unchanged = next(p for p in self.client.get("/api/products").get_json() if p["id"] == product["id"])
        self.assertEqual(unchanged["quantity"], product["quantity"])

    def test_product_rejects_non_finite_prices(self):
        response = self.client.post("/api/products", json={
            "sku": "NAN-001", "name": "Prix invalide", "category": "Test",
            "quantity": 1, "min_stock": 0, "purchase_price": float("nan"),
            "sale_price": 5, "currency": "USD"
        })
        self.assertEqual(response.status_code, 400)

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

    def test_purchase_receipt_increases_stock_and_records_history(self):
        self.client.post("/api/suppliers", json={"name": "Grossiste Lubumbashi"})
        supplier_id = self.client.get("/api/suppliers").get_json()[0]["id"]
        product = self.client.get("/api/products").get_json()[0]
        before = product["quantity"]
        response = self.client.post("/api/purchases", json={
            "supplier_id": supplier_id, "currency": product["currency"],
            "items": [{"product_id": product["id"], "quantity": 7, "unit_cost": 11.5}]
        })
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.get_json()["reference"].startswith("ACH-"))
        updated = next(p for p in self.client.get("/api/products").get_json() if p["id"] == product["id"])
        self.assertEqual(updated["quantity"], before + 7)
        self.assertEqual(updated["purchase_price"], 11.5)
        purchases = self.client.get("/api/purchases").get_json()
        self.assertEqual(len(purchases), 1)
        detail = self.client.get("/api/purchases/" + str(purchases[0]["id"])).get_json()
        self.assertEqual(detail["items"][0]["quantity"], 7)

    def test_purchase_rejects_currency_mismatch_without_stock_change(self):
        product = self.client.get("/api/products").get_json()[0]
        before = product["quantity"]
        wrong_currency = "CDF" if product["currency"] == "USD" else "USD"
        response = self.client.post("/api/purchases", json={
            "currency": wrong_currency,
            "items": [{"product_id": product["id"], "quantity": 3, "unit_cost": 5}]
        })
        self.assertEqual(response.status_code, 400)
        unchanged = next(p for p in self.client.get("/api/products").get_json() if p["id"] == product["id"])
        self.assertEqual(unchanged["quantity"], before)

    def test_purchase_supplier_payment_partial_and_full(self):
        self.client.post("/api/suppliers", json={"name": "Fournisseur à régler"})
        supplier_id = self.client.get("/api/suppliers").get_json()[0]["id"]
        product = self.client.get("/api/products").get_json()[0]
        purchase = self.client.post("/api/purchases", json={
            "supplier_id": supplier_id, "currency": product["currency"],
            "items": [{"product_id": product["id"], "quantity": 2, "unit_cost": 10}]
        })
        self.assertEqual(purchase.status_code, 201)
        purchase_id = self.client.get("/api/purchases").get_json()[0]["id"]
        total = self.client.get("/api/purchases/" + str(purchase_id)).get_json()["purchase"]["total"]
        first = self.client.post("/api/purchases/" + str(purchase_id) + "/payments", json={
            "amount": 5, "method": "Banque", "note": "Acompte"
        })
        self.assertEqual(first.status_code, 201)
        detail = self.client.get("/api/purchases/" + str(purchase_id)).get_json()
        self.assertEqual(detail["payment_summary"]["status"], "Partiellement payé")
        self.assertEqual(detail["payment_summary"]["balance"], round(total - 5, 2))
        final = self.client.post("/api/purchases/" + str(purchase_id) + "/payments", json={
            "amount": round(total - 5, 2), "method": "Mobile Money"
        })
        self.assertEqual(final.status_code, 201)
        detail = self.client.get("/api/purchases/" + str(purchase_id)).get_json()
        self.assertEqual(detail["payment_summary"]["balance"], 0)
        self.assertEqual(detail["payment_summary"]["status"], "Payé")

    def test_supplier_payment_rejects_overpayment(self):
        product = self.client.get("/api/products").get_json()[0]
        self.client.post("/api/purchases", json={
            "currency": product["currency"],
            "items": [{"product_id": product["id"], "quantity": 1, "unit_cost": 3}]
        })
        purchase_id = self.client.get("/api/purchases").get_json()[0]["id"]
        total = self.client.get("/api/purchases/" + str(purchase_id)).get_json()["purchase"]["total"]
        response = self.client.post("/api/purchases/" + str(purchase_id) + "/payments", json={
            "amount": total + 1, "method": "Espèces"
        })
        self.assertEqual(response.status_code, 400)

    def test_expense_create_list_and_validate(self):
        created = self.client.post("/api/expenses", json={
            "category": "Transport", "description": "Livraison pièces",
            "amount": 25.5, "currency": "USD", "method": "Mobile Money"
        })
        self.assertEqual(created.status_code, 201)
        listing = self.client.get("/api/expenses").get_json()
        self.assertEqual(len(listing["expenses"]), 1)
        self.assertEqual(listing["expenses"][0]["amount"], 25.5)
        self.assertEqual(listing["totals"][0]["currency"], "USD")
        invalid = self.client.post("/api/expenses", json={
            "category": "Autre", "description": "Montant invalide",
            "amount": float("nan"), "currency": "USD", "method": "Espèces"
        })
        self.assertEqual(invalid.status_code, 400)
        deleted = self.client.delete("/api/expenses/" + str(listing["expenses"][0]["id"]))
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(len(self.client.get("/api/expenses").get_json()["expenses"]), 0)

    def test_finance_summary_separates_currencies_and_tracks_balances(self):
        product = self.client.get("/api/products").get_json()[0]
        sale_response = self.client.post("/api/sales", json={
            "customer": "Client tableau financier", "currency": product["currency"],
            "items": [{"product_id": product["id"], "quantity": 1}]
        })
        self.assertEqual(sale_response.status_code, 200)
        sale_id = self.client.get("/api/sales").get_json()[0]["id"]
        sale_total = float(self.client.get("/api/sales/" + str(sale_id)).get_json()["sale"]["total"])
        self.client.post("/api/sales/" + str(sale_id) + "/payments", json={
            "amount": round(sale_total / 2, 2), "method": "Espèces"
        })
        self.client.post("/api/expenses", json={
            "category": "Transport", "description": "Frais USD",
            "amount": 10, "currency": product["currency"], "method": "Espèces"
        })
        other_currency = "CDF" if product["currency"] == "USD" else "USD"
        self.client.post("/api/expenses", json={
            "category": "Autre", "description": "Frais autre devise",
            "amount": 7000, "currency": other_currency, "method": "Banque"
        })
        summary = self.client.get("/api/finance/summary")
        self.assertEqual(summary.status_code, 200)
        data = summary.get_json()["currencies"]
        self.assertEqual(data[product["currency"]]["sales_count"], 1)
        self.assertEqual(data[product["currency"]]["received"], round(sale_total / 2, 2))
        self.assertGreater(data[product["currency"]]["receivables"], 0)
        self.assertEqual(data[product["currency"]]["expenses_total"], 10)
        self.assertEqual(data[other_currency]["expenses_total"], 7000)

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

    def test_employee_is_blocked_from_sensitive_admin_operations(self):
        with self.client.session_transaction() as session:
            session["role"] = "Employé"
        checks = [
            self.client.post("/api/products", json={"sku": "EMP-01", "name": "Test", "category": "Test"}),
            self.client.post("/api/movements", json={"product_id": 1, "quantity": 1, "kind": "Entrée"}),
            self.client.post("/api/purchases", json={"items": []}),
            self.client.post("/api/expenses", json={"category": "Test", "description": "Test", "amount": 1, "currency": "USD"}),
            self.client.get("/api/expenses"),
            self.client.get("/api/suppliers"),
            self.client.get("/api/finance/summary"),
            self.client.get("/api/reports"),
            self.client.get("/api/export/products.csv"),
        ]
        self.assertTrue(all(response.status_code == 403 for response in checks))

    def test_employee_can_still_create_a_sale(self):
        with self.client.session_transaction() as session:
            session["role"] = "Employé"
        product = self.client.get("/api/products").get_json()[0]
        response = self.client.post("/api/sales", json={
            "customer": "Vente employé", "currency": product["currency"],
            "items": [{"product_id": product["id"], "quantity": 1}]
        })
        self.assertEqual(response.status_code, 200)

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

    def test_invoice_partial_and_full_payments(self):
        product = self.client.get("/api/products").get_json()[0]
        created = self.client.post("/api/sales", json={
            "customer": "Client paiement", "currency": product["currency"],
            "items": [{"product_id": product["id"], "quantity": 1}]
        })
        sale = self.client.get("/api/sales").get_json()[0]
        sale_id = sale["id"]
        total = float(sale["total"])
        first = round(total / 2, 2)
        response = self.client.post("/api/sales/" + str(sale_id) + "/payments", json={
            "amount": first, "method": "Mobile Money", "note": "Acompte"
        })
        self.assertEqual(response.status_code, 201)
        detail = self.client.get("/api/sales/" + str(sale_id)).get_json()
        self.assertEqual(detail["payment_summary"]["status"], "Partiellement payée")
        self.assertEqual(len(detail["payments"]), 1)
        remaining = detail["payment_summary"]["balance"]
        if remaining > 0:
            final = self.client.post("/api/sales/" + str(sale_id) + "/payments", json={
                "amount": remaining, "method": "Espèces"
            })
            self.assertEqual(final.status_code, 201)
        detail = self.client.get("/api/sales/" + str(sale_id)).get_json()
        self.assertEqual(detail["payment_summary"]["balance"], 0)
        self.assertEqual(detail["payment_summary"]["status"], "Payée")

    def test_invoice_payment_rejects_overpayment_and_invalid_values(self):
        product = self.client.get("/api/products").get_json()[0]
        self.client.post("/api/sales", json={
            "customer": "Client paiement", "currency": product["currency"],
            "items": [{"product_id": product["id"], "quantity": 1}]
        })
        sale_id = self.client.get("/api/sales").get_json()[0]["id"]
        total = float(self.client.get("/api/sales/" + str(sale_id)).get_json()["sale"]["total"])
        over = self.client.post("/api/sales/" + str(sale_id) + "/payments", json={
            "amount": total + 1, "method": "Espèces"
        })
        self.assertEqual(over.status_code, 400)
        invalid = self.client.post("/api/sales/" + str(sale_id) + "/payments", json={
            "amount": -5, "method": "Crypto"
        })
        self.assertEqual(invalid.status_code, 400)
        listing = self.client.get("/api/sales/" + str(sale_id) + "/payments")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.get_json()["summary"]["paid_amount"], 0)

if __name__ == "__main__":
    unittest.main()
