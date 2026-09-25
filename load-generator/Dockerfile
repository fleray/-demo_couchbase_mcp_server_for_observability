# libcouchbase's apt packages (cbc-n1qlback, cbc-pillowfight, ...) are amd64-only -
# no arm64 build is published - so this image is pinned to amd64 and runs under
# emulation (Rosetta/QEMU) on Apple Silicon hosts.
FROM --platform=linux/amd64 python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates \
    && curl -fsSL https://packages.couchbase.com/clients/c/repos/deb/couchbase.key | gpg --dearmor -o /usr/share/keyrings/couchbase.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/couchbase.gpg] https://packages.couchbase.com/clients/c/repos/deb/debian12 bookworm bookworm/main" > /etc/apt/sources.list.d/couchbase.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends libcouchbase3-tools libcouchbase3-libevent \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app-new.py run-new.sh queries.json ./
COPY static ./static
RUN chmod +x run-new.sh

EXPOSE 5000

ENV CB_HOST=couchbase
ENV CB_USERNAME=Administrator
ENV CB_PASSWORD=password123
ENV CB_BUCKET=travel-sample

CMD ["./run-new.sh"]
