FROM python:3.13.15-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin slopbench \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /baseline
COPY baseline/ /baseline/

RUN git init \
    && git config user.name slopbench \
    && git config user.email slopbench@invalid \
    && git add . \
    && GIT_AUTHOR_DATE=2026-01-01T00:00:00Z \
       GIT_COMMITTER_DATE=2026-01-01T00:00:00Z \
       git commit -m "fixture: event summary tracer"

WORKDIR /app
COPY test.sh verify.py revision.py check_*.py /tests/
