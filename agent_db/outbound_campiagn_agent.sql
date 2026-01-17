-- Create debtors table
CREATE TABLE IF NOT EXISTS target_members (
    member_id VARCHAR(50) PRIMARY KEY,
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    email VARCHAR(100),
    campaign_targeted_for VARCHAR(100),
    member_reached_ai_agent BOOLEAN DEFAULT FALSE,
    member_warmhug_transfered BOOLEAN DEFAULT FALSE,
    csr_name VARCHAR(100),
    
);

-- Insert seed data for testing
INSERT INTO target_members (member_id, phone_number, first_name, last_name, email, campaign_target_for)
VALUES 
    ('1001', '9135960926', 'Raju', 'Nekadi', 'rajunekadi@gmail.com', 'Healthy Habit Programs')
ON CONFLICT (member_id) DO NOTHING;
