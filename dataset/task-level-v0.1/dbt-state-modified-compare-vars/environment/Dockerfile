FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch=2.7.6-7 \
    && rm -rf /var/lib/apt/lists/*

# Runtime + test dependencies, fully pinned. dbt-core itself is installed
# editable from /app so that changes made to the repository take effect.
RUN pip install --no-cache-dir \
    agate==1.9.1 \
    annotated-types==0.8.0 \
    attrs==26.1.0 \
    babel==2.18.0 \
    certifi==2026.7.22 \
    charset-normalizer==3.5.1 \
    click==8.4.2 \
    colorama==0.4.6 \
    daff==1.4.2 \
    dbt-adapters==1.24.5 \
    dbt-common==1.39.0 \
    dbt-core-experimental-parser==2.0.0b2 \
    dbt-duckdb==1.11.0 \
    dbt-extractor==0.6.0 \
    dbt-protos==1.0.565 \
    deepdiff==8.6.2 \
    duckdb==1.5.5 \
    idna==3.19 \
    importlib_metadata==9.0.0 \
    iniconfig==2.3.0 \
    isodate==0.7.2 \
    Jinja2==3.1.6 \
    jsonschema==4.26.0 \
    jsonschema-specifications==2025.9.1 \
    leather==0.4.1 \
    MarkupSafe==3.0.3 \
    mashumaro==3.17 \
    metricflow==0.212.0 \
    more-itertools==10.8.0 \
    msgpack==1.2.1 \
    networkx==3.6.1 \
    opentelemetry-api==1.44.0 \
    orderly-set==5.5.0 \
    packaging==26.3 \
    parsedatetime==2.6 \
    pathspec==1.0.4 \
    pluggy==1.6.0 \
    protobuf==6.33.6 \
    pydantic==2.13.4 \
    pydantic_core==2.46.4 \
    Pygments==2.21.0 \
    pytest==9.1.1 \
    python-dateutil==2.9.0.post0 \
    python-dotenv==1.2.3 \
    python-slugify==8.0.4 \
    pytimeparse==1.1.8 \
    pytz==2026.3.post1 \
    PyYAML==6.0.3 \
    RapidFuzz==3.14.5 \
    referencing==0.37.0 \
    requests==2.34.2 \
    rpds-py==2026.6.3 \
    six==1.17.0 \
    snowplow-tracker==1.1.0 \
    sqlglot==30.17.0 \
    sqlparse==0.6.0 \
    tabulate==0.10.0 \
    text-unidecode==1.3 \
    typing_extensions==4.16.0 \
    typing-inspection==0.4.4 \
    urllib3==2.7.0 \
    zipp==4.1.0 \
    hatchling==1.27.0

COPY repository/ /app/
WORKDIR /app

# Install dbt-core editable (no deps, they are pinned above) so the dbt
# entry point and imports resolve to /app/core.
RUN pip install --no-cache-dir --no-deps -e ./core \
    && dbt --version \
    && python -c "import dbt; assert dbt.__file__.startswith('/app/')"
