# IP records

| Date | Covers | Type | Record hash | Signer | Tag |
|---|---|---|---|---|---|
| 2026-11-26 | Commit `696de97`: PostgreSQL studies in SQL and Python with tests and documentation. No authorship claimed in PostgreSQL itself, third-party libraries and the established techniques studied. | software, hash-only | [`f5c417ca9156fd90172bc62074c487e06e77c0b15e7b2fcea72b07d33c8a3ea5`](https://iptrust.org/v/f5c417ca9156fd90172bc62074c487e06e77c0b15e7b2fcea72b07d33c8a3ea5) | Gregory J. Ward | `ip-2026-11-26` |

A record is evidence that the committed content existed by the time of its anchor and that the signer made
the statement; it does not create or register any right. Anchoring completes some hours after submission.
Files under `third_party/`, where present, are not part of the record.

## Verifying

Verification needs the evidence package for the record, which the author holds and which is not committed
here. With it in `.iptrust/`:

```
git worktree add /tmp/at-tag-postgres-patterns ip-2026-11-26
cp -r .iptrust /tmp/at-tag-postgres-patterns/
(cd /tmp/at-tag-postgres-patterns && iptrust2 verify f5c417ca9156fd90172bc62074c487e06e77c0b15e7b2fcea72b07d33c8a3ea5 --fetch-headers)
git worktree remove --force /tmp/at-tag-postgres-patterns
```

Expect `verdict: valid` and `content: matched` once both chains have anchored.
