# Daily AP briefing - 2026-10-08

Processed **10** documents: 8 invoices, 1 receipts, 1 other.

## Payment status

| Status | Count |
|---|---|
| paid | 2 |
| unpaid | 2 |
| overdue | 1 |
| partially_paid | 1 |
| overpaid | 1 |
| duplicate | 1 |
| unknown_vendor | 1 |

**Outstanding payable:** 2,652.00 USD

## Needs attention

| File | Vendor | Invoice | Total | Status | Why |
|---|---|---|---|---|---|
| email_brightway_inv_1009.txt | Brightway Uniforms | INV-1009 | 360.00 USD | overdue | Unpaid, due 2026-09-19 (19 days ago) |
| inv_1002_metro_janitorial.pdf | Metro Janitorial Wholesale | INV-1002 | 462.00 USD | unpaid | No PO number on invoice |
| inv_1002_metro_janitorial_resend.pdf | Metro Janitorial Wholesale | INV-1002 | 462.00 USD | duplicate | INV-1002 from Metro Janitorial Wholesale was already processed |
| inv_1003_brightway_uniforms.pdf | Brightway Uniforms | INV-1003 | 480.00 USD | overpaid | Overpaid by 60.00 |
| inv_1005_northwind_equipment.pdf | Northwind Equipment Hire | INV-1005 | 1,180.00 USD | unpaid | subtotal 900.00 + tax 180.00 != total 1180.00 |
| inv_1007_acme_pest.pdf | Acme Pest Control | INV-1007 | 385.00 USD | unknown_vendor | 'Acme Pest Control' not in purchase orders or ledger |
| inv_1008_sparkleclean.pdf | SparkleClean Supplies Ltd | INV-1008 | 1,650.00 USD | partially_paid | Outstanding 650.00 of 1650.00 |

## Approval queue (3 awaiting human review)

- [ ] `inv_1002_metro_janitorial.pdf` - confidence 0.93; issues: missing_po
- [ ] `inv_1002_metro_janitorial_resend.pdf` - confidence 0.92; issues: missing_po
- [ ] `inv_1005_northwind_equipment.pdf` - confidence 0.90; issues: totals_mismatch

## Clean

- SparkleClean Supplies Ltd INV-1001 - 1,100.00 USD - paid
- QuickFuel Station RCPT-2201 - 74.71 USD - paid
