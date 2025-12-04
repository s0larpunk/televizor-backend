-- Drop existing constraint
ALTER TABLE user_sessions DROP CONSTRAINT IF EXISTS user_sessions_user_phone_fkey;

-- Re-add constraint with CASCADE
ALTER TABLE user_sessions 
ADD CONSTRAINT user_sessions_user_phone_fkey 
FOREIGN KEY (user_phone) 
REFERENCES users(phone) 
ON DELETE CASCADE;
