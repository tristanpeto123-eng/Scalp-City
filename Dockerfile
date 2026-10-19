FROM python:3.12-slim
WORKDIR /srv/project-os
COPY brain ./brain
COPY council ./council
COPY archivist ./archivist
COPY host ./host
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BRAIN_COUNCIL_ROOT=/state/council BRAIN_REGISTERS_ROOT=/state/registers BRAIN_ARCHIVIST_DB=/state/archivist.sqlite3 BRAIN_LEDGER=/state/brain/scans.sqlite3
RUN mkdir -p /state /watchdog-state
EXPOSE 8080
CMD ["python","-m","host.combined"]
