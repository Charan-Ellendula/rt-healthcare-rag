# Logging Standards
Company: RT Healthcare
Owner: Engineering
Version: 1.0
Classification: Internal

---

## 1. Purpose

Defines logging standards to support observability, troubleshooting, security monitoring, and compliance.

---

## 2. Log Levels

DEBUG – Development only  
INFO – General operations  
WARN – Recoverable issues  
ERROR – Failed operations  
CRITICAL – System failures  

Production must not use DEBUG level logging.

---

## 3. Structured Logging

Logs must be in JSON format:

{
  "timestamp": "",
  "level": "",
  "service": "",
  "request_id": "",
  "user_id": "",
  "message": ""
}

---

## 4. Sensitive Data Restrictions

Logs must NOT contain:

- PHI
- SSN
- Credit card numbers
- Passwords
- Authentication tokens

---

## 5. Retention Policy

Application logs – 90 days  
Security logs – 1 year minimum  
Audit logs – 7 years if regulatory requirement applies  

---

## 6. Centralized Logging

Logs must be centralized using:

- ELK stack
- Cloud logging solution
- SIEM integration

---

## 7. Monitoring & Alerts

Alerts required for:

- Repeated failed authentication attempts
- 5xx spikes
- Latency threshold breaches
- Suspicious behavior
