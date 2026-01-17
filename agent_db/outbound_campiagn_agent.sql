-- Create Campaign Taregt Member Call List Table
CREATE TABLE IF NOT EXISTS campaign_target_member_call_list (
    member_id VARCHAR(50) PRIMARY KEY,
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    campaign_name VARCHAR(100),
    call_status VARCHAR(100)
    
);

INSERT INTO campaign_target_member_call_list (member_id, phone_number, campaign_name, call_status)
VALUES 
    ('1001', '9135960926', 'Healthy Habit Programs', 'Not Called')
ON CONFLICT (member_id) DO NOTHING;



-- Create member Detail Table
CREATE TABLE IF NOT EXISTS target_members_detail (
    member_id VARCHAR(50) PRIMARY KEY,
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    member_first_name VARCHAR(100),
    member_last_name VARCHAR(100),
    member_email VARCHAR(100),
    campaign_name VARCHAR(100),
    member_reached_ai_agent BOOLEAN DEFAULT FALSE,
    member_warmhug_transfered BOOLEAN DEFAULT FALSE,
    csr_name VARCHAR(100),
    csr_phone_number VARCHAR(20) UNIQUE NOT NULL,
    campaign_email_sent BOOLEAN DEFAULT FALSE
    
);

-- Insert seed data for testing
INSERT INTO target_members_details (member_id, phone_number, member_first_name, member_last_name, member_email, campaign_name,csr_name,csr_phone_number)
VALUES 
    ('1001', '9135960926', 'Raju', 'Nekadi', 'rajunekadi@gmail.com', 'Healthy Habit Programs','Trang','2147154963')
ON CONFLICT (member_id) DO NOTHING;
