# SPDX-License-Identifier: GPL-3.0-or-later
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TOWERSERVICES_REPO=/app/repo \
    TOWERSERVICES_STATUS_DIR=/var/run/towerservices

# openssh-client for AAP/Tower SSH; git optional for in-place repo updates.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client sshpass git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY bridge/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Ansible collections used by the playbooks.
COPY requirements.yml /app/repo/requirements.yml
RUN ansible-galaxy collection install -r /app/repo/requirements.yml

# Bridge code + the playbook repo.
COPY bridge/ /app/bridge/
COPY . /app/repo/

RUN mkdir -p /var/run/towerservices
EXPOSE 8080
CMD ["uvicorn", "bridge.app:app", "--host", "0.0.0.0", "--port", "8080"]
