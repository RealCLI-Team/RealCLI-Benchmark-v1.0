# File-handling audit findings

### F-130
category: file
severity: medium
cvss: 5.0
route: /api/files/download
summary: Path traversal in download handler

### F-140
category: file
severity: low
cvss: 3.0
route: /api/files/upload
summary: Verbose error message on upload failure

### F-160
category: sql
cvss: 4.6
route: /api/orders
summary: SQL injection via sort parameter

### F-170
category: file
severity: critical
cvss: 9.5
route: /api/admin
summary: Remote code execution via admin upload

### F-200
category: file
cvss: 6.0
route: /api/files/download
summary: CSRF observed on file endpoint
