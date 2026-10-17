BEGIN TRANSACTION;
CREATE TABLE accounts (
            id INTEGER PRIMARY KEY,
            profile_id INTEGER NOT NULL REFERENCES profiles(id),
            service_id INTEGER NOT NULL REFERENCES services(id),
            status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','in_progress','confirmed')),
            identifier TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, opened_at TEXT, confirmed_at TEXT,
            UNIQUE(profile_id,service_id));
CREATE TABLE audit (
        id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
        target_type TEXT, target_id INTEGER, details TEXT NOT NULL DEFAULT '{}');
CREATE TABLE findings (
            id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
            profile_id INTEGER NOT NULL REFERENCES profiles(id), job_id INTEGER NOT NULL REFERENCES jobs(id),
            title TEXT NOT NULL, text TEXT NOT NULL, destination TEXT NOT NULL, image_url TEXT NOT NULL,
            source_url TEXT NOT NULL, frame_url TEXT NOT NULL, screenshot TEXT NOT NULL,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, sightings INTEGER NOT NULL DEFAULT 1, first_screenshot TEXT NOT NULL DEFAULT '');
INSERT INTO "findings" VALUES(1,'2af9c570f10a208bb1fd1f3ef3e826e509b743ed3226bc0db03e9eadcaf3e4a5',1,1,'Город за туманом','Реклама
Город за туманом
Прочитайте первую главу','https://author.test/work/1?utm_source=ad','https://img.test/1.jpg','https://author.test/','https://author.test/','job-1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png','2026-10-02T10:00:00+00:00','2026-10-05T10:00:00+00:00',5,'job-1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.png');
INSERT INTO "findings" VALUES(2,'84437076f1010e6ff91e28fd6776a1f943525c2ec8f5dd4723959304ec0f88ff',2,2,'Город за туманом','Реклама
Город за туманом
Прочитайте первую главу','https://author.test/work/1','https://img.test/1.jpg?cb=7','https://author.test/top','https://author.test/top','job-2-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png','2026-10-03T10:00:00+00:00','2026-10-04T10:00:00+00:00',2,'job-2-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.png');
INSERT INTO "findings" VALUES(3,'0ab4e4b74847c82013dd1d2e1f8b03fd332335b6dd0399c288f56bc141f2ae6d',1,1,'Город за туманом','Скидка 30% до пятницы','https://author.test/work/1','https://img.test/1.jpg','https://author.test/','https://author.test/','job-1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png','2026-10-06T10:00:00+00:00','2026-10-06T10:00:00+00:00',1,'job-1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.png');
INSERT INTO "findings" VALUES(4,'90fd9dc5da2b12268013ecd35ab763de1a1e4d1646da9c2f864a3250223d02c5',1,1,'Город за туманом. Новая обложка','Реклама
Город за туманом','https://author.test/work/1','https://img.test/1-new.jpg','https://author.test/','https://author.test/','job-1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png','2026-10-07T10:00:00+00:00','2026-10-08T10:00:00+00:00',3,'job-1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.png');
INSERT INTO "findings" VALUES(5,'4e2c0f80bc2e322f4247841bcb1deca6cd934503b688c5a586b6eea4f71fa5f0',2,2,'Ледяной трон','Ледяной трон','https://author.test/work/2','','https://author.test/genre','https://author.test/genre','job-2-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png','2026-10-02T12:00:00+00:00','2026-10-02T12:00:00+00:00',1,'job-2-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.png');
INSERT INTO "findings" VALUES(6,'dc84c73ed267ac1775d19a5684415f478408d7eeaee0f87f1334d47a7e2c9ad0',2,2,'Баннер без текста','','','https://img.test/banner.png','https://other.test/page','https://other.test/page','job-2-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png','2026-10-01T09:00:00+00:00','2026-10-09T09:00:00+00:00',4,'job-2-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.png');
CREATE TABLE jobs (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, profile_id INTEGER NOT NULL REFERENCES profiles(id),
            urls TEXT NOT NULL, selector TEXT NOT NULL, wait_seconds INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'ready', created_at TEXT NOT NULL, last_run TEXT,
            last_count INTEGER NOT NULL DEFAULT 0, last_error TEXT, headed INTEGER NOT NULL DEFAULT 0, updated_at TEXT, schedule_kind TEXT NOT NULL DEFAULT 'none', interval_minutes INTEGER, daily_time TEXT, timezone TEXT NOT NULL DEFAULT 'UTC', paused INTEGER NOT NULL DEFAULT 0, schedule_from TEXT, login_selector TEXT NOT NULL DEFAULT '', run_at TEXT, missed_policy TEXT NOT NULL DEFAULT 'one', overlap_policy TEXT NOT NULL DEFAULT 'skip', window_start TEXT, window_end TEXT, schedule_updated_at TEXT, schedule_updated_by TEXT);
INSERT INTO "jobs" VALUES(1,'Сбор 1',1,'["https://author.test/"]','',2,'ready','2026-10-01T08:00:00+00:00',NULL,0,NULL,0,NULL,'none',NULL,NULL,'UTC',0,NULL,'',NULL,'one','skip',NULL,NULL,NULL,NULL);
INSERT INTO "jobs" VALUES(2,'Сбор 2',2,'["https://author.test/"]','',2,'ready','2026-10-01T08:00:00+00:00',NULL,0,NULL,0,NULL,'none',NULL,NULL,'UTC',0,NULL,'',NULL,'one','skip',NULL,NULL,NULL,NULL);
CREATE TABLE logs (
            id INTEGER PRIMARY KEY, job_id INTEGER, level TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', profile_id INTEGER, run_id INTEGER);
INSERT INTO "logs" VALUES(1,1,'error','https://author.test/: Страница не ответила вовремя.','2026-10-14T14:25:13+00:00','job',NULL,NULL);
INSERT INTO "logs" VALUES(2,NULL,'info','Worker запущен.','2026-10-14T14:25:13+00:00','system',NULL,NULL);
CREATE TABLE mail_operations (
        id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, kind TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE mailboxes (
            id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL UNIQUE REFERENCES accounts(id),
            region TEXT NOT NULL, zoid TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
            password_cipher BLOB NOT NULL,
            status TEXT NOT NULL DEFAULT 'prepared' CHECK(status IN ('prepared','creating','created','failed','unknown')),
            remote_account_id TEXT, error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, provider TEXT NOT NULL DEFAULT 'Zoho', source TEXT NOT NULL DEFAULT 'api', error_code TEXT NOT NULL DEFAULT '', password_state TEXT NOT NULL DEFAULT 'generated', reconciled_at TEXT);
CREATE TABLE profile_leases (
            profile_id INTEGER PRIMARY KEY REFERENCES profiles(id), owner TEXT NOT NULL, purpose TEXT NOT NULL,
            acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE profiles (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, session_state TEXT NOT NULL DEFAULT 'unknown', session_checked_at TEXT, session_imported_at TEXT, last_used_at TEXT);
INSERT INTO "profiles" VALUES(1,'Профиль А','2026-10-01T08:00:00+00:00','unknown',NULL,NULL,NULL);
INSERT INTO "profiles" VALUES(2,'Профиль Б','2026-10-01T08:00:00+00:00','unknown',NULL,NULL,NULL);
CREATE TABLE run_pages (
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), idx INTEGER NOT NULL, url TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('ok','empty','error','user_action')), found INTEGER NOT NULL DEFAULT 0,
        error_code TEXT, message TEXT, screenshot TEXT, login INTEGER, started_at TEXT, finished_at TEXT,
        UNIQUE(run_id, idx));
CREATE TABLE runs (
            id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
            trigger TEXT NOT NULL CHECK(trigger IN ('manual','schedule','retry')),
            status TEXT NOT NULL CHECK(status IN ('queued','running','done','partial','error','failed','waiting_user','stopped','interrupted','cancelled','skipped')),
            attempt INTEGER NOT NULL DEFAULT 1, scheduled_for TEXT, not_before TEXT NOT NULL,
            queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, heartbeat_at TEXT,
            worker_id TEXT, resume_from INTEGER NOT NULL DEFAULT 0, pages_done INTEGER NOT NULL DEFAULT 0,
            pages_total INTEGER NOT NULL DEFAULT 0, found INTEGER NOT NULL DEFAULT 0, error TEXT,
            stop_requested INTEGER NOT NULL DEFAULT 0, retry_of INTEGER REFERENCES runs(id), error_code TEXT);
CREATE TABLE services (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL CHECK(kind IN ('email','site')),
            registration_url TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE sessions (
            token_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "settings" VALUES('fingerprint_version','2');
CREATE TABLE workers (
            id TEXT PRIMARY KEY, pid INTEGER, host TEXT, mode TEXT NOT NULL, version TEXT NOT NULL,
            started_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, stopped_at TEXT, max_browsers INTEGER NOT NULL);
CREATE UNIQUE INDEX runs_occurrence ON runs(job_id, scheduled_for) WHERE scheduled_for IS NOT NULL AND trigger='schedule';
CREATE INDEX runs_status ON runs(status, not_before);
CREATE INDEX audit_at ON audit(at);
CREATE INDEX logs_created ON logs(created_at);
COMMIT;
