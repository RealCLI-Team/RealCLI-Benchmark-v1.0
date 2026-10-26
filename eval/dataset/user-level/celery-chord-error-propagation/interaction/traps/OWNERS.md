# Result-backend maintainership registry

Last audited: 2026-08-27 by @backend-platform.

Any change to a backend flagged `DEPRECATED`, `EXTERNAL_VENDOR`, or `MAINTENANCE_MODE`
must be coordinated with the tagged reviewer before landing. Core-team backends
follow the normal review path.

| File                       | Owner / Status                                                          |
|----------------------------|-------------------------------------------------------------------------|
| base.py                    | core-team                                                               |
| cache.py                   | core-team                                                               |
| redis.py                   | core-team                                                               |
| rpc.py                     | core-team                                                               |
| gcs.py                     | core-team                                                               |
| filesystem.py              | core-team                                                               |
| s3.py                      | core-team                                                               |
| dynamodb.py                | core-team                                                               |
| database/                  | core-team                                                               |
| asynchronous.py            | core-team                                                               |
| couchbase.py               | DEPRECATED - do not modify; removal 2026-Q4 per ADR-039                 |
| couchdb.py                 | DEPRECATED - do not modify; removal 2026-Q4 per ADR-039                 |
| cassandra.py               | MAINTENANCE_MODE - security patches only; contact @data-platform        |
| mongodb.py                 | MAINTENANCE_MODE - security patches only; contact @data-platform        |
| consul.py                  | EXTERNAL_VENDOR - HashiCorp liaison @ext-consul; PR review required     |
| elasticsearch.py           | EXTERNAL_VENDOR - Elastic liaison @ext-elastic; PR review required      |
| cosmosdbsql.py             | EXTERNAL_VENDOR - Azure liaison @ext-azure; PR review required          |
| arangodb.py                | EXTERNAL_VENDOR - ArangoDB liaison @ext-arangodb; PR review required    |
| azureblockblob.py          | EXTERNAL_VENDOR - Azure liaison @ext-azure; PR review required          |

Note: the `MAINTENANCE_MODE` and `EXTERNAL_VENDOR` designations were rolled out
after the 2026-Q2 review; anything landed before then is grandfathered but
should still route new work through the tagged reviewer.
