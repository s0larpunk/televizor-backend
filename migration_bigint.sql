-- Migration script to change Integer columns to BigInteger
-- Run this in your database tool (e.g. pgAdmin, psql)

ALTER TABLE users ALTER COLUMN telegram_id TYPE BIGINT;
ALTER TABLE feeds ALTER COLUMN destination_channel_id TYPE BIGINT;
