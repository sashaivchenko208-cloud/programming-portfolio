"""Promo campaign domain prototype. Python 3.10+, standard library only."""
import csv
import io
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

MENU = ['Получить промокод', 'Участвовать в розыгрыше', 'Как участвовать', 'Мои шансы', 'Правила акции']

class Campaign:
    def __init__(self, path='campaign.sqlite3', start='2026-11-01', clock=None):
        self.start = date.fromisoformat(start)
        self.clock = clock or (lambda: datetime.now(ZoneInfo('Europe/Moscow')))
        self.db = sqlite3.connect(path, isolation_level=None, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS users(uid TEXT PRIMARY KEY, started TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS partners(day TEXT PRIMARY KEY, name TEXT NOT NULL, channel TEXT NOT NULL, url TEXT NOT NULL, instructions TEXT NOT NULL, winners INTEGER NOT NULL CHECK(winners>0));
        CREATE TABLE IF NOT EXISTS promos(code TEXT PRIMARY KEY, day TEXT NOT NULL REFERENCES partners(day), uid TEXT, assigned TEXT, UNIQUE(day,uid));
        CREATE TABLE IF NOT EXISTS activity(uid TEXT NOT NULL REFERENCES users(uid), day TEXT NOT NULL REFERENCES partners(day), PRIMARY KEY(uid,day));
        CREATE TABLE IF NOT EXISTS entries(uid TEXT NOT NULL REFERENCES users(uid), day TEXT NOT NULL REFERENCES partners(day), registered TEXT NOT NULL, PRIMARY KEY(uid,day));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, uid TEXT, day TEXT, kind TEXT, at TEXT);
        CREATE TABLE IF NOT EXISTS draws(key TEXT PRIMARY KEY, payload TEXT NOT NULL, at TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS notices(draw_key TEXT NOT NULL REFERENCES draws(key), uid TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', PRIMARY KEY(draw_key,uid));
        CREATE TABLE IF NOT EXISTS claims(draw_key TEXT NOT NULL REFERENCES draws(key), uid TEXT NOT NULL, first_name TEXT NOT NULL, last_name TEXT NOT NULL, consent_at TEXT NOT NULL, PRIMARY KEY(draw_key,uid));
        ''')

        self.db.execute("INSERT OR IGNORE INTO settings VALUES ('start',?)",(start,))
        if self.db.execute("SELECT value FROM settings WHERE key='start'").fetchone()[0] != start:
            self.db.close()
            raise ValueError('Campaign start differs from stored configuration')

    def close(self): self.db.close()
    def now(self): return self.clock().isoformat()
    def today(self):
        dt = self.clock()
        if dt.tzinfo is None: raise ValueError('Clock must be timezone aware')
        return dt.astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
    @contextmanager
    def tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
    def _user(self, uid):
        uid = str(uid).strip()
        if not uid: raise ValueError('Empty user')
        self.db.execute('INSERT OR IGNORE INTO users VALUES (?,?)',(uid,self.now()))
        return uid
    def _partner(self):
        day = self.today()
        p = self.db.execute('SELECT * FROM partners WHERE day=?',(day,)).fetchone()
        if not p: raise ValueError('Campaign is not active today')
        return p
    def configure_day(self, day, name, channel, url, instructions, codes=(), winners=1):
        d=date.fromisoformat(day)
        if not 0 <= (d-self.start).days < 10: raise ValueError('Day outside campaign')
        if not name or not channel or not url.startswith('https://') or not instructions or winners<1: raise ValueError('Invalid partner')
        codes=list(codes)
        if any(not isinstance(c,str) or not c.strip() for c in codes) or len(set(codes)) != len(codes): raise ValueError('Invalid or duplicate codes')
        with self.tx():
            self.db.execute('INSERT INTO partners VALUES (?,?,?,?,?,?)',(day,name,str(channel),url,instructions,winners))
            self.db.executemany('INSERT INTO promos(code,day) VALUES (?,?)',[(c,day) for c in codes])
    def start_user(self, uid):
        with self.tx(): uid=self._user(uid)
        return {'text':'Добро пожаловать в Бот розыгрышей', 'buttons':MENU, 'banner':None}
    def promo(self, uid):
        with self.tx():
            uid=self._user(uid); p=self._partner(); day=p['day']
            self.db.execute('INSERT INTO events(uid,day,kind,at) VALUES (?,?,?,?)',(uid,day,'promo_request',self.now()))
            code=self.db.execute('SELECT code FROM promos WHERE day=? AND uid=?',(day,uid)).fetchone()
            if code: return {'code':code['code'],'reused':True,'instructions':p['instructions']}
            code=self.db.execute('SELECT code FROM promos WHERE day=? AND uid IS NULL ORDER BY code LIMIT 1',(day,)).fetchone()
            if not code: return {'code':None,'text':'Промокоды закончились. Участие в розыгрыше доступно.','buttons':['Участвовать в розыгрыше']}
            self.db.execute('UPDATE promos SET uid=?,assigned=? WHERE code=?',(uid,self.now(),code['code']))
            self.db.execute('INSERT OR IGNORE INTO activity VALUES (?,?)',(uid,day))
            return {'code':code['code'],'reused':False,'instructions':p['instructions']}
    def register(self, uid, verifier):
        # Fail closed on API failure. A test verifier must never be used in production.
        p=self._partner()
        subscribed=verifier(str(uid),p['channel'])
        if subscribed is not True: return {'registered':False,'text':'Подписка не найдена','subscribe_url':p['url']}
        with self.tx():
            uid=self._user(uid)
            if self.today()!=p['day']: raise ValueError('Day changed; repeat subscription verification')
            inserted=self.db.execute('INSERT OR IGNORE INTO entries VALUES (?,?,?)',(uid,p['day'],self.now())).rowcount
            self.db.execute('INSERT OR IGNORE INTO activity VALUES (?,?)',(uid,p['day']))
            return {'registered':True,'new':bool(inserted),'chances':self.chances(uid)['count']}
    def chances(self,uid):
        rows=self.db.execute('SELECT a.day,p.name FROM activity a JOIN partners p USING(day) WHERE uid=? ORDER BY day',(str(uid),)).fetchall()
        remaining=max(0,min(10,(self.start+timedelta(days=10)-date.fromisoformat(self.today())).days-1))
        return {'count':len(rows),'max':10,'days':[r['day'] for r in rows],'partners':[r['name'] for r in rows],'remaining_days_after_today':remaining,'grand_prize':'5 победителей, по 2 билета каждому; реквизиты события задаются правилами'}
    def draw(self,day=None,reserves=2):
        key='final' if day is None else day
        if not isinstance(reserves,int) or reserves<0: raise ValueError('Invalid reserve count')
        with self.tx():
            saved=self.db.execute('SELECT payload FROM draws WHERE key=?',(key,)).fetchone()
            if saved: return json.loads(saved['payload'])
            if day is None:
                if date.fromisoformat(self.today())<self.start+timedelta(days=10): raise ValueError('Campaign not finished')
                rows=self.db.execute('SELECT uid,COUNT(*) weight FROM activity GROUP BY uid ORDER BY uid').fetchall(); n=5
            else:
                if day>=self.today(): raise ValueError('Day not finished')
                partner=self.db.execute('SELECT * FROM partners WHERE day=?',(day,)).fetchone()
                if not partner: raise ValueError('Unknown partner day')
                rows=self.db.execute('SELECT uid,1 weight FROM entries WHERE day=? ORDER BY uid',(day,)).fetchall();n=partner['winners']
            if len(rows)<n+reserves: raise ValueError('Not enough unique participants for winners and reserves')
            weights={r['uid']:r['weight'] for r in rows}; pool=dict(weights); order=[]
            for _ in range(n+reserves):
                ticket=secrets.randbelow(sum(pool.values()))
                for uid,weight in pool.items():
                    ticket-=weight
                    if ticket<0:
                        order.append(uid);del pool[uid];break
            payload={'key':key,'winners':order[:n],'reserves':order[n:],'participants':weights,'tickets_per_winner':2 if day is None else None,'method':'weighted without replacement; secrets.randbelow','created_at':self.now()}
            self.db.execute('INSERT INTO draws(key,payload,at) VALUES (?,?,?)',(key,json.dumps(payload,ensure_ascii=False),self.now()))
            return payload
    def approve(self,key):
        # Admin-only CLI/service method; never exposed as an unauthenticated user command.
        with self.tx():
            row=self.db.execute('SELECT payload FROM draws WHERE key=?',(key,)).fetchone()
            if not row: raise ValueError('Unknown draw')
            self.db.execute('UPDATE draws SET approved=1 WHERE key=?',(key,))
            self.db.executemany('INSERT OR IGNORE INTO notices(draw_key,uid) VALUES (?,?)',[(key,u) for u in json.loads(row['payload'])['winners']])
    def pending_notifications(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM notices WHERE status='pending'")]
    def claim(self,key,uid,first,last,consent):
        if consent is not True or not first.strip() or not last.strip(): raise ValueError('Names and explicit consent required')
        with self.tx():
            if not self.db.execute("SELECT 1 FROM notices WHERE draw_key=? AND uid=? AND status!='expired'",(key,str(uid))).fetchone(): raise ValueError('Not an approved winner')
            self.db.execute('INSERT OR IGNORE INTO claims VALUES (?,?,?,?,?)',(key,str(uid),first.strip(),last.strip(),self.now()))
    def promote_reserve(self,key,expired_uid,deadline):
        if deadline.tzinfo is None or self.clock()<deadline: raise ValueError('Claim deadline not reached')
        with self.tx():
            row=self.db.execute('SELECT * FROM draws WHERE key=?',(key,)).fetchone()
            if not row or not row['approved']: raise ValueError('Draw not approved')
            data=json.loads(row['payload']); expired_uid=str(expired_uid)
            if expired_uid not in data['winners']: raise ValueError('Not a current winner')
            if self.db.execute('SELECT 1 FROM claims WHERE draw_key=? AND uid=?',(key,expired_uid)).fetchone(): raise ValueError('Prize already claimed')
            if not data['reserves']: raise ValueError('No reserves left')
            replacement=data['reserves'].pop(0)
            data['winners'][data['winners'].index(expired_uid)]=replacement
            data.setdefault('replacements',[]).append({'expired':expired_uid,'replacement':replacement,'at':self.now()})
            self.db.execute('UPDATE draws SET payload=? WHERE key=?',(json.dumps(data,ensure_ascii=False),key))
            self.db.execute("UPDATE notices SET status='expired' WHERE draw_key=? AND uid=?",(key,expired_uid))
            self.db.execute('INSERT OR IGNORE INTO notices(draw_key,uid) VALUES (?,?)',(key,replacement))
            return replacement
    def export_draw(self,key):
        row=self.db.execute('SELECT * FROM draws WHERE key=?',(key,)).fetchone()
        if not row: raise ValueError('Unknown draw')
        data=json.loads(row['payload']); out=io.StringIO(); writer=csv.writer(out)
        writer.writerow(['user_id','chances','role','draw_time','admin_approved'])
        for uid,weight in data['participants'].items():
            role='winner' if uid in data['winners'] else ('reserve' if uid in data['reserves'] else 'participant')
            writer.writerow([uid,weight,role,row['at'],row['approved']])
        return out.getvalue()
    def stats(self):
        scalar=lambda sql:self.db.execute(sql).fetchone()[0]
        return {'started_users':scalar('SELECT COUNT(*) FROM users'),
            'new_users_by_day':[dict(r) for r in self.db.execute('SELECT substr(started,1,10) day,COUNT(*) n FROM users GROUP BY day')],
            'promo_requests':scalar("SELECT COUNT(*) FROM events WHERE kind='promo_request'"),
            'issued_promos':scalar('SELECT COUNT(*) FROM promos WHERE uid IS NOT NULL'),
            'remaining_by_partner':[dict(r) for r in self.db.execute('SELECT p.day,p.name,COUNT(c.code) remaining FROM partners p LEFT JOIN promos c ON p.day=c.day AND c.uid IS NULL GROUP BY p.day')],
            'entries_by_day':[dict(r) for r in self.db.execute('SELECT day,COUNT(*) n FROM entries GROUP BY day')]}

if __name__=='__main__':
    c=Campaign(':memory:',start='2026-11-01',clock=lambda:datetime(2026,11,1,12,tzinfo=ZoneInfo('Europe/Moscow')))
    c.configure_day('2026-11-01','DEMO partner','123','https://example.org/partner','Демонстрация: код не действителен',['DEMO-001'])
    print(json.dumps(c.start_user('demo'),ensure_ascii=False,indent=2))
    print(json.dumps(c.promo('demo'),ensure_ascii=False,indent=2))
    print(json.dumps(c.chances('demo'),ensure_ascii=False,indent=2))
