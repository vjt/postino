FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md /app/
COPY src/ /app/src/

RUN pip install --no-cache-dir '.[daemon]'

EXPOSE 8443
ENV POSTINO_CONFIG=/etc/postino/postino.toml
CMD ["python", "-m", "postinod"]
