# MCP Data Bridge — Upload Portal Guide

## Access the Portal

1. Go to **https://private-mcp.propio.cl/portal/**
2. Enter your email when prompted (Cloudflare Access)
3. Check your inbox for a verification code and enter it
4. You're in

**Authorized emails:** `enzo@propiolatam.com`, `francisco@propiolatam.com`

---

## Upload New Data

1. Click **"Upload New Data"** (or go to `/portal/upload`)
2. Fill in the form:
   - **Source Name** (required): e.g., "Banco Estado", "Santander", "Creditu"
     - This becomes the database name
     - Using the same name again **replaces** the existing data
   - **Description** (optional): e.g., "Loan remittance data Q1 2026"
   - **Files**: Drag & drop `.xlsx` or `.xls` files (max 50 MB each)
3. Click **"Upload & Process"**
4. Wait for processing (typically under 1 minute)
5. You'll see a success message with the MCP endpoint URL

The data is **immediately available** to Claude through MCP.

---

## View Database Details

From the dashboard, click **"View Details"** on any database to see:
- Table schemas (column names, types)
- Sample data (first 5 rows)
- Row counts
- MCP endpoint URL

---

## Delete a Database

1. On the dashboard or detail page, click **"Delete"**
2. Confirm the action
3. The database and all its data are permanently removed

---

## Accepted File Formats

| Format | Extension | Supported |
|---|---|---|
| Excel (modern) | `.xlsx` | Yes |
| Excel (legacy) | `.xls` | Yes |
| CSV | `.csv` | No (convert to .xlsx first) |
| Other | — | No |

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Can't access portal | Check that your email is in the authorized list |
| Upload fails | Check file format (.xlsx/.xls only) and size (< 50 MB) |
| Data looks wrong | Delete the database and re-upload with corrected files |
| Portal is slow | Large files take longer to process. Wait for the spinner to finish. |

**Contact:** francisco@propiolatam.com
