# Supply a reviewed Python base image pinned by repository digest.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY server.py /opt/ctfbot-fixture/server.py
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/python3", "/opt/ctfbot-fixture/server.py"]
