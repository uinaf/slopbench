FROM python:3.13.15-bookworm@sha256:62eafe52c91cad83c2c74e630bfde917da8c253673e695665d454def84fc9a13

WORKDIR /app
COPY repo/ /app/

RUN git init \
    && git config user.name slopbench \
    && git config user.email slopbench@invalid \
    && git add . \
    && GIT_AUTHOR_DATE=2026-01-01T00:00:00Z \
       GIT_COMMITTER_DATE=2026-01-01T00:00:00Z \
       git commit -m "fixture: event pagination"
