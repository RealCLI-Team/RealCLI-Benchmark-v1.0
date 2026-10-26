"""验证 sqlglot 对递归 CTE 的 SEARCH / CYCLE 子句的支持。

期望值全部是冻结的 golden 字符串(依据 SQL 标准语法与各数据库对
PostgreSQL/Oracle 递归子查询分解子句的实际行为事先确定),与被测仓库的
实时状态无关。
"""

import pytest
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


PG_CASES = [
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id SET ordercol SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id SET ordercol SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH BREADTH FIRST BY id SET ordercol SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH BREADTH FIRST BY id SET ordercol SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id, link SET ordercol SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id, link SET ordercol SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id, link, data SET seq SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id, link, data SET seq SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id ASC SET ordercol SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id ASC SET ordercol SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id DESC SET ordercol SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id DESC SET ordercol SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id ASC NULLS FIRST SET ordercol SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id ASC NULLS FIRST SET ordercol SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id ASC NULLS LAST SET ordercol SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id ASC SET ordercol SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id DESC NULLS FIRST SET ordercol SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id DESC SET ordercol SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id DESC NULLS LAST SET ordercol SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id DESC NULLS LAST SET ordercol SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH BREADTH FIRST BY id DESC, link ASC SET ordercol SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH BREADTH FIRST BY id DESC, link ASC SET ordercol SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id ASC NULLS FIRST, link DESC NULLS LAST SET seq SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id ASC NULLS FIRST, link DESC NULLS LAST SET seq SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY "Id" DESC SET "OrderCol" SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY "Id" DESC SET "OrderCol" SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) CYCLE id SET is_cycle SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id, link SET is_cycle SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) CYCLE id, link SET is_cycle SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle USING path SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) CYCLE id SET is_cycle USING path SELECT * FROM sg'),
    ("WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle TO 'Y' DEFAULT 'N' SELECT * FROM sg",
     "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' SELECT * FROM sg"),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle TO 1 DEFAULT 0 SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) CYCLE id SET is_cycle TO 1 DEFAULT 0 SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle TO -1 DEFAULT 0 SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) CYCLE id SET is_cycle TO -1 DEFAULT 0 SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle TO TRUE DEFAULT FALSE SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) CYCLE id SET is_cycle TO TRUE DEFAULT FALSE SELECT * FROM sg'),
    ("WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id, link SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg",
     "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) CYCLE id, link SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg"),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle SELECT * FROM sg'),
    ("WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id DESC SET ordercol CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg",
     "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id DESC SET ordercol CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg"),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH BREADTH FIRST BY id, link SET seq CYCLE id, link SET is_cycle TO 1 DEFAULT 0 USING path SELECT * FROM sg',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH BREADTH FIRST BY id, link SET seq CYCLE id, link SET is_cycle TO 1 DEFAULT 0 USING path SELECT * FROM sg'),
]
ORACLE_CASES = [
    ('WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id SET ordercol SELECT * FROM sg',
     'WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id SET ordercol SELECT * FROM sg'),
    ('WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id, link SET ordercol SELECT * FROM sg',
     'WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id, link SET ordercol SELECT * FROM sg'),
    ('WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id ASC NULLS LAST SET ordercol SELECT * FROM sg',
     'WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id ASC SET ordercol SELECT * FROM sg'),
    ('WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id DESC NULLS FIRST SET ordercol SELECT * FROM sg',
     'WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id DESC SET ordercol SELECT * FROM sg'),
    ('WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id DESC NULLS LAST SET ordercol SELECT * FROM sg',
     'WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id DESC NULLS LAST SET ordercol SELECT * FROM sg'),
    ('WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH BREADTH FIRST BY id DESC, link ASC NULLS FIRST SET ordercol SELECT * FROM sg',
     'WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link) SEARCH BREADTH FIRST BY id DESC, link ASC NULLS FIRST SET ordercol SELECT * FROM sg'),
    ('WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle SELECT * FROM sg',
     'WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link) CYCLE id SET is_cycle SELECT * FROM sg'),
    ("WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle TO 'Y' DEFAULT 'N' SELECT * FROM sg",
     "WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link) CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' SELECT * FROM sg"),
    ('WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id, link SET is_cycle TO 1 DEFAULT 0 USING path SELECT * FROM sg',
     'WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link) CYCLE id, link SET is_cycle TO 1 DEFAULT 0 USING path SELECT * FROM sg'),
    ("WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg",
     "WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg"),
]
TRANSPILE_CASES = [
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id DESC SET ordercol SELECT * FROM sg', 'oracle',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id DESC SET ordercol SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id DESC SET ordercol SELECT * FROM sg', 'snowflake',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id DESC SET ordercol SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id DESC SET ordercol SELECT * FROM sg', 'duckdb',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id DESC NULLS FIRST SET ordercol SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id DESC SET ordercol SELECT * FROM sg', 'mysql',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY CASE WHEN id IS NULL THEN 1 ELSE 0 END DESC, id DESC SET ordercol SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH BREADTH FIRST BY id ASC NULLS FIRST, link DESC SET seq SELECT * FROM sg', 'oracle',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link) SEARCH BREADTH FIRST BY id ASC NULLS FIRST, link DESC SET seq SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH BREADTH FIRST BY id ASC NULLS FIRST, link DESC SET seq SELECT * FROM sg', 'tsql',
     'WITH sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH BREADTH FIRST BY id ASC, CASE WHEN link IS NULL THEN 1 ELSE 0 END DESC, link DESC SET seq SELECT * FROM sg'),
    ("WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id, link SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg", 'oracle',
     "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link) CYCLE id, link SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg"),
    ("WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id, link SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg", 'snowflake',
     "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) CYCLE id, link SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg"),
    ("WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id, link SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg", 'bigquery',
     "WITH RECURSIVE sg AS (SELECT g.id AS id, g.link AS link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) CYCLE id, link SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg"),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle TO 1 DEFAULT 0 SELECT * FROM sg', 'oracle',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle TO 1 DEFAULT 0 SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle TO 1 DEFAULT 0 SELECT * FROM sg', 'snowflake',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle TO 1 DEFAULT 0 SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle TO 1 DEFAULT 0 SELECT * FROM sg', 'duckdb',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle TO 1 DEFAULT 0 SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle TO 1 DEFAULT 0 SELECT * FROM sg', 'mysql',
     'WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY CASE WHEN id IS NULL THEN 1 ELSE 0 END, id SET ordercol CYCLE id SET is_cycle TO 1 DEFAULT 0 SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle TO 1 DEFAULT 0 SELECT * FROM sg', 'tsql',
     'WITH sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY CASE WHEN id IS NULL THEN 1 ELSE 0 END, id SET ordercol CYCLE id SET is_cycle TO 1 DEFAULT 0 SELECT * FROM sg'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle TO 1 DEFAULT 0 SELECT * FROM sg', 'bigquery',
     'WITH RECURSIVE sg AS (SELECT g.id AS id, g.link AS link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id NULLS LAST SET ordercol CYCLE id SET is_cycle TO 1 DEFAULT 0 SELECT * FROM sg'),
    ("WITH sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id DESC SET ordercol CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' SELECT * FROM sg", 'postgres',
     "WITH sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id DESC SET ordercol CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' SELECT * FROM sg"),
]
# qualify 应该把 SEARCH/CYCLE 合成出来的额外列 (SEARCH...SET <seq>、
# CYCLE...SET <mark> [...USING <path>]) 也视作 CTE 的可见输出列 —— 参考
# Postgres 与 Oracle 的语义, SELECT * 展开时要包含它们, 未限定引用应能
# 挂回到 CTE 上。
QUALIFY_CASES = [
    ("WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg", 'postgres',
     'WITH RECURSIVE "sg"("id", "link") AS (SELECT "g"."id" AS "id", "g"."link" AS "link" FROM "graph" AS "g" UNION ALL SELECT "g"."id" AS "id", "g"."link" AS "link" FROM "graph" AS "g" JOIN "sg" AS "sg" ON "g"."id" = "sg"."link") SEARCH DEPTH FIRST BY "id" SET "ordercol" CYCLE "id" SET "is_cycle" TO \'Y\' DEFAULT \'N\' USING "path" SELECT "sg"."id" AS "id", "sg"."link" AS "link", "sg"."ordercol" AS "ordercol", "sg"."is_cycle" AS "is_cycle", "sg"."path" AS "path" FROM "sg" AS "sg"'),
    ("WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT ordercol, is_cycle, path FROM sg ORDER BY ordercol", 'postgres',
     'WITH RECURSIVE "sg"("id", "link") AS (SELECT "g"."id" AS "id", "g"."link" AS "link" FROM "graph" AS "g" UNION ALL SELECT "g"."id" AS "id", "g"."link" AS "link" FROM "graph" AS "g" JOIN "sg" AS "sg" ON "g"."id" = "sg"."link") SEARCH DEPTH FIRST BY "id" SET "ordercol" CYCLE "id" SET "is_cycle" TO \'Y\' DEFAULT \'N\' USING "path" SELECT "sg"."ordercol" AS "ordercol", "sg"."is_cycle" AS "is_cycle", "sg"."path" AS "path" FROM "sg" AS "sg" ORDER BY "ordercol"'),
    ('WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id SET ordercol SELECT * FROM sg', 'postgres',
     'WITH RECURSIVE "sg"("id", "link") AS (SELECT "g"."id" AS "id", "g"."link" AS "link" FROM "graph" AS "g" UNION ALL SELECT "g"."id" AS "id", "g"."link" AS "link" FROM "graph" AS "g" JOIN "sg" AS "sg" ON "g"."id" = "sg"."link") SEARCH DEPTH FIRST BY "id" SET "ordercol" SELECT "sg"."id" AS "id", "sg"."link" AS "link", "sg"."ordercol" AS "ordercol" FROM "sg" AS "sg"'),
    ("WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg", 'postgres',
     'WITH RECURSIVE "sg"("id", "link") AS (SELECT "g"."id" AS "id", "g"."link" AS "link" FROM "graph" AS "g" UNION ALL SELECT "g"."id" AS "id", "g"."link" AS "link" FROM "graph" AS "g" JOIN "sg" AS "sg" ON "g"."id" = "sg"."link") CYCLE "id" SET "is_cycle" TO \'Y\' DEFAULT \'N\' USING "path" SELECT "sg"."id" AS "id", "sg"."link" AS "link", "sg"."is_cycle" AS "is_cycle", "sg"."path" AS "path" FROM "sg" AS "sg"'),
]


INVALID_CASES = [
    # SEARCH 缺 kind / 缺 FIRST BY / 缺 SET / 缺序列列 / 非法 kind
    "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH FIRST BY id SET ordercol SELECT * FROM sg",
    "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH BY id SET ordercol SELECT * FROM sg",
    "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST id SET ordercol SELECT * FROM sg",
    "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DEPTH FIRST BY id SET SELECT * FROM sg",
    "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nSEARCH DIAGONAL FIRST BY id SET ordercol SELECT * FROM sg",
    # CYCLE 缺列 / 缺 SET / 缺标记列 / TO 与 DEFAULT 不成对 / 缺常量 / USING 缺路径列
    "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE SET is_cycle SELECT * FROM sg",
    "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET SELECT * FROM sg",
    "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle TO SELECT * FROM sg",
    "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle TO 'Y' SELECT * FROM sg",
    "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle DEFAULT 'N' SELECT * FROM sg",
    "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle TO 'Y' DEFAULT SELECT * FROM sg",
    "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle USING SELECT * FROM sg",
    # CYCLE 出现在 SEARCH 之前(顺序非法)
    "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\nCYCLE id SET is_cycle SEARCH DEPTH FIRST BY id SET ordercol SELECT * FROM sg",
]

NORMALIZATION_CASES = [
    # 关键字大小写、多余空白不影响解析结果
    (
        "with recursive sg(id, link) as (select g.id, g.link from graph g union all select g.id, g.link from graph g join sg on g.id = sg.link) search depth first by id desc set ordercol cycle id set is_cycle to 'Y' default 'N' select * from sg",
        "postgres",
        "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id DESC SET ordercol CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' SELECT * FROM sg",
    ),
    (
        "WITH  RECURSIVE   sg(id, link) AS (SELECT g.id, g.link FROM graph g UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\n\nSEARCH   DEPTH   FIRST   BY   id ,  link   SET   ordercol\n\nSELECT * FROM sg",
        "postgres",
        "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) SEARCH DEPTH FIRST BY id, link SET ordercol SELECT * FROM sg",
    ),
]


def test_postgres_search_cycle_roundtrip():
    for sql, expected in PG_CASES:
        expression = sqlglot.parse_one(sql, read="postgres")
        assert expression.sql("postgres") == expected, sql
        # 再解析规范化输出,结果必须稳定(幂等)
        assert sqlglot.parse_one(expected, read="postgres").sql("postgres") == expected, sql


def test_oracle_search_cycle_roundtrip():
    for sql, expected in ORACLE_CASES:
        expression = sqlglot.parse_one(sql, read="oracle")
        assert expression.sql("oracle") == expected, sql
        assert sqlglot.parse_one(expected, read="oracle").sql("oracle") == expected, sql


def test_transpile_preserves_clauses():
    for sql, write_dialect, expected in TRANSPILE_CASES:
        read_dialect = "oracle" if "postgres" == write_dialect else "postgres"
        expression = sqlglot.parse_one(sql, read=read_dialect)
        assert expression.sql(write_dialect) == expected, (write_dialect, sql)


def test_normalization():
    for sql, dialect, expected in NORMALIZATION_CASES:
        assert sqlglot.parse_one(sql, read=dialect).sql(dialect) == expected, sql


def test_transpile_api():
    sql = (
        "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g "
        "UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\n"
        "SEARCH DEPTH FIRST BY id, link SET ordercol CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path "
        "SELECT * FROM sg"
    )
    result = sqlglot.transpile(sql, read="postgres", write="snowflake")
    assert result == [
        "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph AS g "
        "UNION ALL SELECT g.id, g.link FROM graph AS g JOIN sg ON g.id = sg.link) "
        "SEARCH DEPTH FIRST BY id, link SET ordercol "
        "CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg"
    ]


def test_invalid_syntax_raises():
    for sql in INVALID_CASES:
        with pytest.raises(ParseError):
            sqlglot.parse_one(sql, read="postgres")


def test_qualify_preserves_clauses():
    from sqlglot.optimizer.qualify import qualify

    schema = {"graph": {"id": "INT", "link": "INT"}}
    for sql, dialect, expected in QUALIFY_CASES:
        qualified = qualify(sqlglot.parse_one(sql, read=dialect), schema=schema, dialect=dialect)
        assert qualified.sql(dialect) == expected, sql


def test_ast_shape_search_and_cycle():
    """SEARCH / CYCLE 子句在 AST 上的结构必须是可自省、可编程构造的。
    这些断言是解析后跨模块协作(qualify、pushdown_projections 等)所依赖
    的隐式契约,因此写死到验证里。
    """
    tree = sqlglot.parse_one(
        "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g "
        "UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\n"
        "SEARCH DEPTH FIRST BY id DESC, link ASC NULLS FIRST SET ordercol "
        "CYCLE id, link SET is_cycle TO 'Y' DEFAULT 'N' USING path "
        "SELECT * FROM sg",
        read="postgres",
    )
    with_ = tree.find(exp.With)
    assert with_ is not None
    search = with_.args["search"]
    cycle = with_.args["cycle"]
    # 单独 SEARCH / CYCLE 表达式类型
    assert isinstance(search, exp.RecursiveWithSearch)
    assert isinstance(cycle, exp.RecursiveWithCycle)
    # SEARCH.kind 归一化到大写
    assert search.args["kind"] == "DEPTH"
    # SEARCH 的排序键应当是 exp.Ordered 列表,数量与语法一致
    sort_keys = search.args["expressions"]
    assert isinstance(sort_keys, list) and len(sort_keys) == 2
    for key in sort_keys:
        assert isinstance(key, exp.Ordered)
    assert sort_keys[0].args.get("desc") is True
    assert sort_keys[1].args.get("desc") is False
    assert sort_keys[1].args.get("nulls_first") is True
    # SEARCH 的序列列
    assert search.args["expression"].name == "ordercol"
    # CYCLE 检测列有两个
    assert isinstance(cycle.args["expressions"], list)
    assert [c.name for c in cycle.args["expressions"]] == ["id", "link"]
    # CYCLE 的标记列、to/default、path 列
    assert cycle.args["expression"].name == "is_cycle"
    assert cycle.args["to"].to_py() == "Y"
    assert cycle.args["default"].to_py() == "N"
    assert cycle.args["using"].name == "path"


def test_ast_synthesized_output_columns():
    """WITH 与 CTE 都要暴露 SEARCH/CYCLE 合成的输出列(顺序稳定,方便
    resolver / qualify 等消费方拿来做列解析)。"""
    tree = sqlglot.parse_one(
        "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g "
        "UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\n"
        "SEARCH DEPTH FIRST BY id SET ordercol "
        "CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path "
        "SELECT * FROM sg",
        read="postgres",
    )
    with_ = tree.find(exp.With)
    # WITH 层的合成输出列: 序列列、标记列、路径列(顺序稳定)
    assert with_.synthesized_output_columns() == ["ordercol", "is_cycle", "path"]
    cte = with_.expressions[0]
    # 挂在最后一个 CTE 上
    assert cte.synthesized_output_columns() == ["ordercol", "is_cycle", "path"]

    # 只有 SEARCH 时,只暴露序列列
    tree = sqlglot.parse_one(
        "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g "
        "UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\n"
        "SEARCH DEPTH FIRST BY id SET ordercol SELECT * FROM sg",
        read="postgres",
    )
    with_ = tree.find(exp.With)
    assert with_.synthesized_output_columns() == ["ordercol"]

    # 只有 CYCLE 且没有 USING 时,只暴露标记列
    tree = sqlglot.parse_one(
        "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g "
        "UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\n"
        "CYCLE id SET is_cycle SELECT * FROM sg",
        read="postgres",
    )
    with_ = tree.find(exp.With)
    assert with_.synthesized_output_columns() == ["is_cycle"]

    # 没有 SEARCH/CYCLE 时空列表(避免误伤普通递归 CTE)
    tree = sqlglot.parse_one(
        "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g "
        "UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\n"
        "SELECT * FROM sg",
        read="postgres",
    )
    with_ = tree.find(exp.With)
    assert with_.synthesized_output_columns() == []


def test_qualify_extended_lineage():
    """未限定引用 SEARCH/CYCLE 合成列 —— qualify 必须挂回到 CTE 上。"""
    from sqlglot.optimizer.qualify import qualify

    schema = {"graph": {"id": "INT", "link": "INT"}}
    sql = (
        "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g "
        "UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\n"
        "SEARCH DEPTH FIRST BY id SET ordercol "
        "CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path "
        "SELECT ordercol, is_cycle, path FROM sg WHERE is_cycle = 'N' ORDER BY ordercol"
    )
    qualified = qualify(sqlglot.parse_one(sql, read="postgres"), schema=schema, dialect="postgres")
    text = qualified.sql("postgres")
    for fragment in (
        '"sg"."ordercol"',
        '"sg"."is_cycle"',
        '"sg"."path"',
    ):
        assert fragment in text, (fragment, text)


def test_qualify_star_expands_with_synthesized():
    """qualify 展开 SELECT * 时必须包含 SEARCH/CYCLE 合成列(顺序在
    CTE 的显式列之后,先 SEARCH 的序列列、后 CYCLE 的标记列和路径列)。"""
    from sqlglot.optimizer.qualify import qualify

    schema = {"graph": {"id": "INT", "link": "INT"}}
    sql = (
        "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g "
        "UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\n"
        "SEARCH DEPTH FIRST BY id SET ordercol "
        "CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path "
        "SELECT * FROM sg"
    )
    qualified = qualify(sqlglot.parse_one(sql, read="postgres"), schema=schema, dialect="postgres")
    outer = qualified.selects
    projected_names = [proj.alias_or_name for proj in outer]
    assert projected_names == ["id", "link", "ordercol", "is_cycle", "path"], projected_names


def test_full_optimize_does_not_break():
    """整条 optimize 流水线(qualify / annotate_types / pushdown_projections
    等)跑过带 SEARCH / CYCLE 的递归 CTE 时不能报错,子句要原样保留。"""
    from sqlglot.optimizer import optimize

    schema = {"graph": {"id": "INT", "link": "INT"}}
    sql = (
        "WITH RECURSIVE sg(id, link) AS (SELECT g.id, g.link FROM graph g "
        "UNION ALL SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)\n"
        "SEARCH DEPTH FIRST BY id SET ordercol "
        "CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path "
        "SELECT sg.ordercol, sg.is_cycle FROM sg ORDER BY sg.ordercol"
    )
    optimized = optimize(sqlglot.parse_one(sql, read="postgres"), schema=schema, dialect="postgres")
    text = optimized.sql("postgres")
    assert "SEARCH DEPTH FIRST BY" in text
    assert "CYCLE" in text
    assert "SET \"ordercol\"" in text
    assert "USING \"path\"" in text


def test_backward_compat_existing_recursive_cte():
    """打完补丁后,仓库原本已有的 SEARCH/CYCLE 用例(单排序键、单环路检测
    列、no-USING 变体等)必须保持能解析并原样输出,不能因扩展而回归。"""
    cases = [
        # Postgres 单方向 DEPTH FIRST BY 单列 SET
        (
            "WITH RECURSIVE search_tree(id, link, data) AS (SELECT t.id, t.link, t.data FROM tree t UNION ALL SELECT t.id, t.link, t.data FROM tree t, search_tree st WHERE t.id = st.link) SEARCH DEPTH FIRST BY id SET ordercol SELECT * FROM search_tree ORDER BY ordercol",
            "postgres",
            "WITH RECURSIVE search_tree(id, link, data) AS (SELECT t.id, t.link, t.data FROM tree AS t UNION ALL SELECT t.id, t.link, t.data FROM tree AS t, search_tree AS st WHERE t.id = st.link) SEARCH DEPTH FIRST BY id SET ordercol SELECT * FROM search_tree ORDER BY ordercol",
        ),
        # Postgres BREADTH FIRST
        (
            "WITH RECURSIVE search_tree(id, link, data) AS (SELECT t.id, t.link, t.data FROM tree t UNION ALL SELECT t.id, t.link, t.data FROM tree t, search_tree st WHERE t.id = st.link) SEARCH BREADTH FIRST BY id SET ordercol SELECT * FROM search_tree ORDER BY ordercol",
            "postgres",
            "WITH RECURSIVE search_tree(id, link, data) AS (SELECT t.id, t.link, t.data FROM tree AS t UNION ALL SELECT t.id, t.link, t.data FROM tree AS t, search_tree AS st WHERE t.id = st.link) SEARCH BREADTH FIRST BY id SET ordercol SELECT * FROM search_tree ORDER BY ordercol",
        ),
        # Postgres CYCLE 无 TO/DEFAULT, 有 USING (旧窄语法)
        (
            "WITH RECURSIVE search_graph(id, link, data, depth) AS (SELECT g.id, g.link, g.data, 1 FROM graph g UNION ALL SELECT g.id, g.link, g.data, sg.depth + 1 FROM graph g, search_graph sg WHERE g.id = sg.link) CYCLE id SET is_cycle USING path SELECT * FROM search_graph",
            "postgres",
            "WITH RECURSIVE search_graph(id, link, data, depth) AS (SELECT g.id, g.link, g.data, 1 FROM graph AS g UNION ALL SELECT g.id, g.link, g.data, sg.depth + 1 FROM graph AS g, search_graph AS sg WHERE g.id = sg.link) CYCLE id SET is_cycle USING path SELECT * FROM search_graph",
        ),
    ]
    for sql, dialect, expected in cases:
        assert sqlglot.parse_one(sql, read=dialect).sql(dialect) == expected, sql


def test_build_ast_programmatically():
    """允许下游代码用显式表达式构造 SEARCH/CYCLE 节点。"""
    search = exp.RecursiveWithSearch(
        kind="DEPTH",
        expressions=[
            exp.Ordered(this=exp.to_identifier("id"), desc=True, nulls_first=True),
            exp.Ordered(this=exp.to_identifier("link"), desc=False, nulls_first=True),
        ],
        expression=exp.to_identifier("ordercol"),
    )
    cycle = exp.RecursiveWithCycle(
        expressions=[exp.to_identifier("id"), exp.to_identifier("link")],
        expression=exp.to_identifier("is_cycle"),
        to=exp.Literal.string("Y"),
        default=exp.Literal.string("N"),
        using=exp.to_identifier("path"),
    )
    query = exp.select("*").from_("sg")
    query.set(
        "with_",
        exp.With(
            expressions=[
                exp.CTE(
                    this=exp.select("id", "link").from_("graph"),
                    alias=exp.TableAlias(
                        this=exp.to_identifier("sg"),
                        columns=[exp.to_identifier("id"), exp.to_identifier("link")],
                    ),
                )
            ],
            recursive=True,
            search=search,
            cycle=cycle,
        ),
    )
    generated = query.sql("postgres")
    assert generated == (
        "WITH RECURSIVE sg(id, link) AS (SELECT id, link FROM graph) "
        "SEARCH DEPTH FIRST BY id DESC, link ASC NULLS FIRST SET ordercol "
        "CYCLE id, link SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM sg"
    )
