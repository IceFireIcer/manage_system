# 后端运行时镜像：Python 3.12 + FastAPI/uvicorn + Alembic
# 构建示例（国内网络默认已走阿里云镜像，可覆盖）：
#   docker build -t manage-system-backend:latest ./backend
#   docker build --build-arg PIP_INDEX_URL=https://pypi.org/simple --build-arg PIP_TRUSTED_HOST=pypi.org -t manage-system-backend:latest ./backend
FROM python:3.12-slim

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 时区数据（日志时间显示为东八区）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir \
        --index-url "${PIP_INDEX_URL}" \
        --trusted-host "${PIP_TRUSTED_HOST}" \
        -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

# 非 root 运行
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).status == 200 else 1)"]

# 单进程启动：先执行幂等迁移与演示数据（seed_demo 幂等，重复启动不重复写入），再启动 uvicorn
CMD ["sh", "-c", "alembic upgrade head && python scripts/seed_demo.py && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]