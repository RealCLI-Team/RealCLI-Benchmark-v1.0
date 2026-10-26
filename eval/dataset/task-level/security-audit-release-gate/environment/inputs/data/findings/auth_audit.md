# Authentication audit findings

### F-101
category: auth
severity: high
cvss: 8.2
route: /api/login
summary: Weak session token entropy allows fixation

### F-110
category: auth
route: /api/users
summary: Missing rate limiting on user lookup

### F-120
category: auth
severity: high
cvss: 7.5
route: /api/reports
summary: Insecure password reset token reuse

### F-200
category: auth
cvss: 6.0
route: /api/orders
summary: CSRF token not validated on order submit
