# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.14.2
ARG DISABLE_GIL=0

FROM ubuntu:24.04 AS builder
ARG PYTHON_VERSION
ARG DISABLE_GIL

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /tmp

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        libbz2-dev \
        libffi-dev \
        libgdbm-dev \
        liblzma-dev \
        libncursesw5-dev \
        libreadline-dev \
        libsqlite3-dev \
        libssl-dev \
        tk-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    curl -fsSLO "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz"; \
    tar -xzf "Python-${PYTHON_VERSION}.tgz"; \
    cd "Python-${PYTHON_VERSION}"; \
    CFG="--prefix=/usr/local --with-ensurepip=install"; \
    if [ "${DISABLE_GIL}" = "1" ]; then CFG="$CFG --disable-gil"; fi; \
    ./configure $CFG; \
    make -j"$(nproc)"; \
    make install

FROM ubuntu:24.04
ARG PYTHON_VERSION
ARG DISABLE_GIL

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        libbz2-1.0 \
        libffi8 \
        libgdbm6 \
        liblzma5 \
        libncursesw6 \
        libreadline8 \
        libsqlite3-0 \
        libssl3 \
        tk \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local /usr/local
COPY app/requirements.txt /tmp/requirements.txt
RUN /usr/local/bin/python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && /usr/local/bin/python3 -m pip install --no-cache-dir -r /tmp/requirements.txt

COPY app/ /app/

CMD ["bash", "-lc", "python3 -V && sleep infinity"]
