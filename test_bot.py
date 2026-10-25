import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from bot import Campaign

class Tests(unittest.TestCase):
 def setUp(self):
  self.day=1
  self.c=Campaign(':memory:',start='2026-11-01',clock=lambda:datetime(2026,11,self.day,12,tzinfo=ZoneInfo('Europe/Moscow')))
  for d in range(1,11):self.c.configure_day(f'2026-11-{d:02}',f'Partner {d}','123','https://example.org', 'DEMO instructions',[f'DEMO-{d}'])
 def tearDown(self):self.c.close()
 def test_promo_idempotence_and_chance(self):
  a=self.c.promo('1');b=self.c.promo('1')
  self.assertEqual(a['code'],b['code']);self.assertTrue(b['reused']);self.assertEqual(self.c.chances('1')['count'],1)
 def test_exhausted_not_block_registration(self):
  self.c.promo('1');self.assertIsNone(self.c.promo('2')['code'])
  self.assertTrue(self.c.register('2',lambda *_:True)['registered']);self.assertEqual(self.c.chances('2')['count'],1)
 def test_duplicate_registration(self):
  self.c.promo('1');self.assertTrue(self.c.register('1',lambda *_:True)['new'])
  self.assertFalse(self.c.register('1',lambda *_:True)['new']);self.assertEqual(self.c.chances('1')['count'],1)
 def test_subscription_failure(self):
  self.assertFalse(self.c.register('1',lambda *_:False)['registered']);self.assertEqual(self.c.chances('1')['count'],0)
  def fail(*_):raise OSError('Network down')
  with self.assertRaises(OSError):self.c.register('1',fail)
  self.assertEqual(self.c.chances('1')['count'],0)
 def test_ten_days_limit(self):
  for day in range(1,11):self.day=day;self.c.register('1',lambda *_:True)
  self.assertEqual(self.c.chances('1')['count'],10)
  self.day=11
  with self.assertRaises(ValueError):self.c.promo('1')
 def test_draw_approval_and_claim(self):
  for uid in range(8):self.c.register(uid,lambda *_:True)
  with self.assertRaises(ValueError):self.c.draw('2026-11-01')
  self.day=2;r=self.c.draw('2026-11-01');self.assertEqual(len(set(r['winners']+r['reserves'])),3)
  self.assertEqual(r,self.c.draw('2026-11-01'));self.assertEqual(self.c.pending_notifications(),[])
  uid=r['winners'][0]
  with self.assertRaises(ValueError):self.c.claim(r['key'],uid,'A','B',True)
  self.c.approve(r['key']);self.c.approve(r['key']);self.assertEqual(len(self.c.pending_notifications()),1)
  with self.assertRaises(ValueError):self.c.claim(r['key'],uid,'A','B',False)
  self.c.claim(r['key'],uid,'A','B',True)
  self.assertIn('winner',self.c.export_draw(r['key']))
 def test_final_weighted_unique(self):
  for day in range(1,11):
   self.day=day
   for uid in range(8):self.c.register(uid,lambda *_:True)
  self.day=11;r=self.c.draw();self.assertEqual(len(r['winners']),5);self.assertEqual(len(r['reserves']),2)
  self.assertEqual(len(set(r['winners']+r['reserves'])),7);self.assertTrue(all(w==10 for w in r['participants'].values()))
  self.assertEqual(r,self.c.draw())
 def test_reserve_promotion(self):
  for uid in range(8):self.c.register(uid,lambda *_:True)
  self.day=2;r=self.c.draw('2026-11-01');self.c.approve(r['key']);old=r['winners'][0]
  self.day=4;deadline=datetime(2026,11,3,12,tzinfo=ZoneInfo('Europe/Moscow'))
  new=self.c.promote_reserve(r['key'],old,deadline)
  self.assertEqual(new,r['reserves'][0])
  with self.assertRaises(ValueError):self.c.claim(r['key'],old,'A','B',True)
  self.c.claim(r['key'],new,'A','B',True)
  with self.assertRaises(ValueError):self.c.promote_reserve(r['key'],new,deadline)
 def test_stats(self):
  self.c.promo('1');self.c.promo('1');s=self.c.stats()
  self.assertEqual(s['started_users'],1);self.assertEqual(s['issued_promos'],1);self.assertEqual(s['promo_requests'],2)
 def test_invalid_day_codes(self):
  with self.assertRaises(ValueError):self.c.configure_day('2026-12-01','x','123','https://example.org','x',[])

if __name__=='__main__':unittest.main()
