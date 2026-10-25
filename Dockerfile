# CI image (base build and runtime environment)
FROM docker.io/ubuntu:24.04 AS ci
ARG DEBIAN_FRONTEND=noninteractive

# Python, Icarus Verilog and a local PostgreSQL 16 server: everything
# `sqrtl check` needs.
RUN apt update && apt upgrade -y && \
    apt install -y --no-install-recommends \
    git curl wget ca-certificates zip unzip sudo dumb-init \
    python3 python3-pip python3-venv \
    iverilog postgresql-16 postgresql-client-16 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# System-wide pip is fine inside the container.
ENV PIP_BREAK_SYSTEM_PACKAGES=1
RUN pip install --no-cache-dir "sqlglot>=25" "psycopg[binary]>=3.1" "pytest>=8"

# Create dev sudo user (drop the stock `ubuntu` user so dev gets uid 1000)
RUN userdel -r ubuntu 2>/dev/null || true && \
    useradd --create-home --shell /bin/bash --uid 1000 dev && \
    usermod --append --groups sudo dev && \
    echo '%sudo ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

# Local PostgreSQL: trust connections from inside the container and create
# the `sqrtl` database owned by dev, so the default DSN
# postgresql://localhost/sqrtl works out of the box.
RUN PG_HBA=/etc/postgresql/16/main/pg_hba.conf && \
    sed -i -E 's/^(local\s+all\s+all\s+)peer$/\1trust/; s/^(host\s+all\s+all\s+(127\.0\.0\.1\/32|::1\/128)\s+)scram-sha-256$/\1trust/' "$PG_HBA" && \
    service postgresql start && \
    su postgres -c "createuser --superuser dev" && \
    su postgres -c "createdb --owner dev sqrtl" && \
    service postgresql stop

ENTRYPOINT ["/usr/bin/dumb-init", "--"]

# Devcontainer image (adds developer + agent conveniences)
FROM ci AS dev

USER root
ARG DEBIAN_FRONTEND=noninteractive

# Install dev-specific tools
RUN apt update && \
    apt install -y --no-install-recommends \
    man make zsh vim procps gnupg gnupg2 jq less && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Pre-install GitHub CLI (gh)
ARG GH_CLI_VERSION=2.63.2
ARG GH_TARBALL_HASH_X64="912fdb1ca29cb005fb746fc5d2b787a289078923a29d0f9ec19a0b00272ded00  gh_${GH_CLI_VERSION}_linux_amd64.tar.gz"
ARG GH_TARBALL_HASH_ARM64="0f31e2a8549c64b5c1679f0b99ce5e0dac7c91da9e86f6246adb8805b0f0b4bb  gh_${GH_CLI_VERSION}_linux_arm64.tar.gz"
RUN ARCH="$(uname -m)"; \
    case "$ARCH" in \
      x86_64) GH_ARCH="amd64"; GH_HASH="${GH_TARBALL_HASH_X64}" ;; \
      aarch64|arm64) GH_ARCH="arm64"; GH_HASH="${GH_TARBALL_HASH_ARM64}" ;; \
      *) echo "Unsupported architecture for gh: $ARCH" >&2; exit 1 ;; \
    esac; \
    GH_DIR="gh_${GH_CLI_VERSION}_linux_${GH_ARCH}"; \
    cd /tmp && \
    wget "https://github.com/cli/cli/releases/download/v${GH_CLI_VERSION}/${GH_DIR}.tar.gz" && \
    echo "${GH_HASH}" | sha256sum -c && \
    tar -xzf "${GH_DIR}.tar.gz" && \
    install -m 0755 "${GH_DIR}/bin/gh" /usr/local/bin/gh && \
    rm -rf "${GH_DIR}.tar.gz" "${GH_DIR}"

# Prepare temp and workspace directories with wide permissions for GHA runner
RUN mkdir -p /__w /github/workspace /tmp && \
    chmod -R 777 /__w /github /tmp 2>/dev/null || true

# `pip install -e .` as dev lands console scripts in ~/.local/bin
ENV PATH="/home/dev/.local/bin:${PATH}"

# Setup oh-my-zsh for dev user
USER dev
ARG DOCKER_OHMYZSH_SCRIPT_NAME=zsh-in-docker.sh
ARG DOCKER_OHMYZSH_SCRIPT_URL=https://github.com/deluan/zsh-in-docker/releases/download/v1.1.3/${DOCKER_OHMYZSH_SCRIPT_NAME}
ARG DOCKER_OHMYZSH_SCRIPT_HASH="ffa8175332ef01b500ace59d03ce7e2f3a7453651e9a37060974bb6536f0706b  ${DOCKER_OHMYZSH_SCRIPT_NAME}"
RUN cd /tmp && \
    wget ${DOCKER_OHMYZSH_SCRIPT_URL} && \
    echo ${DOCKER_OHMYZSH_SCRIPT_HASH} | sha256sum -c && \
    chmod +x ./${DOCKER_OHMYZSH_SCRIPT_NAME} && \
    ./${DOCKER_OHMYZSH_SCRIPT_NAME} -t robbyrussell -p git -p ssh-agent && \
    sudo rm ./${DOCKER_OHMYZSH_SCRIPT_NAME}

USER dev
