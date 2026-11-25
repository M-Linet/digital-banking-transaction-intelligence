-- 00_create_database.sql  (run against the default "postgres" database)
DROP DATABASE IF EXISTS digital_bank;
CREATE DATABASE digital_bank WITH ENCODING = 'UTF8' TEMPLATE = template0;
