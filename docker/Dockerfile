FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

EXPOSE 631

ENV OPP_NAME="OpenPrint Docker"
ENV OPP_HOST="0.0.0.0"
ENV OPP_PORT="631"

CMD ["openprint-server"]
