USE postfix;
INSERT INTO domain (domain, description, mailboxes, aliases, maxquota, quota, transport, backupmx, active, created, modified)
VALUES
  ('lists.example.org', 'e2e mlmmj lists subdomain', 100, 100, 1073741824, 1073741824, 'virtual', 0, 1, NOW(), NOW()),
  ('example.org', 'e2e shared-domain', 100, 100, 1073741824, 1073741824, 'virtual', 0, 1, NOW(), NOW());
