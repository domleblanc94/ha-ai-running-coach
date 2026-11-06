FROM python:3.13-slim-bookworm@sha256:281d39ff02e3b389401586134ff9e6572954687822a58055ec07a60ded4e0b7f

ARG BUILD_ARCH
ARG BUILD_VERSION
LABEL io.hass.type="app" \
      io.hass.name="AI Running Coach" \
      io.hass.arch="${BUILD_ARCH}" \
      io.hass.version="${BUILD_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/data/app

RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends gosu tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 coach \
    && useradd --uid 10001 --gid 10001 --home-dir /data/app --no-create-home --shell /usr/sbin/nologin coach

WORKDIR /opt/ai-running-coach
COPY core/requirements.txt /tmp/core-requirements.txt
COPY app/requirements.txt /tmp/app-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/core-requirements.txt -r /tmp/app-requirements.txt \
    && rm /tmp/core-requirements.txt /tmp/app-requirements.txt
COPY core/ ./core/
COPY core-pin.json ./
COPY app/ ./app/
COPY --chmod=0755 run.sh /usr/local/bin/ai-running-coach

EXPOSE 8099
CMD ["/usr/local/bin/ai-running-coach"]
