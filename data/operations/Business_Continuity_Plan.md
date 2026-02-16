# Business Continuity Plan (BCP)
Company: RT Healthcare
Owner: Operations & Security
Version: 2.0
Classification: Internal

---

# 1. Purpose

This Business Continuity Plan (BCP) establishes procedures to ensure critical business functions continue during and after a disruption.

The objective is to minimize operational downtime, protect patient data, and ensure regulatory compliance.

---

# 2. Scope

Applies to:

- Clinical systems
- IT infrastructure
- Cloud services
- Customer-facing applications
- Financial systems
- Vendor-dependent services

---

# 3. Business Impact Analysis (BIA)

Each critical function must document:

- Business owner
- Criticality rating
- Recovery Time Objective (RTO)
- Recovery Point Objective (RPO)
- Dependencies (internal and external)

### Criticality Levels

Critical – Impact within 1 hour  
High – Impact within 4 hours  
Medium – Impact within 24 hours  
Low – Impact beyond 48 hours  

---

# 4. Recovery Objectives

RTO: Maximum tolerable downtime  
RPO: Maximum acceptable data loss  

All Tier 1 systems must maintain:

- RTO < 1 hour  
- RPO < 15 minutes  

---

# 5. Continuity Strategies

Strategies include:

- Redundant cloud regions
- Multi-AZ deployment
- Automated failover
- Regular data backups
- Secondary vendor arrangements
- Remote workforce capability

---

# 6. Incident Response Integration

BCP integrates with:

- Incident Response Plan
- Disaster Recovery Runbook
- Communication Plan

---

# 7. Communication Plan

During disruption:

- Executive leadership notified immediately
- Customers notified if service impact > SLA threshold
- Regulatory reporting performed if required

---

# 8. Testing & Maintenance

BCP must be tested:

- Annually via tabletop exercises
- After major infrastructure changes
- After real incidents

---

# 9. Documentation Retention

All BCP documentation retained for minimum 7 years.