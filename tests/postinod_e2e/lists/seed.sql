USE postfix;
INSERT INTO domain (domain, description, mailboxes, aliases, maxquota, quota, transport, backupmx, active, created, modified)
VALUES ('example.org', 'e2e seed', 100, 100, 1073741824, 1073741824, 'lmtp:unix:/tmp/dovecot', 0, 1, NOW(), NOW());
