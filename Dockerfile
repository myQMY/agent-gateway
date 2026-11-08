# 可选容器复现：不是复现前提（docs/design-w3.md §1）。
# 主复现路径是裸 Python：python -m unittest discover -s tests -q
# 本镜像只装 requirements.txt 已有的 3 个包，不引入任何新依赖。
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway ./gateway
COPY bridge ./bridge
COPY tests ./tests
COPY experiments ./experiments
COPY docs ./docs
COPY README.md LICENSE ./

EXPOSE 8000

CMD ["uvicorn", "gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
