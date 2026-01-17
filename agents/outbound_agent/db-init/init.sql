-- Create debtors table
CREATE TABLE IF NOT EXISTS debtors (
    account_number VARCHAR(50) PRIMARY KEY,
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    ssn VARCHAR(20),
    dob VARCHAR(20),
    balance NUMERIC(10, 2),
    email VARCHAR(100),
    payment_plan_qualified BOOLEAN DEFAULT FALSE,
    last_payment_date TIMESTAMP
);

-- Insert seed data for testing
-- Phone: 9135960926
INSERT INTO debtors (account_number, phone_number, first_name, last_name, ssn, dob, balance, email, payment_plan_qualified)
VALUES 
    ('1001', '9135960926', 'John', 'Doe', '123456789', '1980-01-01', 1500.00, 'nekadiraju@gmail.com', TRUE)
ON CONFLICT (account_number) DO NOTHING;
