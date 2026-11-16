import os, tempfile, unittest
from app import app, init_db
class StockProTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.NamedTemporaryFile(delete=False); self.tmp.close()
        app.config['TESTING']=True
        os.environ['DATABASE_PATH']=self.tmp.name
        import app as module
        module.DB_PATH=self.tmp.name
        init_db()
        self.client=app.test_client()
        self.client.post('/login',data={'username':'admin','password':'Admin@12345'})
    def tearDown(self): os.unlink(self.tmp.name)
    def test_health(self): self.assertEqual(self.client.get('/health').status_code,200)
    def test_dashboard_requires_login(self):
        c=app.test_client(); self.assertEqual(c.get('/').status_code,302)
    def test_products_endpoint(self): self.assertEqual(self.client.get('/api/products').status_code,200)
if __name__=='__main__': unittest.main()
